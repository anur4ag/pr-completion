#!/usr/bin/env python3
"""Run watcher unit/fixture tests with bytecode disabled and cache cleanup.

Cross-platform entrypoint (no shell path assumptions):

  python3 -B scripts/run-watcher-tests.py

Fails explicitly when discovery finds zero tests (Python version independent;
``unittest discover`` exit codes are not trusted for the empty suite case).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path


def package_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def remove_pycache(root: Path) -> int:
    removed = 0
    for path in root.rglob("__pycache__"):
        if ".git" in path.parts:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += 1
    for path in root.rglob("*.pyc"):
        if ".git" in path.parts:
            continue
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def count_discovered_tests(tests_dir: Path) -> int:
    """Return the number of tests unittest would discover under tests_dir."""
    if not tests_dir.is_dir():
        return 0
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(tests_dir), pattern="test*.py")
    return suite.countTestCases()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run watcher unit/fixture tests with zero-suite protection."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (default: repository containing this script)",
    )
    args = parser.parse_args(argv)

    root = (args.root if args.root is not None else package_root_from_script()).resolve()
    tests_dir = root / "skills" / "take-pr-to-completion" / "tests"
    if not tests_dir.is_dir():
        print(f"missing watcher tests directory: {tests_dir}", file=sys.stderr)
        return 1

    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    remove_pycache(root / "skills" / "take-pr-to-completion")

    discovered = count_discovered_tests(tests_dir)
    print(f"discovered {discovered} watcher test case(s) under {tests_dir}", flush=True)
    if discovered == 0:
        print(
            "watcher test suite discovered zero tests; refusing empty suite",
            file=sys.stderr,
        )
        return 1

    command = [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(tests_dir),
        "-v",
    ]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=str(root),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        return completed.returncode

    leftovers = [
        path
        for path in (root / "skills" / "take-pr-to-completion").rglob("__pycache__")
        if ".git" not in path.parts
    ]
    if leftovers:
        print("watcher tests left __pycache__ directories:", file=sys.stderr)
        for path in leftovers:
            print(f"  - {path}", file=sys.stderr)
        return 1
    print(f"watcher test suite passed ({discovered} test case(s))", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
