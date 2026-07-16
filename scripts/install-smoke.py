#!/usr/bin/env python3
"""Isolated Claude Code and Codex marketplace installation smoke tests.

Creates disposable harness homes, adds the checked-out repository as a
marketplace, installs ``pr-completion@pr-completion``, asserts exactly four
skills, and runs the *installed* watcher against offline fixtures.

Installation must resolve from the harness plugin cache under the isolated
home, not fall back to the source working tree.

Verified floors (claimed only when exercised):
  - Claude Code 2.1.207
  - Codex CLI 0.144.3

Usage examples:

  python3 -B scripts/install-smoke.py
  python3 -B scripts/install-smoke.py --harness claude
  python3 -B scripts/install-smoke.py --harness codex --require-cli
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PLUGIN_NAME = "pr-completion"
MARKETPLACE_NAME = "pr-completion"
INSTALL_SPEC = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
EXPECTED_SKILLS = (
    "take-pr-to-completion",
    "commit-workspace-changes",
    "gh-review-comment-triage",
    "merge-conflict-resolution",
)

# Claimed compatibility floors. Do not advertise lower versions until they pass.
CLAUDE_FLOOR = (2, 1, 207)
CODEX_FLOOR = (0, 144, 3)

# Exit codes match pr_watch.EXIT_OBSERVED (0) and EXIT_BLOCKED (20).
WATCHER_FIXTURES = (
    ("ready-to-merge.json", "ready", 0),
    ("review-comment.json", "actionable", 0),
    ("pending-ci.json", "pending", 0),
    ("blocked.json", "blocked", 20),
    ("external-auto-merge.json", "auto_merge", 0),
    ("conflict.json", "actionable", 0),
)

VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


class SmokeError(Exception):
    """Install smoke failure."""


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (
            (path / ".claude-plugin").is_dir() or (path / ".codex-plugin").is_dir()
        ):
            return path
    raise SmokeError(f"could not locate plugin root from {start}")


def parse_semver_tuple(text: str) -> tuple[int, int, int] | None:
    match = VERSION_RE.search(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(version: tuple[int, int, int]) -> str:
    return f"{version[0]}.{version[1]}.{version[2]}"


def version_at_least(actual: tuple[int, int, int], floor: tuple[int, int, int]) -> bool:
    return actual >= floor


def which(command: str) -> str | None:
    return shutil.which(command)


def log(message: str, *, as_json: bool) -> None:
    """Human progress goes to stderr when stdout must stay machine JSON."""
    stream = sys.stderr if as_json else sys.stdout
    print(message, file=stream, flush=True)


def resolve_command(command: list[str], *, env: dict[str, str] | None = None) -> list[str]:
    """Resolve argv[0] to an absolute path when possible.

    On Windows, npm global CLIs are typically ``*.cmd`` shims. ``subprocess``
    without ``shell=True`` does not apply PATHEXT the same way the shell does,
    so bare names like ``claude`` fail with WinError 2 even when ``shutil.which``
    can see them. Resolve once up front using PATH from ``env`` when provided.
    """
    if not command:
        return command
    path_env = None if env is None else env.get("PATH")
    resolved = shutil.which(command[0], path=path_env)
    if resolved is None:
        return command
    return [resolved, *command[1:]]


def run(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path | None = None,
    as_json: bool = False,
) -> subprocess.CompletedProcess[str]:
    resolved = resolve_command(command, env=env)
    log("+ " + " ".join(resolved), as_json=as_json)
    completed = subprocess.run(
        resolved,
        env=env,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.stdout.strip():
        log(completed.stdout.rstrip(), as_json=as_json)
    if completed.stderr.strip():
        print(completed.stderr.rstrip(), file=sys.stderr, flush=True)
    if completed.returncode != 0:
        raise SmokeError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed


def base_env(fake_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    # Avoid leaking the caller's XDG layout into disposable homes.
    for key in (
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
    ):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def assert_under(path: Path, root: Path, label: str) -> None:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as error:
        raise SmokeError(
            f"{label} must live under isolated root {root_resolved}; got {resolved}"
        ) from error


def assert_not_source(path: Path, source_root: Path, label: str) -> None:
    resolved = path.resolve()
    source = source_root.resolve()
    if resolved == source:
        raise SmokeError(f"{label} resolved to the source tree: {resolved}")
    try:
        resolved.relative_to(source)
        # Nested under source is also wrong for an installed cache copy.
        raise SmokeError(f"{label} is nested under the source tree: {resolved}")
    except ValueError:
        return


def inventory_skills(plugin_root: Path) -> list[str]:
    skills_root = plugin_root / "skills"
    if not skills_root.is_dir():
        raise SmokeError(f"installed plugin missing skills/: {plugin_root}")
    names: list[str] = []
    for child in sorted(skills_root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            names.append(child.name)
    return names


def assert_expected_skills(plugin_root: Path) -> list[str]:
    names = inventory_skills(plugin_root)
    if set(names) != set(EXPECTED_SKILLS) or len(names) != len(EXPECTED_SKILLS):
        raise SmokeError(
            "installed skill inventory mismatch: "
            f"expected {sorted(EXPECTED_SKILLS)}, got {sorted(names)}"
        )
    # Return the canonical skill order from the release contract.
    return list(EXPECTED_SKILLS)


def run_installed_watcher(
    installed_root: Path,
    source_root: Path,
    python_executable: str,
) -> list[dict[str, object]]:
    watcher = (
        installed_root
        / "skills"
        / "take-pr-to-completion"
        / "scripts"
        / "pr_watch.py"
    )
    if not watcher.is_file():
        raise SmokeError(f"installed watcher missing: {watcher}")
    lander = watcher.with_name("pr_land.py")
    if not lander.is_file():
        raise SmokeError(f"installed guarded landing helper missing: {lander}")
    assert_not_source(watcher, source_root, "installed watcher")
    assert_not_source(lander, source_root, "installed guarded landing helper")

    fixtures_dir = (
        installed_root / "skills" / "take-pr-to-completion" / "tests" / "fixtures"
    )
    results: list[dict[str, object]] = []
    # Run from a cwd that is neither source nor install root, so relative
    # path fallbacks cannot accidentally load the working tree.
    with tempfile.TemporaryDirectory(prefix="pr-completion-watch-cwd-") as cwd_name:
        cwd = Path(cwd_name)
        for fixture_name, expected_state, expected_code in WATCHER_FIXTURES:
            fixture = fixtures_dir / fixture_name
            if not fixture.is_file():
                raise SmokeError(f"installed fixture missing: {fixture}")
            completed = subprocess.run(
                [
                    python_executable,
                    "-B",
                    str(watcher),
                    "--mode",
                    "once",
                    "--fixture",
                    str(fixture),
                ],
                cwd=str(cwd),
                text=True,
                capture_output=True,
                check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            if completed.returncode != expected_code:
                raise SmokeError(
                    f"installed watcher {fixture_name}: expected exit "
                    f"{expected_code}, got {completed.returncode}\n"
                    f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
                )
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError as error:
                raise SmokeError(
                    f"installed watcher {fixture_name}: invalid JSON stdout: "
                    f"{completed.stdout!r}"
                ) from error
            if payload.get("state") != expected_state:
                raise SmokeError(
                    f"installed watcher {fixture_name}: expected state "
                    f"{expected_state!r}, got {payload.get('state')!r}"
                )
            results.append(
                {
                    "fixture": fixture_name,
                    "state": payload.get("state"),
                    "exitCode": completed.returncode,
                }
            )
        landing = subprocess.run(
            [
                python_executable,
                "-B",
                str(lander),
                "--fixture",
                str(fixtures_dir / "ready-to-merge.json"),
                "--head",
                "head-ready",
                "--mode",
                "auto",
                "--method",
                "squash",
            ],
            cwd=str(cwd),
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if landing.returncode != 0:
            raise SmokeError(
                "installed guarded landing plan failed: "
                f"{landing.stderr or landing.stdout}"
            )
        plan = json.loads(landing.stdout)
        if plan.get("state") != "confirmation_required" or not plan.get(
            "requiresConfirmation"
        ):
            raise SmokeError(f"installed guarded landing plan is unsafe: {plan}")
    return results


def claude_version(env: dict[str, str], *, as_json: bool = False) -> tuple[int, int, int]:
    completed = run(["claude", "--version"], env=env, as_json=as_json)
    version = parse_semver_tuple(completed.stdout + "\n" + completed.stderr)
    if version is None:
        raise SmokeError(f"could not parse claude version from: {completed.stdout!r}")
    return version


def codex_version(env: dict[str, str], *, as_json: bool = False) -> tuple[int, int, int]:
    completed = run(["codex", "--version"], env=env, as_json=as_json)
    version = parse_semver_tuple(completed.stdout + "\n" + completed.stderr)
    if version is None:
        raise SmokeError(f"could not parse codex version from: {completed.stdout!r}")
    return version


def smoke_claude(
    source_root: Path,
    *,
    python_executable: str,
    enforce_floor: bool,
    as_json: bool = False,
) -> dict[str, object]:
    if which("claude") is None:
        raise SmokeError("claude CLI not found on PATH")

    with tempfile.TemporaryDirectory(prefix="pr-completion-claude-smoke-") as base_name:
        base = Path(base_name)
        fake_home = base / "home"
        config_dir = base / "claude-config"
        fake_home.mkdir()
        config_dir.mkdir()
        env = base_env(fake_home)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        version = claude_version(env, as_json=as_json)
        if enforce_floor and not version_at_least(version, CLAUDE_FLOOR):
            raise SmokeError(
                f"claude {format_version(version)} is below verified floor "
                f"{format_version(CLAUDE_FLOOR)}"
            )

        # Marketplace + install from the repository checkout (release source).
        run(
            ["claude", "plugin", "marketplace", "add", str(source_root), "--scope", "user"],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        run(
            [
                "claude",
                "plugin",
                "install",
                INSTALL_SPEC,
                "--scope",
                "user",
            ],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        listed = run(
            ["claude", "plugin", "list", "--json"],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        try:
            plugins = json.loads(listed.stdout)
        except json.JSONDecodeError as error:
            raise SmokeError(f"claude plugin list --json not parseable: {listed.stdout!r}") from error
        if not isinstance(plugins, list):
            raise SmokeError(f"unexpected claude plugin list payload: {plugins!r}")

        match = None
        for item in plugins:
            if isinstance(item, dict) and item.get("id") == INSTALL_SPEC:
                match = item
                break
        if match is None:
            raise SmokeError(f"claude install missing {INSTALL_SPEC} in plugin list")

        install_path = Path(str(match.get("installPath") or ""))
        if not install_path.is_dir():
            raise SmokeError(f"claude installPath missing or not a directory: {install_path}")
        assert_under(install_path, config_dir, "claude installPath")
        assert_not_source(install_path, source_root, "claude installPath")

        skills = assert_expected_skills(install_path)
        # Prefer Claude inventory command when available.
        details = run(
            ["claude", "plugin", "details", INSTALL_SPEC],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        details_text = details.stdout + details.stderr
        for skill in EXPECTED_SKILLS:
            if skill not in details_text:
                raise SmokeError(
                    f"claude plugin details missing skill {skill!r}:\n{details_text}"
                )

        watcher_results = run_installed_watcher(
            install_path, source_root, python_executable
        )
        return {
            "harness": "claude",
            "cliVersion": format_version(version),
            "floor": format_version(CLAUDE_FLOOR),
            "installPath": str(install_path),
            "skills": skills,
            "watcher": watcher_results,
            "installVerified": True,
        }


def smoke_codex(
    source_root: Path,
    *,
    python_executable: str,
    enforce_floor: bool,
    as_json: bool = False,
) -> dict[str, object]:
    """Install from the Claude marketplace path (repo root) into CODEX_HOME.

    This is the required Codex compatibility path for
    ``.claude-plugin/marketplace.json``. If it fails, callers should fall back
    to generating a Codex catalog from the same release metadata rather than
    hand-maintaining a second catalog.
    """
    if which("codex") is None:
        raise SmokeError("codex CLI not found on PATH")

    with tempfile.TemporaryDirectory(prefix="pr-completion-codex-smoke-") as base_name:
        base = Path(base_name)
        fake_home = base / "home"
        codex_home = base / "codex-home"
        fake_home.mkdir()
        codex_home.mkdir()
        (fake_home / ".agents" / "plugins").mkdir(parents=True)
        env = base_env(fake_home)
        env["CODEX_HOME"] = str(codex_home)

        version = codex_version(env, as_json=as_json)
        if enforce_floor and not version_at_least(version, CODEX_FLOOR):
            raise SmokeError(
                f"codex {format_version(version)} is below verified floor "
                f"{format_version(CODEX_FLOOR)}"
            )

        run(
            ["codex", "plugin", "marketplace", "add", str(source_root), "--json"],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        added = run(
            ["codex", "plugin", "add", INSTALL_SPEC, "--json"],
            env=env,
            cwd=fake_home,
            as_json=as_json,
        )
        try:
            install_json = json.loads(added.stdout)
        except json.JSONDecodeError as error:
            raise SmokeError(
                f"codex plugin add --json not parseable: {added.stdout!r}"
            ) from error

        install_path = Path(str(install_json.get("installedPath") or ""))
        if not install_path.is_dir():
            raise SmokeError(f"codex installedPath missing: {install_path}")
        assert_under(install_path, codex_home, "codex installedPath")
        assert_not_source(install_path, source_root, "codex installedPath")

        if install_json.get("pluginId") not in {INSTALL_SPEC, PLUGIN_NAME}:
            # Accept either full id or bare name depending on CLI shape.
            if install_json.get("name") != PLUGIN_NAME:
                raise SmokeError(f"unexpected codex install payload: {install_json}")

        skills = assert_expected_skills(install_path)
        watcher_results = run_installed_watcher(
            install_path, source_root, python_executable
        )
        return {
            "harness": "codex",
            "cliVersion": format_version(version),
            "floor": format_version(CODEX_FLOOR),
            "installPath": str(install_path),
            "skills": skills,
            "watcher": watcher_results,
            "marketplaceCompatibility": "claude-marketplace.json",
            "installPayload": {
                key: install_json.get(key)
                for key in (
                    "pluginId",
                    "name",
                    "marketplaceName",
                    "version",
                    "installedPath",
                )
            },
            "installVerified": True,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Isolated Claude/Codex marketplace install smoke tests that run the "
            "installed watcher against offline fixtures."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (defaults to repository containing this script)",
    )
    parser.add_argument(
        "--harness",
        choices=("both", "claude", "codex"),
        default="both",
        help="which harness install path to exercise",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to launch the installed watcher",
    )
    parser.add_argument(
        "--require-cli",
        action="store_true",
        help="fail when a requested CLI is missing (default: skip missing CLIs)",
    )
    parser.add_argument(
        "--enforce-floor",
        action="store_true",
        help="fail when the installed CLI is below the verified floor",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="emit machine-readable results on stdout",
    )
    args = parser.parse_args(argv)

    try:
        source_root = (
            args.root if args.root is not None else plugin_root_from(Path(__file__))
        ).resolve()
        results: list[dict[str, object]] = []
        skipped: list[str] = []

        want_claude = args.harness in {"both", "claude"}
        want_codex = args.harness in {"both", "codex"}

        if want_claude:
            if which("claude") is None:
                message = "claude CLI not found on PATH"
                if args.require_cli:
                    raise SmokeError(message)
                skipped.append(message)
                log(f"skip: {message}", as_json=args.print_json)
            else:
                results.append(
                    smoke_claude(
                        source_root,
                        python_executable=args.python,
                        enforce_floor=args.enforce_floor,
                        as_json=args.print_json,
                    )
                )

        if want_codex:
            if which("codex") is None:
                message = "codex CLI not found on PATH"
                if args.require_cli:
                    raise SmokeError(message)
                skipped.append(message)
                log(f"skip: {message}", as_json=args.print_json)
            else:
                try:
                    results.append(
                        smoke_codex(
                            source_root,
                            python_executable=args.python,
                            enforce_floor=args.enforce_floor,
                            as_json=args.print_json,
                        )
                    )
                except SmokeError as error:
                    # Required Codex + Claude marketplace compatibility path failed.
                    # Surface a clear remediation hint per the release plan.
                    raise SmokeError(
                        f"{error}\n"
                        "Codex could not install from the Claude marketplace path. "
                        "Generate a Codex catalog from the same release metadata "
                        "(.agents/plugins/marketplace.json) instead of hand-maintaining "
                        "a second catalog, then re-run this smoke test."
                    ) from error

        payload = {
            "sourceRoot": str(source_root),
            "results": results,
            "skipped": skipped,
        }
        if args.print_json:
            print(json.dumps(payload, indent=2))
        else:
            for item in results:
                print(
                    f"{item['harness']} install smoke passed "
                    f"(cli {item['cliVersion']}, floor {item['floor']})",
                    flush=True,
                )
                print(f"  installPath: {item['installPath']}", flush=True)
                print(f"  skills: {', '.join(item['skills'])}", flush=True)
            if skipped:
                print(f"skipped: {len(skipped)}", flush=True)
            if not results and skipped:
                print(
                    "no install smoke tests ran (CLIs missing); "
                    "pass --require-cli to fail instead",
                    flush=True,
                )
            elif results:
                print("install smoke suite passed", flush=True)
        return 0
    except SmokeError as error:
        print(f"install-smoke failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
