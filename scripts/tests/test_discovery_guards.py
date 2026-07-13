#!/usr/bin/env python3
"""Zero-suite discovery must fail for watcher and package runners."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
RUN_PACKAGE = SCRIPTS / "run-package-tests.py"
RUN_WATCHER = SCRIPTS / "run-watcher-tests.py"


def _run(script: Path, *extra: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-B", str(script), *extra]
    if root is not None:
        command.extend(["--root", str(root)])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        cwd=str(ROOT),
    )


class ZeroDiscoveryGuardTests(unittest.TestCase):
    def test_package_runner_fails_on_empty_tests_dir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pkg-zero-") as base_name:
            root = Path(base_name)
            (root / "scripts" / "tests").mkdir(parents=True)
            completed = _run(RUN_PACKAGE, "--skip-validate", root=root)
            self.assertNotEqual(completed.returncode, 0, msg=completed.stdout + completed.stderr)
            self.assertIn("zero tests", completed.stderr.lower())

    def test_watcher_runner_fails_on_empty_tests_dir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="watch-zero-") as base_name:
            root = Path(base_name)
            tests = root / "skills" / "take-pr-to-completion" / "tests"
            tests.mkdir(parents=True)
            completed = _run(RUN_WATCHER, root=root)
            self.assertNotEqual(completed.returncode, 0, msg=completed.stdout + completed.stderr)
            self.assertIn("zero tests", completed.stderr.lower())

    def test_package_runner_fails_when_tests_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pkg-missing-") as base_name:
            root = Path(base_name)
            (root / "scripts").mkdir(parents=True)
            completed = _run(RUN_PACKAGE, "--skip-validate", root=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("missing package tests directory", completed.stderr)

    def test_watcher_runner_fails_when_tests_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="watch-missing-") as base_name:
            root = Path(base_name)
            completed = _run(RUN_WATCHER, root=root)
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("missing watcher tests directory", completed.stderr)

    def test_real_package_discovery_is_nonzero(self) -> None:
        # Sanity: the live tree must keep discovering a positive count.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "run_package_tests_mod", RUN_PACKAGE
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        count = module.count_discovered_tests(ROOT / "scripts" / "tests")
        self.assertGreater(count, 0)

    def test_real_watcher_discovery_is_nonzero(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "run_watcher_tests_mod", RUN_WATCHER
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        count = module.count_discovered_tests(
            ROOT / "skills" / "take-pr-to-completion" / "tests"
        )
        self.assertGreater(count, 0)


if __name__ == "__main__":
    unittest.main()
