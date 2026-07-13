#!/usr/bin/env python3
"""Regression tests for package foundation tooling (batch-A amends)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
SET_VERSION = SCRIPTS / "set-version.py"
STAGE = SCRIPTS / "stage-codex-dev-install.py"
VALIDATE = SCRIPTS / "validate-release.py"
RUN_PACKAGE_TESTS = SCRIPTS / "run-package-tests.py"

# Canonical package suite entrypoint (bytecode disabled, cache-clean).
CANONICAL_PACKAGE_TEST_COMMAND = (
    f"{sys.executable} -B scripts/run-package-tests.py"
)


def load_script(module_name: str, path: Path) -> types.ModuleType:
    # Avoid writing bytecode when tests import hyphenated scripts.
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


set_version_mod = load_script("pr_completion_set_version", SET_VERSION)
stage_mod = load_script("pr_completion_stage_codex", STAGE)


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    """Byte snapshot of every non-directory path under root (follows no policy)."""
    snapshot: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink() or path.is_file():
            # For symlinks, record link target as payload for stability checks.
            if path.is_symlink():
                snapshot[rel] = b"symlink:" + os.fsencode(os.readlink(path))
            else:
                snapshot[rel] = path.read_bytes()
    return snapshot


class SetVersionTransactionalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="set-version-")
        self.root = Path(self.temporary.name)
        self._seed_package(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_package(self, root: Path) -> None:
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "skills" / "take-pr-to-completion").mkdir(parents=True)
        (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
        for relative, payload in (
            (
                ".claude-plugin/plugin.json",
                {"name": "pr-completion", "version": "0.1.0", "skills": "./skills/"},
            ),
            (
                ".codex-plugin/plugin.json",
                {"name": "pr-completion", "version": "0.1.0", "skills": "./skills/"},
            ),
            (
                ".claude-plugin/marketplace.json",
                {
                    "name": "pr-completion",
                    "plugins": [
                        {
                            "name": "pr-completion",
                            "version": "0.1.0",
                            "source": "./",
                        }
                    ],
                },
            ),
        ):
            path = root / relative
            path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _snapshot(self) -> dict[str, bytes]:
        return {
            "VERSION": _read(self.root / "VERSION"),
            "claude": _read(self.root / ".claude-plugin" / "plugin.json"),
            "codex": _read(self.root / ".codex-plugin" / "plugin.json"),
            "market": _read(self.root / ".claude-plugin" / "marketplace.json"),
        }

    def test_successful_set_updates_all_targets(self) -> None:
        summaries = set_version_mod.set_version(self.root, "1.2.3")
        self.assertTrue(any("1.2.3" in line for line in summaries))
        self.assertEqual((self.root / "VERSION").read_text(encoding="utf-8").strip(), "1.2.3")
        self.assertEqual(_json(self.root / ".claude-plugin" / "plugin.json")["version"], "1.2.3")
        self.assertEqual(_json(self.root / ".codex-plugin" / "plugin.json")["version"], "1.2.3")
        market = _json(self.root / ".claude-plugin" / "marketplace.json")
        self.assertEqual(market["plugins"][0]["version"], "1.2.3")

    def test_invalid_version_leaves_all_files_unchanged(self) -> None:
        before = self._snapshot()
        with self.assertRaises(set_version_mod.SetVersionError):
            set_version_mod.set_version(self.root, "not-a-version")
        self.assertEqual(self._snapshot(), before)

    def test_build_metadata_rejected_without_mutation(self) -> None:
        before = self._snapshot()
        with self.assertRaises(set_version_mod.SetVersionError):
            set_version_mod.set_version(self.root, "0.1.0+codex.bad")
        self.assertEqual(self._snapshot(), before)

    def test_missing_marketplace_plugin_entry_leaves_files_unchanged(self) -> None:
        market = self.root / ".claude-plugin" / "marketplace.json"
        market.write_text(
            json.dumps(
                {
                    "name": "pr-completion",
                    "plugins": [{"name": "other", "version": "0.1.0", "source": "./"}],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        before = self._snapshot()
        with self.assertRaises(set_version_mod.SetVersionError):
            set_version_mod.set_version(self.root, "2.0.0")
        self.assertEqual(self._snapshot(), before)

    def test_cli_failed_update_exit_nonzero_and_no_change(self) -> None:
        before = self._snapshot()
        completed = subprocess.run(
            [sys.executable, "-B", str(SET_VERSION), "0.1.0+codex.nope", "--root", str(self.root)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(self._snapshot(), before)


class StagingOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="stage-own-")
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self._seed_min_plugin(self.source)
        self.source_snapshot = _tree_snapshot(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_min_plugin(self, root: Path) -> None:
        (root / ".codex-plugin").mkdir(parents=True)
        (root / "skills" / "take-pr-to-completion").mkdir(parents=True)
        (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
        (root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "pr-completion",
                    "version": "0.1.0",
                    "description": "test",
                    "skills": "./skills/",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "skills" / "take-pr-to-completion" / "SKILL.md").write_text(
            "---\nname: take-pr-to-completion\ndescription: test\n---\n# Test\n",
            encoding="utf-8",
        )

    def _assert_source_untouched(self) -> None:
        self.assertEqual(_tree_snapshot(self.source), self.source_snapshot)

    def test_reject_staging_equal_to_source(self) -> None:
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, self.source)
        self._assert_source_untouched()

    def test_reject_staging_inside_source(self) -> None:
        nested = self.source / "inside-staging"
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, nested)
        self._assert_source_untouched()

    def test_reject_staging_ancestor_of_source(self) -> None:
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, self.base)
        self._assert_source_untouched()

    def test_reject_existing_unowned_directory_without_delete(self) -> None:
        keep = self.base / "unowned"
        keep.mkdir()
        precious = keep / "do-not-delete.txt"
        precious.write_text("keep me\n", encoding="utf-8")
        before = _tree_snapshot(keep)
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(_tree_snapshot(keep), before)
        self._assert_source_untouched()

    def test_reject_wrong_marker_contents_without_delete_or_source_mutation(self) -> None:
        keep = self.base / "spoofed-marker"
        keep.mkdir()
        precious = keep / "unrelated.txt"
        precious.write_text("must survive\n", encoding="utf-8")
        marker = keep / stage_mod.STAGING_MARKER_NAME
        marker.write_text("owned-by: not-this-script\n", encoding="utf-8")
        before = _tree_snapshot(keep)
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(_tree_snapshot(keep), before)
        self.assertEqual(precious.read_text(encoding="utf-8"), "must survive\n")
        self.assertEqual(
            marker.read_text(encoding="utf-8"),
            "owned-by: not-this-script\n",
        )
        self._assert_source_untouched()

    def test_reject_symlink_marker_without_delete_or_source_mutation(self) -> None:
        keep = self.base / "symlink-marker"
        keep.mkdir()
        precious = keep / "unrelated.txt"
        precious.write_text("must survive symlink case\n", encoding="utf-8")
        # Even if the symlink target holds the exact owned contents, ownership
        # must not authenticate through a symlink marker.
        target = keep / "marker-target.txt"
        target.write_text(stage_mod.STAGING_MARKER_CONTENTS, encoding="utf-8")
        marker = keep / stage_mod.STAGING_MARKER_NAME
        marker.symlink_to(target.name)
        self.assertTrue(marker.is_symlink())
        before = _tree_snapshot(keep)
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(_tree_snapshot(keep), before)
        self.assertTrue(marker.is_symlink())
        self.assertTrue(precious.is_file())
        self.assertEqual(
            precious.read_text(encoding="utf-8"),
            "must survive symlink case\n",
        )
        self._assert_source_untouched()

    def test_reject_symlink_marker_to_outside_payload(self) -> None:
        keep = self.base / "symlink-outside"
        keep.mkdir()
        outside = self.base / "outside-marker.txt"
        outside.write_text(stage_mod.STAGING_MARKER_CONTENTS, encoding="utf-8")
        marker = keep / stage_mod.STAGING_MARKER_NAME
        marker.symlink_to(outside)
        keep_file = keep / "payload.bin"
        keep_file.write_bytes(b"\x00\x01payload")
        before_keep = _tree_snapshot(keep)
        before_outside = outside.read_bytes()
        with self.assertRaises(stage_mod.StagingError):
            stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(_tree_snapshot(keep), before_keep)
        self.assertEqual(outside.read_bytes(), before_outside)
        self._assert_source_untouched()

    def test_allow_empty_directory_and_mark_owned(self) -> None:
        keep = self.base / "empty"
        keep.mkdir()
        prepared = stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(prepared, keep.resolve())
        marker = keep / stage_mod.STAGING_MARKER_NAME
        st = os.lstat(marker)
        self.assertFalse(stat.S_ISLNK(st.st_mode))
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self.assertTrue(stage_mod.is_authentic_staging_marker(marker))
        self._assert_source_untouched()

    def test_allow_reuse_of_script_owned_directory(self) -> None:
        keep = self.base / "owned"
        keep.mkdir()
        marker = keep / stage_mod.STAGING_MARKER_NAME
        marker.write_bytes(stage_mod.STAGING_MARKER_BYTES)
        stale = keep / "stale-plugins"
        stale.mkdir()
        (stale / "old.txt").write_text("old\n", encoding="utf-8")
        prepared = stage_mod.prepare_staging_root(self.source, keep)
        self.assertEqual(prepared, keep.resolve())
        self.assertFalse(stale.exists())
        self.assertTrue(stage_mod.is_authentic_staging_marker(marker))
        st = os.lstat(marker)
        self.assertFalse(stat.S_ISLNK(st.st_mode))
        self.assertTrue(stat.S_ISREG(st.st_mode))
        self._assert_source_untouched()

    def test_stage_writes_supported_marketplace_layout(self) -> None:
        keep = self.base / "stage-layout"
        marketplace_root, marketplace_path, plugin_dest, staged_version = stage_mod.stage_plugin(
            self.source,
            "test-local",
            keep,
        )
        self.assertEqual(
            marketplace_path,
            marketplace_root / ".agents" / "plugins" / "marketplace.json",
        )
        self.assertTrue(marketplace_path.is_file())
        self.assertTrue((plugin_dest / ".codex-plugin" / "plugin.json").is_file())
        self.assertEqual(staged_version, "0.1.0+codex.test-local")
        self.assertEqual(
            _json(self.source / ".codex-plugin" / "plugin.json")["version"],
            "0.1.0",
        )
        market = _json(marketplace_path)
        self.assertEqual(market["name"], "pr-completion-dev")
        self.assertEqual(
            market["plugins"][0]["source"]["path"],
            "./plugins/pr-completion",
        )
        self._assert_source_untouched()


@unittest.skipUnless(shutil.which("codex"), "codex CLI not available")
class StagingInstallIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="stage-install-")
        self.base = Path(self.temporary.name)
        self.codex_home = self.base / "codex-home"
        self.keep = self.base / "stage"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_isolated_install_uses_cachebusted_staging_not_source(self) -> None:
        source_before = _json(ROOT / ".codex-plugin" / "plugin.json")["version"]
        self.assertEqual(source_before, "0.1.0")
        source_bytes_before = _read(ROOT / ".codex-plugin" / "plugin.json")

        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(STAGE),
                "--root",
                str(ROOT),
                "--cachebuster",
                "e2e-isolated",
                "--keep-dir",
                str(self.keep),
                "--install",
                "--codex-home",
                str(self.codex_home),
                "--print-json",
            ],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["sourceVersion"], "0.1.0")
        self.assertEqual(payload["stagedVersion"], "0.1.0+codex.e2e-isolated")
        self.assertTrue(payload["sourceUnchanged"])
        self.assertTrue(payload["installed"])
        self.assertTrue(payload["install"]["isolated"])

        # Source byte-for-byte unchanged.
        self.assertEqual(_read(ROOT / ".codex-plugin" / "plugin.json"), source_bytes_before)

        staged_manifest = (
            Path(payload["stagedPluginRoot"]) / ".codex-plugin" / "plugin.json"
        )
        self.assertEqual(_json(staged_manifest)["version"], "0.1.0+codex.e2e-isolated")

        marketplace_manifest = Path(payload["marketplaceManifest"])
        self.assertEqual(
            marketplace_manifest,
            Path(payload["marketplaceRoot"])
            / ".agents"
            / "plugins"
            / "marketplace.json",
        )
        self.assertTrue(marketplace_manifest.is_file())

        install_json = None
        for command in payload["install"]["commands"]:
            if command.get("json"):
                install_json = command["json"]
        self.assertIsNotNone(install_json)
        assert install_json is not None
        self.assertEqual(install_json["version"], "0.1.0+codex.e2e-isolated")
        installed_path = Path(install_json["installedPath"])
        self.assertTrue(installed_path.is_dir())
        # Isolated install must land under the disposable codex home.
        self.assertTrue(
            str(installed_path).startswith(str(self.codex_home.resolve())),
            msg=installed_path,
        )
        installed_version = _json(
            installed_path / ".codex-plugin" / "plugin.json"
        )["version"]
        self.assertEqual(installed_version, "0.1.0+codex.e2e-isolated")


class LivePackageValidators(unittest.TestCase):
    def test_validate_release_passes_on_actual_tree(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(VALIDATE), "--root", str(ROOT)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_canonical_command_constant_matches_runner(self) -> None:
        self.assertTrue(RUN_PACKAGE_TESTS.is_file())
        self.assertIn("run-package-tests.py", CANONICAL_PACKAGE_TEST_COMMAND)
        self.assertIn("-B", CANONICAL_PACKAGE_TEST_COMMAND)

    def test_stage_only_leaves_source_plain_semver(self) -> None:
        """Staging without install leaves source plain SemVer."""
        source_before = _read(ROOT / ".codex-plugin" / "plugin.json")
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(STAGE),
                "--root",
                str(ROOT),
                "--cachebuster",
                "no-install",
                "--print-json",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["sourceVersion"], "0.1.0")
        self.assertEqual(payload["stagedVersion"], "0.1.0+codex.no-install")
        self.assertFalse(payload["installed"])
        self.assertEqual(_read(ROOT / ".codex-plugin" / "plugin.json"), source_before)
        # Cleanup temp marketplace created without --keep-dir.
        shutil.rmtree(payload["marketplaceRoot"], ignore_errors=True)


class CanonicalSuiteMetaTests(unittest.TestCase):
    def test_run_package_tests_module_is_executable_entrypoint(self) -> None:
        text = RUN_PACKAGE_TESTS.read_text(encoding="utf-8")
        self.assertIn("PYTHONDONTWRITEBYTECODE", text)
        self.assertIn("unittest", text)
        self.assertIn("validate-release.py", text)
        self.assertIn("__pycache__", text)


if __name__ == "__main__":
    # Prefer: python3 -B scripts/run-package-tests.py
    unittest.main()
