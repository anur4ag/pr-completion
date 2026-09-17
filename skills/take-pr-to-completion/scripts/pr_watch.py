#!/usr/bin/env python3
"""Deterministic GitHub pull-request state watcher for agent workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse


SCHEMA_VERSION = 1
CONFIG_NAME = ".pr-completion.json"
EXIT_OBSERVED = 0
EXIT_BLOCKED = 20
EXIT_TIMEOUT = 30
EXIT_INTERRUPTED = 130

# gh pr checks buckets we understand. Anything else fails closed.
KNOWN_CHECK_BUCKETS = frozenset({"pass", "fail", "pending", "skipping", "cancel", "unknown"})
# Only these buckets may be present (with items) when declaring verified ready.
READY_SAFE_CHECK_BUCKETS = frozenset({"pass", "skipping"})
# Explicit bucket -> accepted conclusion states. Incoherent pairs fail closed.
BUCKET_ALLOWED_STATES: dict[str, frozenset[str]] = {
    "pass": frozenset({"SUCCESS"}),
    "fail": frozenset(
        {
            "FAILURE",
            "ERROR",
            "TIMED_OUT",
            "STARTUP_FAILURE",
            "ACTION_REQUIRED",
        }
    ),
    "pending": frozenset(
        {
            "PENDING",
            "IN_PROGRESS",
            "QUEUED",
            "REQUESTED",
            "WAITING",
            "EXPECTED",
        }
    ),
    "skipping": frozenset({"SKIPPED", "NEUTRAL"}),
    "cancel": frozenset({"CANCELLED", "CANCELED"}),
}
# GitHub mergeStateStatus values that may yield ready. All others fail closed.
READY_SAFE_MERGE_STATES = frozenset({"CLEAN", "HAS_HOOKS"})
READY_SAFE_MERGEABLE = frozenset({"MERGEABLE"})
# Merge states already mapped to conflict/base-behind actions or explicit pending.
HANDLED_UNSAFE_MERGE_STATES = frozenset({"DIRTY", "BEHIND", "UNKNOWN", "BLOCKED", "DRAFT"})

DEFAULTS = {
    "mode": "until-actionable",
    "intervalSeconds": 30.0,
    "maxIntervalSeconds": 120.0,
    "timeoutSeconds": 3600.0,
    "jitter": 0.1,
    "maxErrors": 5,
    "discover": "current",
    "maxDepth": 4,
    "checkPolicy": "all",
    "strictChangesRequested": False,
    "requiredReviewers": [],
    "requireApproval": False,
    "targets": [],
    "cursorPath": "auto",
    "observationsPath": None,
}

CONFIG_KEYS = {"version", *DEFAULTS.keys()}
PR_FIELDS = (
    "number,url,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,"
    "mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,mergedAt,mergeCommit,statusCheckRollup"
)
CHECK_FIELDS = "name,state,bucket,link,workflow,startedAt,completedAt"
THREAD_QUERY = """
query($owner: String!, $name: String!, $number: Int!,
      $threadCursor: String, $reviewCursor: String, $commentCursor: String, $reactionCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      headRefOid
      isMergeQueueEnabled
      mergeQueueEntry {
        id state position enqueuedAt
        enqueuer { login }
        headCommit { oid }
      }
      reviewThreads(first: 100, after: $threadCursor) {
        nodes {
          id isResolved isOutdated path line originalLine
          originalComment: comments(first: 1) {
            nodes { id author { login } createdAt url body }
          }
          comments(last: 1) {
            nodes { id author { login } createdAt url body }
          }
        }
        pageInfo { hasNextPage endCursor }
      }
      reviews(first: 100, after: $reviewCursor) {
        nodes { id author { login } state body submittedAt url commit { oid } }
        pageInfo { hasNextPage endCursor }
      }
      comments(first: 100, after: $commentCursor) {
        nodes {
          id author { login } body createdAt url
          reactions(last: 100) { nodes { content createdAt user { login } } }
        }
        pageInfo { hasNextPage endCursor }
      }
      reactions(first: 100, after: $reactionCursor) {
        nodes { id content createdAt user { login } }
        pageInfo { hasNextPage endCursor }
      }
    }
  }
}
""".strip()

SKIP_DIRECTORIES = {
    ".cache",
    ".git",
    ".idea",
    ".next",
    ".tox",
    ".venv",
    ".vscode",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


class WatchError(RuntimeError):
    """An expected command, configuration, or data-shape failure."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class Target:
    path: Path
    selector: str | None
    kind: str


@dataclass(frozen=True)
class Settings:
    mode: str
    interval_seconds: float
    max_interval_seconds: float
    timeout_seconds: float
    jitter: float
    max_errors: int
    discover: str
    max_depth: int
    check_policy: str
    policy_source: str
    config_path: Path | None
    strict_changes_requested: bool
    required_reviewers: tuple[str, ...]
    targets: tuple[Target, ...]
    cursor_path: Path | None
    observations_path: Path | None
    await_merge_head: str | None
    await_merge_mode: str | None
    await_merge_since: datetime | None
    await_merge_grace_seconds: float
    fixture: Path | None
    pretty: bool
    verbose: bool
    output_path: Path | None = None
    require_approval: bool = False


class Runner:
    def __init__(self) -> None:
        self.repositories: dict[Path, object] = {}
        self.target_errors: dict[Target, int] = {}
        self.target_failures: dict[Target, dict[str, object]] = {}
        self.deadline: float | None = None

    def run(
        self,
        args: Sequence[str],
        cwd: Path,
        allowed_codes: frozenset[int] = frozenset({0}),
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                list(args),
                cwd=cwd,
                text=True,
                capture_output=True,
                check=False,
                timeout=min(60.0, max(0.01, self.deadline - time.monotonic()))
                if self.deadline is not None else 60.0,
            )
        except subprocess.TimeoutExpired as error:
            raise WatchError(f"{' '.join(args[:3])} timed out", retryable=True) from error
        except FileNotFoundError as error:
            raise WatchError(f"required command not found: {args[0]}") from error

        if result.returncode not in allowed_codes:
            detail = result.stderr.strip() or result.stdout.strip() or "no details"
            retryable = is_retryable_error(detail)
            command = " ".join(args[:3])
            raise WatchError(f"{command} failed ({result.returncode}): {detail}", retryable)
        return result

    def json(
        self,
        args: Sequence[str],
        cwd: Path,
        allowed_codes: frozenset[int] = frozenset({0}),
    ) -> object:
        result = self.run(args, cwd, allowed_codes)
        output = result.stdout.strip()
        if not output and result.returncode != 0 and result.stderr.strip():
            detail = result.stderr.strip()
            raise WatchError(detail, retryable=is_retryable_error(detail))
        if not output:
            raise WatchError(f"{' '.join(args[:3])} returned no JSON")
        try:
            return json.loads(output)
        except json.JSONDecodeError as error:
            raise WatchError(f"{' '.join(args[:3])} returned invalid JSON") from error


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def is_retryable_error(message: str) -> bool:
    lowered = message.lower()
    retryable_markers = (
        "timeout",
        "timed out",
        "temporarily unavailable",
        "connection reset",
        "connection refused",
        "could not resolve host",
        "server error",
        "http 502",
        "http 503",
        "http 504",
        "rate limit",
    )
    return any(marker in lowered for marker in retryable_markers)


def normalize_login(login: str) -> str:
    normalized = login.strip().lower()
    if normalized.endswith("[bot]"):
        normalized = normalized[:-5]
    return normalized


def find_config(start: Path) -> Path | None:
    current = start.resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def read_config(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WatchError(f"config file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise WatchError(f"invalid JSON config {path}: {error}") from error
    if not isinstance(value, dict):
        raise WatchError("config root must be an object")
    unknown = sorted(set(value) - CONFIG_KEYS)
    if unknown:
        raise WatchError(f"unknown config keys: {', '.join(unknown)}")
    if value.get("version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise WatchError(f"config version must be {SCHEMA_VERSION}")
    return value


def parse_target(value: str, base: Path, kind: str = "explicit") -> Target:
    path_text, separator, selector_text = value.rpartition("=")
    if not separator:
        path_text = value
        selector_text = "auto"
    if not path_text:
        raise WatchError(f"target path is empty: {value}")
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = base / path
    selector = None if selector_text in {"", "auto"} else selector_text
    return Target(path.resolve(), selector, kind)


def config_targets(values: object, base: Path) -> tuple[Target, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise WatchError("config targets must be an array")
    targets: list[Target] = []
    for value in values:
        if isinstance(value, str):
            targets.append(parse_target(value, base))
            continue
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise WatchError("each config target must be a string or an object with path")
        selector = value.get("pr", "auto")
        if not isinstance(selector, (str, int)):
            raise WatchError("target pr must be auto, a branch, URL, or PR number")
        encoded = f"{value['path']}={selector}"
        targets.append(parse_target(encoded, base))
    return tuple(targets)


def positive_float(value: object, name: str, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise WatchError(f"{name} must be a number") from error
    if not math.isfinite(number):
        raise WatchError(f"{name} must be finite")
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise WatchError(f"{name} must be {qualifier}")
    return number


def utc_timestamp(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise WatchError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise WatchError(f"{name} must include a timezone")
    normalized = parsed.astimezone(timezone.utc)
    if normalized > datetime.now(timezone.utc):
        raise WatchError(f"{name} cannot be in the future")
    return normalized


def positive_int(value: object, name: str, allow_zero: bool = False) -> int:
    if isinstance(value, bool):
        raise WatchError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise WatchError(f"{name} must be an integer") from error
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise WatchError(f"{name} must be {qualifier}")
    return number


def git_directory(start: Path) -> Path | None:
    current = start.resolve()
    for directory in (current, *current.parents):
        marker = directory / ".git"
        if marker.is_dir():
            return marker.resolve()
        if not marker.is_file():
            continue
        try:
            prefix, separator, value = marker.read_text(encoding="utf-8").strip().partition(":")
        except OSError:
            continue
        if separator and prefix.lower() == "gitdir" and value.strip():
            path = Path(value.strip()).expanduser()
            return (path if path.is_absolute() else directory / path).resolve()
    return None


def default_cursor_path(cwd: Path) -> Path:
    repository_git_dir = git_directory(cwd)
    if repository_git_dir is not None:
        return repository_git_dir / "pr-completion" / "pr-watch-cursors.json"
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        base = Path(state_home).expanduser()
    elif os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        base = Path(os.environ["LOCALAPPDATA"]).expanduser()
    else:
        base = Path.home() / ".local" / "state"
    return (base / "pr-completion" / "pr-watch-cursors.json").resolve()


def configured_path(
    value: object,
    name: str,
    base: Path,
    allow_auto: bool,
    cwd: Path,
) -> Path | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise WatchError(f"{name} must be a non-empty path string or null")
    if allow_auto and value == "auto":
        return default_cursor_path(cwd)
    path = Path(value).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def build_settings(args: argparse.Namespace, cwd: Path) -> Settings:
    config_path: Path | None = None
    config: dict[str, object] = {}
    if not args.no_config:
        config_path = Path(args.config).expanduser().resolve() if args.config else find_config(cwd)
        if config_path is not None:
            config = read_config(config_path)
    policy_source = (
        "no-config"
        if args.no_config
        else "explicit-config"
        if args.config
        else "discovered-config"
        if config_path is not None
        else "defaults"
    )

    values = {**DEFAULTS, **config}
    overrides = {
        "mode": args.mode,
        "intervalSeconds": args.interval,
        "maxIntervalSeconds": args.max_interval,
        "timeoutSeconds": args.timeout,
        "jitter": args.jitter,
        "maxErrors": args.max_errors,
        "discover": args.discover,
        "maxDepth": args.max_depth,
        "checkPolicy": args.check_policy,
        "strictChangesRequested": args.strict_changes_requested,
        "requireApproval": args.require_approval,
        "cursorPath": args.cursor,
        "observationsPath": args.observations_file,
    }
    values.update({key: value for key, value in overrides.items() if value is not None})

    mode = str(values["mode"])
    discover = str(values["discover"])
    check_policy = str(values["checkPolicy"])
    if mode not in {"once", "until-actionable", "watch"}:
        raise WatchError("mode must be once, until-actionable, or watch")
    if discover not in {"current", "changed", "ahead", "open-pr"}:
        raise WatchError("discover must be current, changed, ahead, or open-pr")
    if check_policy not in {"all", "required"}:
        raise WatchError("checkPolicy must be all or required")
    if not isinstance(values["requireApproval"], bool):
        raise WatchError("requireApproval must be a boolean")

    interval = positive_float(values["intervalSeconds"], "intervalSeconds")
    max_interval = positive_float(values["maxIntervalSeconds"], "maxIntervalSeconds")
    timeout = positive_float(values["timeoutSeconds"], "timeoutSeconds", allow_zero=True)
    jitter = positive_float(values["jitter"], "jitter", allow_zero=True)
    if jitter > 1:
        raise WatchError("jitter must be between 0 and 1")
    if max_interval < interval:
        raise WatchError("maxIntervalSeconds must be at least intervalSeconds")

    config_base = config_path.parent if config_path is not None else cwd
    if args.target:
        targets = tuple(parse_target(value, cwd) for value in args.target)
    else:
        targets = config_targets(values.get("targets"), config_base)

    reviewers_value = args.reviewer if args.reviewer is not None else values["requiredReviewers"]
    if not isinstance(reviewers_value, list) or not all(
        isinstance(value, str) for value in reviewers_value
    ):
        raise WatchError("requiredReviewers must be an array of strings")
    reviewers = tuple(dict.fromkeys(normalize_login(value) for value in reviewers_value if value))

    strict_changes_requested = values["strictChangesRequested"]
    if not isinstance(strict_changes_requested, bool):
        raise WatchError("strictChangesRequested must be a boolean")

    cursor_path = configured_path(
        values["cursorPath"], "cursorPath", config_base, True, cwd
    )
    observations_path = configured_path(
        values["observationsPath"], "observationsPath", config_base, False, cwd
    )

    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
    await_merge_head = args.await_merge.strip() if args.await_merge else None
    if args.await_merge is not None and not await_merge_head:
        raise WatchError("--await-merge must be a non-empty head SHA")
    if await_merge_head is not None and len(targets) > 1:
        raise WatchError("--await-merge supports exactly one pull request target")
    if await_merge_head is not None and not targets and discover != "current":
        raise WatchError("--await-merge without --target requires --discover current")
    await_merge_mode = args.await_merge_mode
    if await_merge_head is not None and await_merge_mode is None:
        raise WatchError("--await-merge requires --await-merge-mode auto or queue")
    if await_merge_head is None and await_merge_mode is not None:
        raise WatchError("--await-merge-mode requires --await-merge HEAD_SHA")
    await_merge_since = (
        utc_timestamp(args.await_merge_since, "awaitMergeSince")
        if args.await_merge_since is not None
        else None
    )
    if await_merge_head is not None and await_merge_since is None:
        raise WatchError("--await-merge requires --await-merge-since TIMESTAMP")
    if await_merge_head is None and await_merge_since is not None:
        raise WatchError("--await-merge-since requires --await-merge HEAD_SHA")
    await_merge_grace_seconds = positive_float(
        args.await_merge_grace,
        "awaitMergeGraceSeconds",
        allow_zero=True,
    )
    if await_merge_grace_seconds > 60:
        raise WatchError("awaitMergeGraceSeconds must not exceed 60")
    if fixture is not None and args.cursor is None and "cursorPath" not in config:
        # Offline fixtures stay hermetic unless a cursor is explicitly under test.
        cursor_path = None
    return Settings(
        mode=mode,
        interval_seconds=interval,
        max_interval_seconds=max_interval,
        timeout_seconds=timeout,
        jitter=jitter,
        max_errors=positive_int(values["maxErrors"], "maxErrors"),
        discover=discover,
        max_depth=positive_int(values["maxDepth"], "maxDepth", allow_zero=True),
        check_policy=check_policy,
        policy_source=policy_source,
        config_path=config_path,
        strict_changes_requested=strict_changes_requested,
        required_reviewers=reviewers,
        require_approval=values["requireApproval"],
        targets=targets,
        cursor_path=cursor_path,
        observations_path=observations_path,
        await_merge_head=await_merge_head,
        await_merge_mode=await_merge_mode,
        await_merge_since=await_merge_since,
        await_merge_grace_seconds=await_merge_grace_seconds,
        fixture=fixture,
        pretty=args.pretty,
        verbose=args.verbose,
        output_path=Path(args.output).expanduser().resolve() if args.output else None,
    )


def marker_kind(marker: Path) -> str:
    if not marker.is_file():
        return "nested"
    try:
        content = marker.read_text(encoding="utf-8", errors="replace").replace("\\", "/")
    except OSError:
        return "nested"
    return "submodule" if "/modules/" in content else "nested"


def scan_repositories(root: Path, max_depth: int) -> list[Target]:
    root = root.resolve()
    repositories = [Target(root, None, "current")]
    for current_text, directories, _files in os.walk(root, topdown=True):
        current = Path(current_text)
        depth = len(current.relative_to(root).parts)
        if depth >= max_depth:
            directories[:] = []
            continue
        marker = current / ".git"
        if current != root and marker.exists():
            repositories.append(Target(current.resolve(), None, marker_kind(marker)))
            directories[:] = []
            continue
        directories[:] = [name for name in directories if name not in SKIP_DIRECTORIES]
    return repositories


def current_root(runner: Runner, cwd: Path) -> Path:
    result = runner.run(["git", "rev-parse", "--show-toplevel"], cwd)
    return Path(result.stdout.strip()).resolve()


def repository_changed(runner: Runner, path: Path) -> bool:
    result = runner.run(["git", "status", "--porcelain=v1", "--untracked-files=all"], path)
    return bool(result.stdout.strip())


def repository_ahead(runner: Runner, path: Path) -> bool:
    try:
        result = runner.run(["git", "rev-list", "--count", "@{upstream}..HEAD"], path)
    except WatchError:
        return False
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return False


def repository_has_open_pr(runner: Runner, path: Path) -> bool:
    try:
        runner.run(["gh", "pr", "view", "--json", "number"], path)
        return True
    except WatchError:
        return False


def discover_targets(settings: Settings, runner: Runner, cwd: Path) -> tuple[Target, ...]:
    if settings.targets:
        return settings.targets
    root = current_root(runner, cwd)
    if settings.discover == "current":
        return (Target(root, None, "current"),)

    candidates = scan_repositories(root, settings.max_depth)
    predicates = {
        "changed": repository_changed,
        "ahead": repository_ahead,
        "open-pr": repository_has_open_pr,
    }
    predicate = predicates[settings.discover]
    targets = tuple(target for target in candidates if predicate(runner, target.path))
    if not targets:
        raise WatchError(f"no repositories matched discovery mode {settings.discover}")
    return targets


def selector_args(selector: str | None) -> list[str]:
    return [] if selector is None else [selector]


def pull_request_auxiliary_state(
    runner: Runner, path: Path, repository: str, pr_number: int, hostname: str,
) -> dict[str, object]:
    owner, name = repository.split("/", 1)
    connections = {"reviewThreads": "threadCursor", "reviews": "reviewCursor",
                   "comments": "commentCursor", "reactions": "reactionCursor"}
    cursors: dict[str, str] = {}
    collected: dict[str, dict[str, object]] = {key: {} for key in connections}
    result: dict[str, object] = {}
    head: str | None = None
    while True:
        command = ["gh", "api", "graphql", "--hostname", hostname,
                   "-F", f"owner={owner}", "-F", f"name={name}",
                   "-F", f"number={pr_number}", "-f", f"query={THREAD_QUERY}"]
        for key, cursor in cursors.items():
            command.extend(["-f", f"{key}={cursor}"])
        page = runner.json(command, path)
        try:
            if page.get("errors"):
                raise WatchError(f"GitHub GraphQL errors: {page['errors']}")
            pr = page["data"]["repository"]["pullRequest"]
            current = pr["headRefOid"]
            if not isinstance(current, str) or not current:
                raise WatchError("GraphQL returned no PR head")
            if head is not None and head != current:
                raise WatchError("PR head changed during pagination", retryable=True)
            head = current
            result.update({key: pr.get(key) for key in ("headRefOid", "mergeQueueEntry", "isMergeQueueEnabled")})
            more = False
            for key, variable in connections.items():
                connection = pr[key]
                nodes = connection["nodes"]
                info = connection["pageInfo"]
                if not isinstance(nodes, list) or not isinstance(info.get("hasNextPage"), bool):
                    raise WatchError(f"malformed {key} connection")
                for node in nodes:
                    if not isinstance(node, dict) or not isinstance(node.get("id"), str):
                        raise WatchError(f"malformed {key} node")
                    collected[key][node["id"]] = node
                if info["hasNextPage"]:
                    cursor = info.get("endCursor")
                    if not isinstance(cursor, str) or not cursor or cursor == cursors.get(variable):
                        raise WatchError(f"invalid {key} pagination cursor")
                    cursors[variable] = cursor
                    more = True
        except (KeyError, TypeError, AttributeError) as error:
            raise WatchError("incomplete GitHub review/queue response") from error
        if not more:
            return {**result, **{key: list(nodes.values()) for key, nodes in collected.items()}}


def collect_target(target: Target, settings: Settings, runner: Runner) -> dict[str, object]:
    repo_value = getattr(runner, "repositories", {}).get(target.path)
    if repo_value is None:
        repo_value = runner.json(
            [
                "gh",
                "repo",
                "view",
                "--json",
                "nameWithOwner,url,mergeCommitAllowed,rebaseMergeAllowed,squashMergeAllowed",
            ],
            target.path,
        )
    if not isinstance(repo_value, dict) or not isinstance(repo_value.get("nameWithOwner"), str):
        raise WatchError("gh repo view did not return nameWithOwner")
    if hasattr(runner, "repositories"):
        runner.repositories[target.path] = repo_value
    repository = repo_value["nameWithOwner"]
    repository_url = repo_value.get("url")
    hostname = urlparse(repository_url).hostname if isinstance(repository_url, str) else None
    if hostname is None:
        raise WatchError("gh repo view did not return a repository URL with hostname")

    pr_value = runner.json(
        ["gh", "pr", "view", *selector_args(target.selector), "--json", PR_FIELDS],
        target.path,
    )
    if not isinstance(pr_value, dict) or not isinstance(pr_value.get("number"), int):
        raise WatchError("gh pr view did not return a PR number")

    if pr_value.get("state") == "MERGED":
        checks: object = []
        threads: list[dict[str, object]] = []
        merge_queue_entry: dict[str, object] | None = None
        is_merge_queue_enabled: bool | None = None
    else:
        check_args = [
            "gh",
            "pr",
            "checks",
            *selector_args(target.selector),
            "--json",
            CHECK_FIELDS,
        ]
        if settings.check_policy == "required":
            check_args.append("--required")
        checks = runner.json(
            check_args,
            target.path,
            allowed_codes=frozenset({0, 1, 8}),
        )
        auxiliary = pull_request_auxiliary_state(
            runner, target.path, repository, pr_value["number"], hostname
        )
        if auxiliary["headRefOid"] != pr_value.get("headRefOid"):
            raise WatchError("PR head changed while collecting checks/reviews", retryable=True)
        threads = auxiliary["reviewThreads"]
        pr_value["reviews"] = auxiliary["reviews"]
        pr_value["comments"] = auxiliary["comments"]
        pr_value["reactions"] = auxiliary["reactions"]
        merge_queue_entry = auxiliary["mergeQueueEntry"]
        is_merge_queue_enabled = auxiliary["isMergeQueueEnabled"]

    return {
        "path": str(target.path),
        "kind": target.kind,
        "repository": repository,
        "repositoryPolicy": {
            "mergeCommitAllowed": repo_value.get("mergeCommitAllowed"),
            "rebaseMergeAllowed": repo_value.get("rebaseMergeAllowed"),
            "squashMergeAllowed": repo_value.get("squashMergeAllowed"),
        },
        "pr": pr_value,
        "checks": checks,
        "reviewThreads": threads,
        "mergeQueueEntry": merge_queue_entry,
        "isMergeQueueEnabled": is_merge_queue_enabled,
    }


def latest_reviews(pr: dict[str, object]) -> dict[str, dict[str, object]]:
    reviews = pr.get("reviews", [])
    if not isinstance(reviews, list):
        return {}
    latest: dict[str, dict[str, object]] = {}
    for review in reviews:
        if not isinstance(review, dict):
            continue
        author = review.get("author")
        login = author.get("login") if isinstance(author, dict) else None
        if not isinstance(login, str):
            continue
        key = normalize_login(login)
        previous = latest.get(key)
        submitted = str(review.get("submittedAt") or "")
        previous_submitted = str(previous.get("submittedAt") or "") if previous else ""
        if previous is None or submitted >= previous_submitted:
            latest[key] = review
    return latest


def feedback_items(pr: dict[str, object]) -> list[dict[str, object]]:
    items = []
    for kind, field in (("review", "reviews"), ("comment", "comments")):
        for item in pr.get(field, []):
            body = item.get("body") or ""
            if not isinstance(body, str):
                raise WatchError(f"malformed {kind} body")
            if not body.strip():
                continue
            # Our bot commands are requests, never evidence of a completed review.
            if kind == "comment" and body.strip() in {
                "@coderabbitai approve", "@coderabbitai review", "@codex review",
            }:
                continue
            digest = hashlib.sha256(body.encode()).hexdigest()
            items.append({"id": item.get("id"), "kind": kind, "body": body,
                          "author": item.get("author"), "url": item.get("url"),
                          "token": f"{item.get('id')}:{digest}"})
    return items


def feedback_path(settings: Settings) -> Path | None:
    return settings.cursor_path.with_suffix(".feedback.json") if settings.cursor_path else None


def read_feedback(settings: Settings) -> dict[str, object]:
    path = feedback_path(settings)
    if path is None or not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
            raise ValueError("expected feedback evidence records")
        if any(v.get("verdict") not in {"addressed", "non-actionable"}
               or not isinstance(v.get("evidence"), str) or not v["evidence"].strip()
               for v in value.values()):
            raise ValueError("feedback acknowledgement lacks disposition or evidence")
        return value
    except (OSError, ValueError) as error:
        raise WatchError(f"invalid feedback evidence {path}: {error}") from error


def acknowledge_feedback(settings: Settings, value: dict[str, object], tokens: list[str],
                         verdict: str | None, evidence: str | None) -> None:
    if verdict not in {"addressed", "non-actionable"} or not evidence or not evidence.strip():
        raise WatchError("--ack-feedback requires --verdict and concrete --evidence")
    path = feedback_path(settings)
    if path is None:
        raise WatchError("--ack-feedback requires a durable --cursor")
    available = {item["token"]: target for target in value["targets"]
                 for item in target.get("reviews", {}).get("feedback", [])}
    if any(token not in available for token in tokens):
        raise WatchError("feedback changed or is absent; inspect the latest observation before acknowledging")
    records = read_feedback(settings)
    for token in tokens:
        records[token] = {"verdict": verdict, "evidence": evidence.strip(),
                          "headSha": available[token]["pr"]["headSha"], "at": utc_now()}
    atomic_json(path, records)


def compact_thread(thread: dict[str, object]) -> dict[str, object]:
    comments = thread.get("comments")
    nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
    last = nodes[-1] if isinstance(nodes, list) and nodes else {}
    author = last.get("author") if isinstance(last, dict) else None
    original = thread.get("originalComment", {}).get("nodes", [])
    return {
        "originalComment": original[0] if original else None,
        "id": thread.get("id"),
        "path": thread.get("path"),
        "line": thread.get("line") or thread.get("originalLine"),
        "isOutdated": bool(thread.get("isOutdated")),
        "author": author.get("login") if isinstance(author, dict) else None,
        "url": last.get("url") if isinstance(last, dict) else None,
        "body": last.get("body") if isinstance(last, dict) else None,
    }


def auto_merge_provenance(pr: dict[str, object]) -> dict[str, object] | None:
    """Structured read-only provenance for externally configured auto-merge.

    Any non-None autoMergeRequest (including an empty object) is treated as
    present external auto-merge configuration.
    """
    if "autoMergeRequest" not in pr:
        return None
    raw = pr.get("autoMergeRequest")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return {"enabled": True, "raw": raw}
    provenance: dict[str, object] = {"enabled": True}
    for key, value in raw.items():
        provenance[str(key)] = value
    # Normalize common GraphQL actor shape when present.
    enabled_by = provenance.get("enabledBy")
    if isinstance(enabled_by, dict) and "login" in enabled_by and "login" not in provenance:
        provenance["enabledByLogin"] = enabled_by.get("login")
    return provenance


def parse_checks(
    checks_value: object,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return (valid_checks, malformations). Never silently drop bad rows."""
    malformations: list[dict[str, object]] = []
    if not isinstance(checks_value, list):
        return [], [{"reason": "checks is not a list", "value_type": type(checks_value).__name__}]

    checks: list[dict[str, object]] = []
    for index, entry in enumerate(checks_value):
        if not isinstance(entry, dict):
            malformations.append(
                {
                    "index": index,
                    "reason": "check row is not an object",
                    "value_type": type(entry).__name__,
                }
            )
            continue
        name = entry.get("name")
        bucket_raw = entry.get("bucket")
        state_raw = entry.get("state")
        if not isinstance(name, str) or not name.strip():
            malformations.append(
                {
                    "index": index,
                    "reason": "missing or empty check name",
                    "name": name,
                }
            )
            continue
        if not isinstance(bucket_raw, str) or not bucket_raw.strip():
            malformations.append(
                {
                    "index": index,
                    "reason": "missing or empty check bucket",
                    "name": name,
                }
            )
            continue
        if not isinstance(state_raw, str) or not state_raw.strip():
            malformations.append(
                {
                    "index": index,
                    "reason": "missing or empty check state",
                    "name": name,
                    "bucket": bucket_raw,
                }
            )
            continue
        bucket = bucket_raw.strip().lower()
        state = state_raw.strip().upper()
        if bucket not in KNOWN_CHECK_BUCKETS:
            # Still keep the row so unknown-bucket handling can report it.
            checks.append({**entry, "bucket": bucket, "state": state, "name": name})
            continue
        if bucket == "unknown":
            checks.append({**entry, "bucket": bucket, "state": state, "name": name})
            continue
        allowed = BUCKET_ALLOWED_STATES.get(bucket, frozenset())
        if state not in allowed:
            malformations.append(
                {
                    "index": index,
                    "reason": "incoherent check bucket/state",
                    "name": name,
                    "bucket": bucket,
                    "state": state,
                    "allowedStates": sorted(allowed),
                }
            )
            # Do not classify into a bucket that would falsely look healthy.
            continue
        checks.append({**entry, "bucket": bucket, "state": state, "name": name})
    return checks, malformations


def is_verified_ready(
    *,
    head_sha: str,
    mergeable: str,
    merge_state: str,
    checks: Sequence[dict[str, object]],
    check_buckets: dict[str, list[dict[str, object]]],
    unresolved: Sequence[dict[str, object]],
    missing_reviewers: Sequence[str],
    review_decision: str,
    actions: Sequence[dict[str, object]],
    pending: Sequence[dict[str, object]],
    allowed_merge_states: frozenset[str] = READY_SAFE_MERGE_STATES,
    allow_empty_checks: bool = False,
) -> bool:
    """Explicit positive predicate for verified merge readiness. Fail closed otherwise."""
    if actions or pending:
        return False
    if not head_sha.strip():
        return False
    if mergeable not in READY_SAFE_MERGEABLE:
        return False
    if merge_state not in allowed_merge_states:
        return False
    if not checks and not allow_empty_checks:
        return False
    if unresolved or missing_reviewers:
        return False
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        return False
    for bucket, items in check_buckets.items():
        if not items:
            continue
        if bucket not in READY_SAFE_CHECK_BUCKETS:
            return False
        allowed = BUCKET_ALLOWED_STATES.get(bucket, frozenset())
        for check in items:
            state = str(check.get("state") or "").upper()
            if state not in allowed:
                return False
    return True


def classify_target(
    raw: dict[str, object],
    required_reviewers: Sequence[str],
    strict_changes_requested: bool,
    await_merge_head: str | None,
    await_merge_mode: str | None,
    allow_missing_landing_evidence: bool,
) -> dict[str, object]:
    pr = raw.get("pr")
    if not isinstance(pr, dict):
        raise WatchError("fixture or collector target is missing pr object")
    checks_value = raw.get("checks")
    checks, check_malformations = parse_checks(checks_value)
    threads_value = raw.get("reviewThreads", [])
    threads = [thread for thread in threads_value if isinstance(thread, dict)] if isinstance(threads_value, list) else []

    check_buckets: dict[str, list[dict[str, object]]] = {
        "pass": [],
        "fail": [],
        "pending": [],
        "skipping": [],
        "cancel": [],
        "unknown": [],
    }
    for check in checks:
        bucket = str(check.get("bucket") or "unknown").lower()
        check_buckets.setdefault(bucket, []).append(check)

    unresolved = [thread for thread in threads if not bool(thread.get("isResolved"))]
    head_raw = pr.get("headRefOid")
    head_sha = head_raw.strip() if isinstance(head_raw, str) else ""
    reviews = latest_reviews(pr)
    completed = {normalize_login(review.get("author", {}).get("login", ""))
                 for review in pr.get("reviews", [])
                 if isinstance(review.get("author"), dict)
                 and review.get("state") in {"APPROVED", "COMMENTED", "CHANGES_REQUESTED"}}
    reactions = list(pr.get("reactions", []))
    for comment in pr.get("comments", []):
        if str(comment.get("body", "")).strip().startswith("@codex review"):
            reactions.extend(comment.get("reactions", {}).get("nodes", []))
    completed.update(normalize_login(r["user"]["login"]) for r in reactions
                     if r.get("content") == "THUMBS_UP" and isinstance(r.get("user"), dict))
    missing_reviewers = [reviewer for reviewer in required_reviewers
                         if normalize_login(reviewer) not in completed]
    feedback = [item for item in feedback_items(pr)
                if item["token"] not in raw.get("handledFeedback", {})]
    mergeable = str(pr.get("mergeable") or "UNKNOWN").upper()
    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN").upper()
    required_only = raw.get("checkPolicy") == "required"
    allowed_merge_states = READY_SAFE_MERGE_STATES
    if required_only:
        # Native --required selection can exclude a failing optional status.
        allowed_merge_states |= {"UNSTABLE"}
    if raw.get("isMergeQueueEnabled") is True:
        # Queue admission uses GitHub's protected path after the known gates pass.
        allowed_merge_states |= {"BLOCKED"}
    review_decision = str(pr.get("reviewDecision") or "").upper()
    # A later COMMENTED review is not a new approval vote. GitHub decides whether
    # retained approvals satisfy its stale-review/CODEOWNER/last-push rules.
    votes = latest_reviews({"reviews": [r for r in pr.get("reviews", [])
                                        if r.get("state") != "COMMENTED"]})
    approved = review_decision == "APPROVED" or (
        not review_decision and any(r.get("state") == "APPROVED" for r in votes.values())
    )
    approval_required = bool(raw.get("requireApproval")) or review_decision in {
        "APPROVED", "REVIEW_REQUIRED", "CHANGES_REQUESTED",
    }
    approval_satisfied = approved or not approval_required
    failed_checks = check_buckets["fail"] + check_buckets["cancel"]
    pending_checks = check_buckets["pending"]
    review_running = any("coderabbit" in str(c.get("name", "")).lower()
                         or "codex" in str(c.get("name", "")).lower() for c in pending_checks)
    review_running = review_running or any(
        any(bot in str(c.get("name", c.get("context", ""))).lower() for bot in ("coderabbit", "codex"))
        and (c.get("status") in {"QUEUED", "IN_PROGRESS"} or c.get("state") == "PENDING")
        for c in pr.get("statusCheckRollup", [])
    )
    active_bots = {normalize_login(r["user"].get("login", "")) for r in reactions
                   if isinstance(r.get("user"), dict)} & {"coderabbitai", "chatgpt-codex-connector"}
    for reviewer in set(required_reviewers) | active_bots:
        activity = [r for r in reactions if isinstance(r.get("user"), dict)
                    and normalize_login(r["user"].get("login", "")) == normalize_login(reviewer)]
        last_eye = max((str(r.get("createdAt", "")) for r in activity if r.get("content") == "EYES"), default="")
        last_complete = max(
            [str(r.get("createdAt", "")) for r in activity if r.get("content") == "THUMBS_UP"]
            + [str(r.get("submittedAt", "")) for r in pr.get("reviews", [])
               if isinstance(r.get("author"), dict) and normalize_login(r["author"].get("login", "")) == normalize_login(reviewer)],
            default="",
        )
        review_running = review_running or last_eye > last_complete
    if "coderabbitai" in {normalize_login(r) for r in required_reviewers}:
        current_review = any(
            normalize_login(r.get("author", {}).get("login", "")) == "coderabbitai"
            and r.get("state") in {"APPROVED", "COMMENTED", "CHANGES_REQUESTED"}
            and (r.get("commit") or {}).get("oid") == head_sha
            for r in pr.get("reviews", []) if isinstance(r.get("author"), dict)
        )
        current_check = any(
            "coderabbit" in str(c.get("name", c.get("context", ""))).lower()
            and (c.get("conclusion") in {"SUCCESS", "NEUTRAL", "SKIPPED"}
                 or c.get("state") == "SUCCESS")
            for c in pr.get("statusCheckRollup", [])
        )
        review_running = review_running or not (current_review or current_check)
    actions: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []
    if ("reviewDecision" not in pr or not isinstance(pr["reviewDecision"], (str, type(None)))
        or review_decision not in {
        "", "APPROVED", "REVIEW_REQUIRED", "CHANGES_REQUESTED",
    }):
        pending.append({"type": "review_policy", "reason": "GitHub approval policy is unknown"})
    if review_running:
        pending.append({"type": "review_running", "nextAction": "wait for the automatic incremental review"})
    provenance = auto_merge_provenance(pr)
    merge_queue_entry_raw = raw.get("mergeQueueEntry")
    merge_queue_entry = (
        merge_queue_entry_raw if isinstance(merge_queue_entry_raw, dict) else None
    )

    if mergeable == "CONFLICTING" or merge_state == "DIRTY":
        actions.append({"type": "conflict"})
    if merge_state == "BEHIND":
        if pending_checks or review_running:
            pending.append({"type": "base_behind",
                            "reason": "wait for current checks and automatic review before updating the base"})
        else:
            actions.append({"type": "base_behind"})
    if failed_checks:
        actions.append(
            {
                "type": "ci_failure",
                "checks": [check.get("name") for check in failed_checks],
            }
        )
    if unresolved:
        actions.append(
            {
                "type": "review_threads",
                "count": len(unresolved),
                "threads": [compact_thread(thread) for thread in unresolved],
            }
        )
    if feedback:
        actions.append({"type": "review_feedback", "items": feedback})
    if review_decision == "CHANGES_REQUESTED" and (strict_changes_requested or unresolved):
        actions.append({"type": "changes_requested"})
    if not approval_satisfied:
        requests = [c for c in pr.get("comments", [])
                    if str(c.get("body", "")).strip().startswith("@coderabbitai approve")]
        last_request = max((str(c.get("createdAt", "")) for c in requests), default="")
        last_review = max((str(r.get("submittedAt", "")) for r in pr.get("reviews", [])
                           if isinstance(r.get("author"), dict)
                           and normalize_login(r["author"].get("login", "")) == "coderabbitai"), default="")
        coderabbit_available = "coderabbitai" in {
            normalize_login(r) for r in required_reviewers
        } or "coderabbitai" in completed
        if coderabbit_available and not (
            unresolved or feedback or missing_reviewers or review_running or failed_checks or pending_checks
        ):
            if not last_request or last_request < last_review:
                actions.append({"type": "approval_needed", "suggestedCommand": "@coderabbitai approve",
                                "reason": "reviews triaged; obtain an effective approval without another review pass"})
        pending.append({"type": "review_required", "reviewRunning": review_running,
                        "approvalRequestedAt": last_request or None,
                        "nextAction": "obtain the approval required by this repository; use only its configured reviewers"})

    if not head_sha:
        pending.append(
            {
                "type": "head_sha",
                "reason": "missing current head SHA",
            }
        )
    if check_malformations:
        pending.append(
            {
                "type": "malformed_checks",
                "count": len(check_malformations),
                "details": check_malformations,
            }
        )
    if not checks and not check_malformations and not required_only:
        pending.append(
            {
                "type": "checks",
                "checks": [],
                "reason": "empty or missing check output",
            }
        )
    if pending_checks:
        pending.append(
            {
                "type": "checks",
                "checks": [check.get("name") for check in pending_checks],
            }
        )
    # Fail closed on unknown buckets and any non-whitelisted check classification.
    for bucket, items in sorted(check_buckets.items()):
        if not items:
            continue
        if bucket not in KNOWN_CHECK_BUCKETS or bucket == "unknown":
            pending.append(
                {
                    "type": "unknown_checks",
                    "bucket": bucket,
                    "checks": [check.get("name") for check in items],
                }
            )
    if missing_reviewers:
        pending.append({"type": "required_reviewers", "reviewers": missing_reviewers})
    if mergeable == "UNKNOWN" or merge_state == "UNKNOWN":
        pending.append({"type": "mergeability"})
    elif mergeable not in READY_SAFE_MERGEABLE | {"CONFLICTING"}:
        pending.append({"type": "mergeability", "mergeable": mergeable})
    # Merge states outside the selected check/queue policy fail closed.
    if (
        merge_state not in allowed_merge_states
        and merge_state not in HANDLED_UNSAFE_MERGE_STATES
    ):
        pending.append({"type": "merge_state", "mergeStateStatus": merge_state})

    pr_state = str(pr.get("state") or "UNKNOWN")
    blocked_reason: str | None = None
    same_landing_head = await_merge_head is not None and head_sha == await_merge_head
    enrollment = provenance is not None or merge_queue_entry is not None
    if pr_state == "MERGED" or pr.get("mergedAt"):
        state, actions, pending = "merged", [], []
    elif pr_state != "OPEN" or bool(pr.get("isDraft")):
        state = "blocked"
        blocked_reason = "pull request is draft" if pr.get("isDraft") else f"pull request is {pr_state.lower()}"
    elif actions:
        state = "actionable"
    elif same_landing_head and not enrollment and not allow_missing_landing_evidence:
        state = "actionable"
        actions.append({"type": "landing_enrollment_missing", "mode": await_merge_mode,
                        "reason": "reconcile the absent landing request and current gates before retrying"})
    elif merge_queue_entry and (merge_queue_entry.get("state") == "UNMERGEABLE" or (
        isinstance(merge_queue_entry.get("headCommit"), dict)
        and merge_queue_entry["headCommit"].get("oid") not in {None, head_sha}
    )):
        state = "actionable"
        actions.append({"type": "landing_enrollment_rejected",
                        "queueState": merge_queue_entry.get("state"),
                        "reason": "queue rejected the entry or its head no longer matches"})
    elif enrollment or (same_landing_head and allow_missing_landing_evidence):
        state = "awaiting_merge"
        pending.append({"type": "merge_completion", "headSha": head_sha,
                        "mode": await_merge_mode, "evidencePending": not enrollment})
    elif pending:
        state = "pending"
    elif is_verified_ready(
        head_sha=head_sha,
        mergeable=mergeable,
        merge_state=merge_state,
        checks=checks,
        check_buckets=check_buckets,
        unresolved=unresolved,
        missing_reviewers=missing_reviewers,
        review_decision=review_decision,
        actions=actions,
        pending=pending,
        allowed_merge_states=allowed_merge_states,
        allow_empty_checks=required_only,
    ):
        state = "ready"
    elif merge_state == "BLOCKED":
        state = "blocked"
        blocked_reason = "merge is blocked without a reported pending gate"
    else:
        state = "blocked"
        blocked_reason = (
            "not verified merge-ready "
            f"(mergeStateStatus={merge_state}, mergeable={mergeable})"
        )

    if blocked_reason:
        actions.append({"type": "blocked", "reason": blocked_reason})

    return {
        "path": raw.get("path"),
        "kind": raw.get("kind", "explicit"),
        "repository": raw.get("repository"),
        "repositoryPolicy": raw.get("repositoryPolicy"),
        "state": state,
        "pr": {
            "number": pr.get("number"),
            "url": pr.get("url"),
            "state": pr_state,
            "headRefName": pr.get("headRefName"),
            "headSha": head_sha or pr.get("headRefOid"),
            "baseRefName": pr.get("baseRefName"),
            "baseSha": pr.get("baseRefOid"),
            "mergeable": mergeable,
            "mergeStateStatus": merge_state,
            "reviewDecision": review_decision or None,
            "mergedAt": pr.get("mergedAt"),
            "mergeCommit": pr.get("mergeCommit"),
            "autoMergeEnabled": provenance is not None,
            "autoMerge": provenance,
            "mergeQueueEntry": merge_queue_entry,
            "isMergeQueueEnabled": raw.get("isMergeQueueEnabled"),
        },
        "landingRequest": (
            {
                "expectedHead": await_merge_head,
                "currentHead": head_sha or None,
                "current": await_merge_head is not None and head_sha == await_merge_head,
                "mode": await_merge_mode,
            }
            if await_merge_head is not None
            else None
        ),
        "checks": {
            "total": len(checks),
            "pass": [check.get("name") for check in check_buckets["pass"]],
            "fail": [check.get("name") for check in failed_checks],
            "pending": [check.get("name") for check in pending_checks],
            "skipping": [check.get("name") for check in check_buckets["skipping"]],
            "unknown": [
                check.get("name")
                for bucket, items in check_buckets.items()
                if bucket not in READY_SAFE_CHECK_BUCKETS
                and bucket not in {"fail", "cancel", "pending"}
                for check in items
            ],
            "malformed": check_malformations,
        },
        "reviews": {
            "unresolvedThreadCount": len(unresolved),
            "requiredReviewers": list(required_reviewers),
            "missingRequiredReviewers": missing_reviewers,
            "completedReviewers": sorted(completed),
            "approved": approved,
            "approvalRequired": approval_required,
            "approvalSatisfied": approval_satisfied,
            "feedback": feedback,
        },
        "actions": actions,
        "pending": pending,
    }


def aggregate_state(targets: Sequence[dict[str, object]]) -> str:
    states = {str(target.get("state")) for target in targets}
    for state in ("actionable", "ready", "blocked", "pending", "awaiting_merge", "merged"):
        if state in states:
            return state
    return "blocked"


def snapshot(
    raw_targets: Sequence[dict[str, object]],
    settings: Settings,
    allow_missing_landing_evidence: bool = False,
) -> dict[str, object]:
    handled = read_feedback(settings)
    targets = [
        classify_target(
            {**target, "handledFeedback": handled, "checkPolicy": settings.check_policy,
             "requireApproval": settings.require_approval},
            settings.required_reviewers,
            settings.strict_changes_requested,
            settings.await_merge_head,
            settings.await_merge_mode,
            allow_missing_landing_evidence,
        )
        for target in raw_targets
    ]
    state = aggregate_state(targets)
    actions = [
        {"repository": target.get("repository"), **action}
        for target in targets
        for action in target.get("actions", [])
        if isinstance(action, dict)
    ]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "observedAt": utc_now(),
        "state": state,
        "policy": {
            "source": settings.policy_source,
            "configPath": (
                str(settings.config_path) if settings.config_path is not None else None
            ),
            "checkPolicy": settings.check_policy,
            "strictChangesRequested": settings.strict_changes_requested,
            "requiredReviewers": list(settings.required_reviewers),
            "requireApproval": settings.require_approval,
        },
        "targets": targets,
        "actions": actions,
        "errors": [],
    }


def load_fixture(
    path: Path,
    settings: Settings,
    allow_missing_landing_evidence: bool = False,
) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WatchError(f"fixture file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise WatchError(f"invalid fixture JSON {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("targets"), list):
        raise WatchError("fixture must contain a targets array")
    raw_targets = [target for target in value["targets"] if isinstance(target, dict)]
    return snapshot(raw_targets, settings, allow_missing_landing_evidence)


def collect_snapshot(
    settings: Settings,
    runner: Runner,
    cwd: Path,
    allow_missing_landing_evidence: bool = False,
) -> dict[str, object]:
    if settings.fixture is not None:
        return load_fixture(settings.fixture, settings, allow_missing_landing_evidence)
    targets = discover_targets(settings, runner, cwd)
    raw_targets, failures = [], []
    for target in targets:
        if target in runner.target_failures:
            failures.append(runner.target_failures[target])
            continue
        try:
            raw_targets.append(collect_target(target, settings, runner))
            runner.target_errors.pop(target, None)
        except WatchError as error:
            if len(targets) == 1:
                raise
            attempts = runner.target_errors.get(target, 0) + 1
            runner.target_errors[target] = attempts
            retry = error.retryable and attempts < settings.max_errors
            detail = {"type": "watch_retry" if retry else "watch_error",
                      "reason": str(error), "retryable": error.retryable}
            failure = {"path": str(target.path), "selector": target.selector,
                       "state": "pending" if retry else "blocked",
                       "actions": [] if retry else [detail],
                       "pending": [detail] if retry else []}
            failures.append(failure)
            if not retry:
                # Resume after recovery with a new runner; do not repeat a denied call
                # just because an independent PR still needs observation.
                runner.target_failures[target] = failure
    value = snapshot(raw_targets, settings, allow_missing_landing_evidence)
    value["targets"].extend(failures)
    value["state"] = aggregate_state(value["targets"])
    value["actions"].extend({"path": target["path"], **action}
                            for target in failures for action in target["actions"])
    return value


def error_snapshot(error: WatchError) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "observedAt": utc_now(),
        "state": "blocked",
        "targets": [],
        "actions": [{"type": "watch_error", "reason": str(error)}],
        "errors": [str(error)],
    }


def exit_code(state: str) -> int:
    """Return process status; the emitted JSON remains the state-machine signal."""
    if state == "blocked":
        return EXIT_BLOCKED
    return EXIT_OBSERVED


def emit(value: dict[str, object], pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, indent=2, sort_keys=True), flush=True)
    else:
        print(json.dumps(value, separators=(",", ":"), sort_keys=True), flush=True)


def snapshot_fingerprint(value: dict[str, object]) -> str:
    comparable = {key: item for key, item in value.items() if key not in {"observedAt", "waitingSince"}}
    return json.dumps(comparable, sort_keys=True, separators=(",", ":"))


def target_cursor_key(target: dict[str, object]) -> str | None:
    pr = target.get("pr")
    if not isinstance(pr, dict):
        return None
    url = pr.get("url")
    if isinstance(url, str) and url:
        return url
    repository = target.get("repository")
    number = pr.get("number")
    if isinstance(repository, str) and repository and isinstance(number, int):
        return f"{repository}#{number}"
    return None


def target_cursor_fingerprints(value: dict[str, object]) -> dict[str, str]:
    targets = value.get("targets")
    if not isinstance(targets, list):
        return {}
    fingerprints: dict[str, str] = {}
    for target in targets:
        if not isinstance(target, dict):
            continue
        key = target_cursor_key(target)
        if key is not None:
            fingerprints[key] = json.dumps(target, sort_keys=True, separators=(",", ":"))
    return fingerprints


def read_cursor(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": SCHEMA_VERSION, "targets": {}}
    except (OSError, json.JSONDecodeError) as error:
        raise WatchError(f"could not read cursor {path}: {error}") from error
    if not isinstance(value, dict) or value.get("version") != SCHEMA_VERSION:
        raise WatchError(f"cursor {path} must be a version {SCHEMA_VERSION} object")
    targets = value.get("targets")
    if not isinstance(targets, dict) or not all(
        isinstance(key, str) and isinstance(fingerprint, str)
        for key, fingerprint in targets.items()
    ):
        raise WatchError(f"cursor {path} has invalid target fingerprints")
    return value


def write_cursor(value: dict[str, object], path: Path | None) -> None:
    if path is None:
        return
    previous = read_cursor(path)
    previous["targets"].update(target_cursor_fingerprints(value))
    previous["observation"] = {"fingerprint": snapshot_fingerprint(value),
                               "waitingSince": value.get("waitingSince")}
    atomic_json(path, previous)


def atomic_json(path: Path, value: object) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except OSError as error:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise WatchError(f"could not write {path}: {error}") from error


def append_observation(value: dict[str, object], path: Path | None) -> None:
    if path is None:
        return
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as error:
        raise WatchError(f"could not append observations file {path}: {error}") from error


def emit_observation(value: dict[str, object], settings: Settings, announce: bool = True) -> None:
    append_observation(value, settings.observations_path)
    write_cursor(value, settings.cursor_path)
    if settings.output_path is not None:
        atomic_json(settings.output_path, value)
    if announce:
        emit(value, settings.pretty)


def sleep_duration(base: float, jitter: float) -> float:
    if jitter == 0:
        return base
    return max(0.0, base * random.uniform(1 - jitter, 1 + jitter))


def watch(settings: Settings, runner: Runner, cwd: Path) -> int:
    started = time.monotonic()
    runner.deadline = started + settings.timeout_seconds if settings.timeout_seconds else None
    consecutive_errors = 0
    last_fingerprint = None
    last_value = None
    unchanged_since = started
    waiting_since = utc_now()
    checkpoint = read_cursor(settings.cursor_path).get("observation", {}) if settings.cursor_path else {}
    if not isinstance(checkpoint, dict):
        raise WatchError("invalid cursor observation checkpoint")
    delay = settings.interval_seconds

    while True:
        now = time.monotonic()
        if runner.deadline is not None and now >= runner.deadline:
            value = {**(last_value or error_snapshot(WatchError("no successful observation"))),
                     "state": "timeout", "observedAt": utc_now(),
                     "errors": ["watch timeout reached; resume using the retained target state"]}
            emit_observation(value, settings)
            return EXIT_TIMEOUT
        try:
            elapsed = (datetime.now(timezone.utc) - settings.await_merge_since).total_seconds() if settings.await_merge_since else now - started
            value = collect_snapshot(settings, runner, cwd, allow_missing_landing_evidence=(
                settings.await_merge_head is not None and elapsed < settings.await_merge_grace_seconds
            ))
            consecutive_errors = 0
        except WatchError as error:
            consecutive_errors += 1
            if not error.retryable or consecutive_errors >= settings.max_errors:
                value = error_snapshot(error)
                if last_value is not None:
                    value["lastObservation"] = last_value
                emit_observation(value, settings)
                return EXIT_BLOCKED
            if settings.verbose:
                print(f"retryable watcher error: {error}", file=sys.stderr, flush=True)
            delay = min(settings.max_interval_seconds, delay * 2)
        else:
            fingerprint = snapshot_fingerprint(value)
            changed = fingerprint != last_fingerprint
            if changed:
                unchanged_since = now
                waiting_since = utc_now()
                if last_fingerprint is None and checkpoint.get("fingerprint") == fingerprint:
                    saved_since = checkpoint.get("waitingSince")
                    if saved_since:
                        if not isinstance(saved_since, str):
                            raise WatchError("cursor waitingSince must be a timestamp")
                        parsed = utc_timestamp(saved_since, "cursor waitingSince")
                        unchanged_since -= max(0, (datetime.now(timezone.utc) - parsed).total_seconds())
                        waiting_since = saved_since
                delay = settings.interval_seconds
            else:
                delay = min(settings.max_interval_seconds, delay * 1.5)
            state = str(value["state"])
            if state in {"pending", "awaiting_merge"} and now - unchanged_since >= 900:
                value["state"] = "actionable"
                value["actions"].append({"type": "diagnose_wait", "secondsUnchanged": int(now - unchanged_since),
                                         "reason": "inspect pending gates and cooldowns; do not blindly retrigger reviews"})
                state, changed = "actionable", True
                unchanged_since = now
                waiting_since = utc_now()
            value["waitingSince"] = waiting_since if state in {"pending", "awaiting_merge"} else None
            last_value = value
            if settings.mode == "once":
                emit_observation(value, settings)
                return exit_code(state)
            # Replay unhandled work after a restart; the cursor records observations,
            # never acknowledgements. Only in-process unchanged notifications are deduplicated.
            terminal = state not in {"pending", "awaiting_merge"}
            if settings.mode == "until-actionable" and terminal:
                emit_observation(value, settings)
                return exit_code(state)
            if changed:
                emit_observation(value, settings, announce=settings.mode == "watch")
                last_fingerprint = snapshot_fingerprint(value)
            if settings.mode == "watch" and (state == "merged" or all(
                target["state"] in {"blocked", "merged"} for target in value["targets"]
            )):
                return exit_code(state)
        remaining = max(0.0, runner.deadline - time.monotonic()) if runner.deadline else delay
        time.sleep(min(remaining, sleep_duration(delay, settings.jitter)))


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch GitHub PR state and emit deterministic JSON for agents.",
    )
    parser.add_argument("--config", help=f"JSON config path; otherwise search for {CONFIG_NAME}")
    parser.add_argument("--no-config", action="store_true", help="ignore discovered config files")
    parser.add_argument(
        "--target",
        action="append",
        help="repository path, optionally PATH=PR; repeat for multiple PRs",
    )
    parser.add_argument("--discover", choices=("current", "changed", "ahead", "open-pr"))
    parser.add_argument("--max-depth", type=int, help="nested repository scan depth")
    parser.add_argument("--mode", choices=("once", "until-actionable", "watch"))
    parser.add_argument("--interval", type=float, help="successful poll interval in seconds")
    parser.add_argument("--max-interval", type=float, help="maximum retry backoff in seconds")
    parser.add_argument("--timeout", type=float, help="total timeout in seconds; 0 disables")
    parser.add_argument("--jitter", type=float, help="poll jitter fraction from 0 to 1")
    parser.add_argument("--max-errors", type=int, help="consecutive retryable errors")
    parser.add_argument("--check-policy", choices=("all", "required"))
    parser.add_argument(
        "--strict-changes-requested",
        action="store_true",
        default=None,
        help="always treat CHANGES_REQUESTED as actionable",
    )
    parser.add_argument("--reviewer", action="append", help="required review participant (completion, not an approval vote); repeatable")
    parser.add_argument("--require-approval", action="store_true", default=None,
                        help="require an approving review even when GitHub has no approval rule")
    parser.add_argument("--cursor", help="durable observation cursor path")
    parser.add_argument(
        "--observations-file",
        help="append emitted observations as NDJSON at this path",
    )
    parser.add_argument("--output", help="atomic latest JSON observation file")
    parser.add_argument("--ack-feedback", action="append", help="acknowledge a reviewed ID:digest token; repeatable")
    parser.add_argument("--verdict", choices=("addressed", "non-actionable"))
    parser.add_argument("--evidence", help="pushed fix and validation, or concrete non-actionability reason")
    parser.add_argument("--fixture", help="offline raw snapshot fixture")
    parser.add_argument(
        "--await-merge",
        metavar="HEAD_SHA",
        help="after an approved landing request, wait for this exact PR head to merge",
    )
    parser.add_argument(
        "--await-merge-mode",
        choices=("auto", "queue"),
        help="approved landing mechanism whose enrollment must remain observable",
    )
    parser.add_argument(
        "--await-merge-since",
        metavar="TIMESTAMP",
        help="landing-request timestamp; keeps the evidence grace bounded across restarts",
    )
    parser.add_argument(
        "--await-merge-grace",
        type=float,
        default=60.0,
        help="seconds to allow GitHub enrollment evidence to appear (default: 60)",
    )
    parser.add_argument("--print-config", action="store_true", help="print resolved configuration and exit")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    parser.add_argument("--verbose", action="store_true", help="write retry diagnostics to stderr")
    return parser


def resolved_config(settings: Settings) -> dict[str, object]:
    directory = Path(__file__).resolve().parent
    manifest = directory.parents[2] / ".codex-plugin/plugin.json"
    return {
        "pluginVersion": json.loads(manifest.read_text()).get("version") if manifest.is_file() else None,
        "runtime": {str(path): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in (directory / "pr_watch.py", directory / "pr_land.py",
                                 *sorted(directory.parent.parent.glob("*/SKILL.md"))) if path.is_file()},
        "outputPath": str(settings.output_path) if settings.output_path else None,
        "version": SCHEMA_VERSION,
        "mode": settings.mode,
        "intervalSeconds": settings.interval_seconds,
        "maxIntervalSeconds": settings.max_interval_seconds,
        "timeoutSeconds": settings.timeout_seconds,
        "jitter": settings.jitter,
        "maxErrors": settings.max_errors,
        "discover": settings.discover,
        "maxDepth": settings.max_depth,
        "checkPolicy": settings.check_policy,
        "policySource": settings.policy_source,
        "configPath": (
            str(settings.config_path) if settings.config_path is not None else None
        ),
        "strictChangesRequested": settings.strict_changes_requested,
        "requiredReviewers": list(settings.required_reviewers),
        "requireApproval": settings.require_approval,
        "cursorPath": str(settings.cursor_path) if settings.cursor_path is not None else None,
        "observationsPath": (
            str(settings.observations_path) if settings.observations_path is not None else None
        ),
        "awaitMergeHead": settings.await_merge_head,
        "awaitMergeMode": settings.await_merge_mode,
        "awaitMergeSince": (
            settings.await_merge_since.isoformat().replace("+00:00", "Z")
            if settings.await_merge_since is not None
            else None
        ),
        "awaitMergeGraceSeconds": settings.await_merge_grace_seconds,
        "targets": [
            {
                "path": str(target.path),
                "pr": target.selector or "auto",
            }
            for target in settings.targets
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argument_parser()
    args = parser.parse_args(argv)
    cwd = Path.cwd()
    try:
        settings = build_settings(args, cwd)
        if args.print_config:
            emit(resolved_config(settings), settings.pretty)
            return EXIT_OBSERVED
        if args.ack_feedback:
            value = collect_snapshot(settings, Runner(), cwd)
            acknowledge_feedback(settings, value, args.ack_feedback, args.verdict, args.evidence)
            emit({"state": "feedback_recorded", "tokens": args.ack_feedback}, settings.pretty)
            return EXIT_OBSERVED
        if args.verdict or args.evidence:
            raise WatchError("--verdict and --evidence require --ack-feedback")
        return watch(settings, Runner(), cwd)
    except WatchError as error:
        emit(error_snapshot(error), bool(getattr(args, "pretty", False)))
        return EXIT_BLOCKED
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
