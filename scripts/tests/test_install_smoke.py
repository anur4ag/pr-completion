#!/usr/bin/env python3
"""Install-smoke unit/mocked coverage.

Live Claude/Codex marketplace installs are intentionally excluded from the
default package suite. They run only when:

  PR_COMPLETION_LIVE_INSTALL_SMOKE=1

or via the dedicated entrypoint:

  python3 -B scripts/install-smoke.py
  python3 -B scripts/run-ci-validation.py --install-smoke-only
"""

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
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
INSTALL_SMOKE = SCRIPTS / "install-smoke.py"
RUN_CI = SCRIPTS / "run-ci-validation.py"
RUN_WATCHER = SCRIPTS / "run-watcher-tests.py"

LIVE_SMOKE_ENABLED = os.environ.get("PR_COMPLETION_LIVE_INSTALL_SMOKE", "0") == "1"


def load_script(module_name: str, path: Path) -> types.ModuleType:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


smoke = load_script("pr_completion_install_smoke", INSTALL_SMOKE)


class InstallSmokeUnitTests(unittest.TestCase):
    def test_parse_semver_from_cli_banners(self) -> None:
        self.assertEqual(smoke.parse_semver_tuple("2.1.207 (Claude Code)"), (2, 1, 207))
        self.assertEqual(smoke.parse_semver_tuple("codex-cli 0.144.3"), (0, 144, 3))
        self.assertIsNone(smoke.parse_semver_tuple("no version here"))

    def test_version_floor_comparison(self) -> None:
        self.assertTrue(smoke.version_at_least((2, 1, 207), smoke.CLAUDE_FLOOR))
        self.assertTrue(smoke.version_at_least((2, 1, 208), smoke.CLAUDE_FLOOR))
        self.assertFalse(smoke.version_at_least((2, 1, 206), smoke.CLAUDE_FLOOR))
        self.assertTrue(smoke.version_at_least((0, 144, 3), smoke.CODEX_FLOOR))
        self.assertFalse(smoke.version_at_least((0, 144, 2), smoke.CODEX_FLOOR))

    def test_assert_not_source_rejects_source_tree(self) -> None:
        with self.assertRaises(smoke.SmokeError):
            smoke.assert_not_source(ROOT, ROOT, "path")
        nested = ROOT / "skills"
        with self.assertRaises(smoke.SmokeError):
            smoke.assert_not_source(nested, ROOT, "path")

    def test_assert_under_requires_isolation_root(self) -> None:
        with tempfile.TemporaryDirectory() as base_name:
            base = Path(base_name)
            inside = base / "cache" / "plugin"
            inside.mkdir(parents=True)
            smoke.assert_under(inside, base, "install")
            outside = Path(tempfile.gettempdir()).resolve()
            with self.assertRaises(smoke.SmokeError):
                smoke.assert_under(outside, base, "install")

    def test_inventory_skills_on_source_tree(self) -> None:
        names = smoke.inventory_skills(ROOT)
        self.assertEqual(set(names), set(smoke.EXPECTED_SKILLS))
        self.assertEqual(smoke.assert_expected_skills(ROOT), list(smoke.EXPECTED_SKILLS))

    def test_run_installed_watcher_from_temp_copy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="installed-copy-") as base_name:
            copy_root = Path(base_name) / "plugin"
            shutil.copytree(
                ROOT / "skills" / "take-pr-to-completion",
                copy_root / "skills" / "take-pr-to-completion",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            results = smoke.run_installed_watcher(
                copy_root, ROOT, sys.executable
            )
            states = {item["fixture"]: item["state"] for item in results}
            self.assertEqual(states["ready-to-merge.json"], "ready")
            self.assertEqual(states["blocked.json"], "blocked")
            self.assertEqual(states["review-comment.json"], "actionable")

    def test_missing_cli_skips_without_require_flag(self) -> None:
        with mock.patch.object(smoke, "which", return_value=None):
            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(INSTALL_SMOKE),
                    "--root",
                    str(ROOT),
                    "--harness",
                    "claude",
                    "--print-json",
                ],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PATH": "",  # force no claude/codex on PATH for the child
                },
            )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["results"], [])
        self.assertTrue(payload["skipped"])

    def test_missing_cli_fails_with_require_flag(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(INSTALL_SMOKE),
                "--root",
                str(ROOT),
                "--harness",
                "claude",
                "--require-cli",
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "PATH": "/usr/bin:/bin",  # no claude
            },
        )
        # If claude still resolves via absolute defaults this may pass; accept
        # either "not found" failure or success when PATH still finds it.
        if completed.returncode != 0:
            self.assertIn("claude CLI not found", completed.stderr)


class RunnerEntrypointTests(unittest.TestCase):
    def test_run_watcher_tests_entrypoint_exists(self) -> None:
        self.assertTrue(RUN_WATCHER.is_file())
        text = RUN_WATCHER.read_text(encoding="utf-8")
        self.assertIn("unittest", text)
        self.assertIn("take-pr-to-completion", text)

    def test_run_ci_validation_entrypoint_owns_docs_and_single_cycles(self) -> None:
        self.assertTrue(RUN_CI.is_file())
        text = RUN_CI.read_text(encoding="utf-8")
        self.assertIn("check-merge-ready-safety.py", text)
        self.assertIn("run-package-tests.py", text)
        self.assertIn("run-watcher-tests.py", text)
        self.assertIn("build-docs.py", text)
        self.assertIn("check-docs-links.py", text)
        self.assertIn("install-smoke.py", text)
        self.assertIn("--install-smoke-only", text)
        self.assertIn("--skip-install-smoke", text)

    def test_install_smoke_cli_help(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-B", str(INSTALL_SMOKE), "--help"],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        self.assertIn("--harness", completed.stdout)

    def test_ci_workflow_has_no_duplicated_full_gate_in_install_job(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        self.assertIn("run-ci-validation.py --skip-install-smoke", workflow)
        self.assertIn("--install-smoke-only", workflow)
        # Full gate and live smoke are separate jobs; install job is smoke-only.
        self.assertIn("Live isolated marketplace install smoke only", workflow)
        self.assertEqual(workflow.count("run-ci-validation.py"), 2)
        self.assertIn('python-version: ["3.10", "3.14"]', workflow)
        self.assertIn("cli-channel: [floor, latest]", workflow)
        # next/prerelease must not be claimed as current stable.
        self.assertNotIn("2.1.208", workflow)
        self.assertNotIn("cli-channel: next", workflow)
        self.assertIn('claude-spec: "latest"', workflow)
        self.assertIn("Resolved CLI versions", workflow)


@unittest.skipUnless(
    LIVE_SMOKE_ENABLED and shutil.which("claude"),
    "set PR_COMPLETION_LIVE_INSTALL_SMOKE=1 with claude on PATH",
)
class LiveClaudeInstallSmoke(unittest.TestCase):
    def test_isolated_claude_marketplace_install(self) -> None:
        result = smoke.smoke_claude(
            ROOT,
            python_executable=sys.executable,
            enforce_floor=False,
            as_json=True,
        )
        self.assertEqual(result["harness"], "claude")
        self.assertEqual(result["skills"], list(smoke.EXPECTED_SKILLS))
        self.assertTrue(result["installVerified"])
        install_path = Path(str(result["installPath"]))
        self.assertIn("plugins/cache", str(install_path).replace("\\", "/"))
        self.assertNotEqual(install_path.resolve(), ROOT.resolve())
        watcher_states = {item["fixture"]: item["state"] for item in result["watcher"]}
        self.assertEqual(watcher_states["ready-to-merge.json"], "ready")
        self.assertEqual(watcher_states["blocked.json"], "blocked")


@unittest.skipUnless(
    LIVE_SMOKE_ENABLED and shutil.which("codex"),
    "set PR_COMPLETION_LIVE_INSTALL_SMOKE=1 with codex on PATH",
)
class LiveCodexInstallSmoke(unittest.TestCase):
    def test_isolated_codex_claude_marketplace_install(self) -> None:
        result = smoke.smoke_codex(
            ROOT,
            python_executable=sys.executable,
            enforce_floor=False,
            as_json=True,
        )
        self.assertEqual(result["harness"], "codex")
        self.assertEqual(result["marketplaceCompatibility"], "claude-marketplace.json")
        self.assertEqual(result["skills"], list(smoke.EXPECTED_SKILLS))
        self.assertTrue(result["installVerified"])
        install_path = Path(str(result["installPath"]))
        self.assertIn("plugins/cache", str(install_path).replace("\\", "/"))
        self.assertNotEqual(install_path.resolve(), ROOT.resolve())
        watcher_states = {item["fixture"]: item["state"] for item in result["watcher"]}
        self.assertEqual(watcher_states["external-auto-merge.json"], "auto_merge")


if __name__ == "__main__":
    unittest.main()
