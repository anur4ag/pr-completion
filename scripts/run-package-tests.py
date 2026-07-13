#!/usr/bin/env python3
"""Canonical package-tooling test entrypoint (bytecode disabled, cache-clean).

Run from any working directory:

  python3 scripts/run-package-tests.py

This command:
  1. Forces PYTHONDONTWRITEBYTECODE=1 and python -B semantics
  2. Removes existing __pycache__ / *.pyc under the package tree
  3. Discovers and counts scripts/tests cases; fails if the count is zero
  4. Runs scripts/tests with unittest
  5. Re-scans for generated caches and fails if any remain
  6. Runs scripts/validate-release.py against the actual package tree

Fails explicitly when discovery finds zero tests (Python version independent;
``unittest discover`` exit codes are not trusted for the empty suite case).

Do not use plain `python3 -m unittest discover -s scripts/tests` as the release
gate; that path can leave bytecode and is not the documented package suite.
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


def iter_cache_paths(root: Path) -> list[Path]:
    skip_dirs = {".git"}
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        path = Path(dirpath)
        # Do not descend into VCS metadata.
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        if path.name == "__pycache__":
            found.append(path)
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith((".pyc", ".pyo")):
                found.append(path / name)
    return found


def remove_caches(root: Path) -> int:
    removed = 0
    for path in iter_cache_paths(root):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
        elif path.exists() or path.is_symlink():
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
        description=(
            "Run package tooling tests with cache cleanup, zero-suite "
            "protection, and validate-release."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (default: repository containing this script)",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="skip validate-release (used by zero-suite negative tests)",
    )
    args = parser.parse_args(argv)

    root = (args.root if args.root is not None else package_root_from_script()).resolve()
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # Prevent this process from writing bytecode if helpers are imported later.
    sys.dont_write_bytecode = True

    removed = remove_caches(root)
    print(f"removed {removed} cache path(s) under {root}", flush=True)

    tests_dir = root / "scripts" / "tests"
    if not tests_dir.is_dir():
        print(f"missing package tests directory: {tests_dir}", file=sys.stderr)
        return 1

    discovered = count_discovered_tests(tests_dir)
    print(f"discovered {discovered} package test case(s) under {tests_dir}", flush=True)
    if discovered == 0:
        print(
            "package test suite discovered zero tests; refusing empty suite",
            file=sys.stderr,
        )
        return 1

    unittest_cmd = [
        sys.executable,
        "-B",
        "-m",
        "unittest",
        "discover",
        "-s",
        str(tests_dir),
        "-v",
    ]
    print("+", " ".join(unittest_cmd), flush=True)
    completed = subprocess.run(
        unittest_cmd,
        cwd=str(root),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        return completed.returncode

    leftovers = iter_cache_paths(root)
    if leftovers:
        print("package suite left generated caches:", file=sys.stderr)
        for path in leftovers:
            print(f"  - {path}", file=sys.stderr)
        return 1
    print(
        f"no __pycache__ or .pyc files remain after package tests "
        f"({discovered} test case(s))",
        flush=True,
    )

    if args.skip_validate:
        print(f"package suite passed for {root} (validate-release skipped)", flush=True)
        return 0

    validate_script = root / "scripts" / "validate-release.py"
    if not validate_script.is_file():
        # Fall back to the validator shipped next to this entrypoint.
        validate_script = Path(__file__).resolve().parent / "validate-release.py"
    validate_cmd = [
        sys.executable,
        "-B",
        str(validate_script),
        "--root",
        str(root),
    ]

    print("+", " ".join(validate_cmd), flush=True)
    validated = subprocess.run(
        validate_cmd,
        cwd=str(root),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if validated.returncode != 0:
        return validated.returncode

    leftovers = iter_cache_paths(root)
    if leftovers:
        print("validate-release left generated caches:", file=sys.stderr)
        for path in leftovers:
            print(f"  - {path}", file=sys.stderr)
        return 1

    print(f"canonical package suite passed for {root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
