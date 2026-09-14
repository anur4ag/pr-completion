#!/usr/bin/env python3
"""Guarded, per-PR landing request for PR Completion.

This is the only shipped helper allowed to mutate GitHub merge state. It first
re-runs the read-only watcher, requires the observed head to remain
verified ready, and then invokes GitHub CLI without admin or protection bypass.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


EXIT_OK = 0
EXIT_BLOCKED = 20
METHOD_POLICY_FIELDS = {
    "merge": "mergeCommitAllowed",
    "rebase": "rebaseMergeAllowed",
    "squash": "squashMergeAllowed",
}


class LandingError(RuntimeError):
    """A fail-closed landing precondition or command failure."""


def emit(value: dict[str, object], pretty: bool) -> None:
    print(
        json.dumps(value, indent=2, sort_keys=True)
        if pretty
        else json.dumps(value, separators=(",", ":"), sort_keys=True),
        flush=True,
    )


def run(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            list(args),
            cwd=cwd,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as error:
        raise LandingError("command timed out; reconcile GitHub state before retrying") from error
    except FileNotFoundError as error:
        raise LandingError(f"required command not found: {args[0]}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no details"
        raise LandingError(f"{' '.join(args[:3])} failed ({result.returncode}): {detail}")
    return result


def watcher_snapshot(
    repository: Path, selector: str | None, fixture: Path | None,
    config: Path | None, no_config: bool, reviewers: Sequence[str],
    check_policy: str | None, strict_changes_requested: bool, cursor: Path | None = None,
) -> dict[str, object]:
    watcher = Path(__file__).with_name("pr_watch.py")
    command = [sys.executable, str(watcher), "--mode", "once"]
    if fixture is not None:
        command.extend(["--no-config", "--fixture", str(fixture)])
    else:
        target = str(repository) if selector is None else f"{repository}={selector}"
        command.extend(["--target", target])
        if no_config:
            command.append("--no-config")
        elif config is not None:
            command.extend(["--config", str(config)])
        for reviewer in reviewers:
            command.extend(["--reviewer", reviewer])
        if check_policy is not None:
            command.extend(["--check-policy", check_policy])
        if strict_changes_requested:
            command.append("--strict-changes-requested")
    if cursor is not None:
        command.extend(["--cursor", str(cursor)])
    result = run(command, repository)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LandingError("read-only watcher returned invalid JSON") from error
    if not isinstance(value, dict):
        raise LandingError("read-only watcher returned a non-object snapshot")
    return value


def verified_target(
    snapshot: dict[str, object],
    expected_head: str,
    mode: str,
    method: str | None,
) -> dict[str, object]:
    targets = snapshot.get("targets")
    if snapshot.get("state") != "ready":
        raise LandingError(f"pull request is not verified ready (state={snapshot.get('state')})")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        raise LandingError("landing requires exactly one verified pull request target")
    target = targets[0]
    pr = target.get("pr")
    if target.get("state") != "ready" or not isinstance(pr, dict):
        raise LandingError("target is not verified ready")
    current_head = pr.get("headSha")
    if current_head != expected_head:
        raise LandingError(
            f"PR head changed; resume observation (expected {expected_head}, current {current_head})"
        )
    url = pr.get("url")
    if not isinstance(url, str) or not url:
        raise LandingError("verified target is missing its pull request URL")
    queue_enabled = pr.get("isMergeQueueEnabled")
    if not isinstance(queue_enabled, bool):
        raise LandingError("fresh pull request merge-queue policy is unknown")
    if mode == "queue" and not queue_enabled:
        raise LandingError("merge queue is not enabled for the fresh pull request target")
    if mode == "auto" and queue_enabled:
        raise LandingError("fresh pull request policy requires the merge queue")
    if mode == "auto":
        policy = target.get("repositoryPolicy")
        if not isinstance(policy, dict) or method not in METHOD_POLICY_FIELDS:
            raise LandingError("fresh repository merge-method policy is unknown")
        field = METHOD_POLICY_FIELDS[method]
        allowed = policy.get(field)
        if not isinstance(allowed, bool):
            raise LandingError(f"fresh repository policy does not report {field}")
        if not allowed:
            raise LandingError(f"fresh repository policy does not allow {method} merges")
    return target


def landing_command(
    url: str,
    head: str,
    mode: str,
    method: str | None,
) -> list[str]:
    command = ["gh", "pr", "merge", url, "--match-head-commit", head]
    if mode == "queue":
        if method is not None:
            raise LandingError("queue mode does not accept a merge method")
        return command
    if method is None:
        raise LandingError("auto mode requires --method merge, squash, or rebase")
    return [*command, "--auto", f"--{method}"]


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Land an in-scope PR under task authorization after fresh protected readiness checks.",
    )
    parser.add_argument("--repo", default=".", help="pull request repository path")
    parser.add_argument("--pr", help="pull request number, URL, or branch selector")
    policy_source = parser.add_mutually_exclusive_group()
    policy_source.add_argument(
        "--config", help="watcher config path used for the ready observation"
    )
    policy_source.add_argument(
        "--no-config",
        action="store_true",
        help="preserve a ready observation that explicitly disabled config discovery",
    )
    parser.add_argument(
        "--reviewer",
        action="append",
        help="CLI-required reviewer from the ready observation; repeatable",
    )
    parser.add_argument("--check-policy", choices=("all", "required"))
    parser.add_argument("--strict-changes-requested", action="store_true")
    parser.add_argument("--head", required=True, help="observed head for the race guard; a changed head resumes watching")
    parser.add_argument("--mode", required=True, choices=("auto", "queue"))
    parser.add_argument("--method", choices=("merge", "squash", "rebase"))
    parser.add_argument("--dry-run", action="store_true", help="print the protected landing plan without mutation")
    parser.add_argument("--cursor", help="same watcher cursor, including its feedback evidence")
    parser.add_argument("--fixture", help="offline watcher fixture; requires --dry-run")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    repository = Path(args.repo).expanduser().resolve()
    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
    config = Path(args.config).expanduser().resolve() if args.config else None
    reviewers = tuple(args.reviewer or ())
    try:
        if not args.dry_run and fixture is not None:
            raise LandingError("offline fixtures cannot authorize a landing mutation")
        head = args.head.strip()
        if not head:
            raise LandingError("--head must be non-empty")
        command = landing_command("pending", head, args.mode, args.method)
        snapshot = watcher_snapshot(
            repository,
            args.pr,
            fixture,
            config,
            args.no_config,
            reviewers,
            args.check_policy,
            args.strict_changes_requested,
            Path(args.cursor).expanduser().resolve() if args.cursor else None,
        )
        if snapshot.get("state") == "merged":
            emit(snapshot, args.pretty)
            return EXIT_OK
        target = verified_target(snapshot, head, args.mode, args.method)
        pr = target["pr"]
        url = str(pr["url"])
        command = landing_command(url, head, args.mode, args.method)
        plan = {"schemaVersion": 1, "state": "landing_planned",
                "repository": target.get("repository"), "pr": pr,
                "headSha": head, "mode": args.mode, "method": args.method,
                "readinessPolicy": snapshot.get("policy"), "command": command}
        if args.dry_run:
            emit(plan, args.pretty)
            return EXIT_OK
        result = run(command, repository)
        emit(
            {
                **plan,
                "state": "landing_requested",
                "command": command,
                "stdout": result.stdout.strip() or None,
                "requestedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            args.pretty,
        )
        return EXIT_OK
    except LandingError as error:
        emit(
            {
                "schemaVersion": 1,
                "state": "blocked",
                "actions": [{"type": "landing_error", "reason": str(error)}],
                "errors": [str(error)],
            },
            args.pretty,
        )
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
