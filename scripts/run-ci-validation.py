#!/usr/bin/env python3
"""Canonical local/CI validation entrypoint for pr-completion.

Owns each validation exactly once (no duplicated cycles inside this runner):

  1. merge-ready safety scanner
  2. package tooling suite + validate-release (unit/mocked tests only)
  3. watcher unit/fixture suite
  4. docs static site build
  5. docs link validation
  6. optional live Claude/Codex install smoke (install-smoke.py)

Live marketplace install smoke is opt-in via flags and is never folded into the
package unit suite. Batch-A package gates remain inside run-package-tests.py.

  python3 -B scripts/run-ci-validation.py
  python3 -B scripts/run-ci-validation.py --skip-install-smoke
  python3 -B scripts/run-ci-validation.py --require-cli --enforce-floor
  python3 -B scripts/run-ci-validation.py --install-smoke-only
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_step(label: str, command: list[str], cwd: Path) -> int:
    print(f"==> {label}", flush=True)
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        print(f"FAILED: {label} (exit {completed.returncode})", file=sys.stderr)
    else:
        print(f"OK: {label}", flush=True)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full pr-completion validation suite exactly once."
    )
    parser.add_argument(
        "--skip-install-smoke",
        action="store_true",
        help="skip live Claude/Codex marketplace install smoke tests",
    )
    parser.add_argument(
        "--install-smoke-only",
        action="store_true",
        help=(
            "run only live install smoke (for the dedicated CI install-smoke job; "
            "does not re-run package/watcher/docs gates)"
        ),
    )
    parser.add_argument(
        "--require-cli",
        action="store_true",
        help="require claude/codex CLIs for install smoke (default: skip if missing)",
    )
    parser.add_argument(
        "--enforce-floor",
        action="store_true",
        help="require Claude/Codex CLI versions to meet verified floors",
    )
    parser.add_argument(
        "--harness",
        choices=("both", "claude", "codex"),
        default="both",
        help="install-smoke harness selection",
    )
    args = parser.parse_args(argv)

    if args.install_smoke_only and args.skip_install_smoke:
        print(
            "conflicting flags: --install-smoke-only and --skip-install-smoke",
            file=sys.stderr,
        )
        return 2

    root = package_root()
    scripts = root / "scripts"
    python = sys.executable
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    # Keep package unit discovery free of live marketplace smoke unless a test
    # module explicitly opts in via PR_COMPLETION_LIVE_INSTALL_SMOKE=1.
    os.environ.setdefault("PR_COMPLETION_LIVE_INSTALL_SMOKE", "0")
    sys.dont_write_bytecode = True

    def install_smoke_command() -> list[str]:
        command = [
            python,
            "-B",
            str(scripts / "install-smoke.py"),
            "--root",
            str(root),
            "--harness",
            args.harness,
            "--python",
            python,
        ]
        if args.require_cli:
            command.append("--require-cli")
        if args.enforce_floor:
            command.append("--enforce-floor")
        return command

    if args.install_smoke_only:
        code = run_step("isolated install smoke", install_smoke_command(), root)
        if code != 0:
            return code
        print("install-smoke-only validation passed", flush=True)
        return 0

    steps: list[tuple[str, list[str]]] = [
        (
            "merge-ready safety scanner",
            [python, "-B", str(scripts / "check-merge-ready-safety.py"), "--root", str(root)],
        ),
        (
            "package tooling + validate-release",
            [python, "-B", str(scripts / "run-package-tests.py")],
        ),
        (
            "watcher unit and fixture tests",
            [python, "-B", str(scripts / "run-watcher-tests.py")],
        ),
        (
            "docs static site build",
            [python, "-B", str(scripts / "build-docs.py"), "--root", str(root)],
        ),
        (
            "docs link validation",
            [python, "-B", str(scripts / "check-docs-links.py"), "--root", str(root)],
        ),
    ]

    if not args.skip_install_smoke:
        steps.append(("isolated install smoke", install_smoke_command()))

    for label, command in steps:
        code = run_step(label, command, root)
        if code != 0:
            return code

    print("all CI validation steps passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
