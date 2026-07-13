#!/usr/bin/env python3
"""Injected contamination, version-drift, and required-surface deletion gates."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
VALIDATE = SCRIPTS / "validate-release.py"
SAFETY = SCRIPTS / "check-merge-ready-safety.py"


def load_validate_module() -> types.ModuleType:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location(
        "pr_completion_validate_release", VALIDATE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {VALIDATE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validate_mod = load_validate_module()


def _run_validate(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(VALIDATE), "--root", str(root)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _seed_complete_package(root: Path, version: str = "0.1.0") -> None:
    """Copy every surface validate-release requires."""
    ignore = shutil.ignore_patterns(
        ".git",
        "__pycache__",
        "*.pyc",
        ".venv",
        "venv",
        "node_modules",
        ".codex-staging",
        ".cachebust",
        "_site",
    )
    for name in (
        "VERSION",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
        "README.md",
        "SECURITY.md",
        ".claude-plugin",
        ".codex-plugin",
        "skills",
        "scripts",
        "docs",
        "submission",
        ".github",
    ):
        source = ROOT / name
        dest = root / name
        if not source.exists():
            raise RuntimeError(f"seed source missing: {source}")
        if source.is_dir():
            shutil.copytree(source, dest, ignore=ignore)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    (root / "VERSION").write_text(f"{version}\n", encoding="utf-8")


class ContaminationRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="contam-")
        self.root = Path(self.temporary.name) / "pkg"
        self.root.mkdir()
        _seed_complete_package(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_clean_seed_passes_validate_release(self) -> None:
        completed = _run_validate(self.root)
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)

    def test_version_drift_is_rejected(self) -> None:
        codex = self.root / ".codex-plugin" / "plugin.json"
        payload = json.loads(codex.read_text(encoding="utf-8"))
        payload["version"] = "9.9.9"
        codex.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not match VERSION", completed.stderr)

    def test_marketplace_version_drift_is_rejected(self) -> None:
        market = self.root / ".claude-plugin" / "marketplace.json"
        payload = json.loads(market.read_text(encoding="utf-8"))
        payload["plugins"][0]["version"] = "0.0.0-drift"
        market.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("does not match VERSION", completed.stderr)

    def test_cachebuster_metadata_is_rejected(self) -> None:
        codex = self.root / ".codex-plugin" / "plugin.json"
        payload = json.loads(codex.read_text(encoding="utf-8"))
        payload["version"] = "0.1.0+codex.ci-bad"
        codex.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(
            "cachebuster" in completed.stderr.lower()
            or "+codex." in completed.stderr
            or "does not match VERSION" in completed.stderr,
            msg=completed.stderr,
        )

    def test_absolute_personal_path_is_rejected(self) -> None:
        personal_marker = "/" + "Users/" + "someone/secret-path"
        target = self.root / "skills" / "take-pr-to-completion" / "SKILL.md"
        text = target.read_text(encoding="utf-8")
        target.write_text(
            text + f"\n<!-- contamination: {personal_marker} -->\n",
            encoding="utf-8",
        )
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("absolute personal path", completed.stderr)

    def test_python_cache_is_rejected(self) -> None:
        cache = (
            self.root
            / "skills"
            / "take-pr-to-completion"
            / "scripts"
            / "__pycache__"
        )
        cache.mkdir(parents=True)
        (cache / "pr_watch.cpython-314.pyc").write_bytes(b"\0\0contaminated")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(
            "cache" in completed.stderr.lower() or ".pyc" in completed.stderr,
            msg=completed.stderr,
        )

    def test_secret_marker_is_rejected(self) -> None:
        secret_marker = "gh" + "p_" + "contaminatedtokenvalue"
        target = self.root / "CHANGELOG.md"
        target.write_text(
            target.read_text(encoding="utf-8") + f"\n{secret_marker}\n",
            encoding="utf-8",
        )
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("secret marker", completed.stderr)

    def test_missing_skill_is_rejected(self) -> None:
        shutil.rmtree(self.root / "skills" / "merge-conflict-resolution")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("missing", completed.stderr.lower())


class RequiredSurfaceDeletionTests(unittest.TestCase):
    """Deleting any ticket 3/4 required group must fail validate-release."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="req-del-")
        self.root = Path(self.temporary.name) / "pkg"
        self.root.mkdir()
        _seed_complete_package(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _delete_relative(self, relative: str) -> None:
        path = self.root / relative
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()

    def test_required_file_groups_cover_ticket_3_through_6_surfaces(self) -> None:
        groups = validate_mod.REQUIRED_FILE_GROUPS
        self.assertIn("ticket-3-ci-runners", groups)
        self.assertIn("ticket-4-public-docs", groups)
        self.assertIn("ticket-5-release", groups)
        self.assertIn("ticket-6-openai-submission", groups)
        self.assertIn("verification-test-modules", groups)
        self.assertIn("watcher-install-fixtures", groups)
        self.assertIn("docs-assets", groups)
        joined = " ".join(
            relative for group in groups.values() for relative in group
        )
        for needle in (
            "README.md",
            "SECURITY.md",
            ".github/workflows/ci.yml",
            ".github/workflows/pages.yml",
            ".github/workflows/release.yml",
            "scripts/package-release.py",
            "scripts/package-openai-submission.py",
            "scripts/install-smoke.py",
            "scripts/run-ci-validation.py",
            "scripts/run-watcher-tests.py",
            "scripts/build-docs.py",
            "scripts/check-docs-links.py",
            "docs/site.json",
            "docs/index.md",
            "docs/assets/site.css",
            "skills/take-pr-to-completion/tests/test_pr_watch.py",
            "scripts/tests/test_package_tooling.py",
            "scripts/tests/test_contamination.py",
            "scripts/tests/test_install_smoke.py",
            "scripts/tests/test_openai_submission.py",
            "submission/openai/listing.json",
            "submission/openai/test-cases.json",
            "skills/take-pr-to-completion/tests/fixtures/ready-to-merge.json",
            "skills/take-pr-to-completion/tests/fixtures/blocked.json",
            "skills/take-pr-to-completion/tests/fixtures/repository-layout.json",
        ):
            self.assertIn(needle, joined)

    def test_deleting_each_required_group_fails_validate_release(self) -> None:
        for group_name, relatives in validate_mod.REQUIRED_FILE_GROUPS.items():
            with self.subTest(group=group_name):
                temporary = tempfile.TemporaryDirectory(prefix=f"del-{group_name}-")
                try:
                    package = Path(temporary.name) / "pkg"
                    package.mkdir()
                    _seed_complete_package(package)
                    for relative in relatives:
                        path = package / relative
                        if path.is_dir():
                            shutil.rmtree(path)
                        elif path.exists():
                            path.unlink()
                    completed = _run_validate(package)
                    self.assertNotEqual(
                        completed.returncode,
                        0,
                        msg=f"group {group_name} deletion should fail: {completed.stderr}",
                    )
                    self.assertIn("missing required file", completed.stderr)
                finally:
                    temporary.cleanup()

    def test_deleting_readme_alone_fails(self) -> None:
        self._delete_relative("README.md")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("README.md", completed.stderr)

    def test_deleting_security_alone_fails(self) -> None:
        self._delete_relative("SECURITY.md")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("SECURITY.md", completed.stderr)

    def test_deleting_ci_workflow_alone_fails(self) -> None:
        self._delete_relative(".github/workflows/ci.yml")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ci.yml", completed.stderr)

    def test_deleting_pages_workflow_alone_fails(self) -> None:
        self._delete_relative(".github/workflows/pages.yml")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("pages.yml", completed.stderr)

    def test_deleting_docs_site_json_alone_fails(self) -> None:
        self._delete_relative("docs/site.json")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("docs/site.json", completed.stderr)

    def test_deleting_install_smoke_runner_alone_fails(self) -> None:
        self._delete_relative("scripts/install-smoke.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("install-smoke.py", completed.stderr)

    def test_deleting_ci_validation_runner_alone_fails(self) -> None:
        self._delete_relative("scripts/run-ci-validation.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("run-ci-validation.py", completed.stderr)

    def test_deleting_docs_builder_alone_fails(self) -> None:
        self._delete_relative("scripts/build-docs.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("build-docs.py", completed.stderr)

    def test_deleting_docs_link_checker_alone_fails(self) -> None:
        self._delete_relative("scripts/check-docs-links.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("check-docs-links.py", completed.stderr)

    def test_deleting_watcher_test_module_alone_fails(self) -> None:
        self._delete_relative("skills/take-pr-to-completion/tests/test_pr_watch.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("test_pr_watch.py", completed.stderr)

    def test_deleting_package_tooling_tests_alone_fails(self) -> None:
        self._delete_relative("scripts/tests/test_package_tooling.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("test_package_tooling.py", completed.stderr)

    def test_deleting_contamination_tests_alone_fails(self) -> None:
        self._delete_relative("scripts/tests/test_contamination.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("test_contamination.py", completed.stderr)

    def test_deleting_install_smoke_tests_alone_fails(self) -> None:
        self._delete_relative("scripts/tests/test_install_smoke.py")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("test_install_smoke.py", completed.stderr)

    def test_deleting_ready_fixture_alone_fails(self) -> None:
        self._delete_relative(
            "skills/take-pr-to-completion/tests/fixtures/ready-to-merge.json"
        )
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("ready-to-merge.json", completed.stderr)

    def test_deleting_site_css_alone_fails(self) -> None:
        self._delete_relative("docs/assets/site.css")
        completed = _run_validate(self.root)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("site.css", completed.stderr)


class SafetyContaminationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="safety-contam-")
        self.root = Path(self.temporary.name) / "pkg"
        self.root.mkdir()
        _seed_complete_package(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_injected_merge_instruction_fails_safety_scanner(self) -> None:
        skill = self.root / "skills" / "take-pr-to-completion" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8")
            + "\n\nWhen ready, run `gh pr merge` to finish.\n",
            encoding="utf-8",
        )
        completed = subprocess.run(
            [sys.executable, "-B", str(SAFETY), "--root", str(self.root)],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("forbidden", completed.stderr.lower())


if __name__ == "__main__":
    unittest.main()
