#!/usr/bin/env python3
"""Deterministic GitHub pull-request state watcher for agent workflows."""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
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
READY_SAFE_MERGE_STATES = frozenset({"CLEAN"})
READY_SAFE_MERGEABLE = frozenset({"MERGEABLE"})
# Merge states already mapped to conflict/base-behind actions or explicit pending.
HANDLED_UNSAFE_MERGE_STATES = frozenset({"DIRTY", "BEHIND", "UNKNOWN", "BLOCKED", "DRAFT"})

DEFAULTS = {
    "mode": "until-actionable",
    "intervalSeconds": 30.0,
    "maxIntervalSeconds": 120.0,
    "timeoutSeconds": 0.0,
    "jitter": 0.1,
    "maxErrors": 5,
    "discover": "current",
    "maxDepth": 4,
    "checkPolicy": "all",
    "requiredReviewers": [],
    "targets": [],
}

CONFIG_KEYS = {"version", *DEFAULTS.keys()}
PR_FIELDS = (
    "number,url,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,"
    "mergeable,mergeStateStatus,reviewDecision,autoMergeRequest,mergedAt,reviews"
)
CHECK_FIELDS = "name,state,bucket,link,workflow,startedAt,completedAt"
THREAD_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $endCursor) {
        nodes {
          id isResolved isOutdated path line originalLine
          comments(last: 1) {
            nodes { id author { login } createdAt url }
          }
        }
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
    required_reviewers: tuple[str, ...]
    targets: tuple[Target, ...]
    fixture: Path | None
    pretty: bool
    verbose: bool


class Runner:
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
            )
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
        empty_value: object | None = None,
    ) -> object:
        result = self.run(args, cwd, allowed_codes)
        output = result.stdout.strip()
        if not output:
            if empty_value is not None:
                return empty_value
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
    if number < 0 or (number == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise WatchError(f"{name} must be {qualifier}")
    return number


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


def build_settings(args: argparse.Namespace, cwd: Path) -> Settings:
    config_path: Path | None = None
    config: dict[str, object] = {}
    if not args.no_config:
        config_path = Path(args.config).expanduser().resolve() if args.config else find_config(cwd)
        if config_path is not None:
            config = read_config(config_path)

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

    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
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
        required_reviewers=reviewers,
        targets=targets,
        fixture=fixture,
        pretty=args.pretty,
        verbose=args.verbose,
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


def review_threads(
    runner: Runner,
    path: Path,
    repository: str,
    pr_number: int,
    hostname: str,
) -> list[dict[str, object]]:
    owner, name = repository.split("/", 1)
    value = runner.json(
        [
            "gh",
            "api",
            "graphql",
            "--hostname",
            hostname,
            "--paginate",
            "--slurp",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
            "-f",
            f"query={THREAD_QUERY}",
        ],
        path,
    )
    pages = value if isinstance(value, list) else [value]
    threads: list[dict[str, object]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        try:
            nodes = page["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        except (KeyError, TypeError):
            continue
        if isinstance(nodes, list):
            threads.extend(node for node in nodes if isinstance(node, dict))
    return threads


def collect_target(target: Target, settings: Settings, runner: Runner) -> dict[str, object]:
    repo_value = runner.json(["gh", "repo", "view", "--json", "nameWithOwner,url"], target.path)
    if not isinstance(repo_value, dict) or not isinstance(repo_value.get("nameWithOwner"), str):
        raise WatchError("gh repo view did not return nameWithOwner")
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
            empty_value=[],
        )
        threads = review_threads(runner, target.path, repository, pr_value["number"], hostname)

    return {
        "path": str(target.path),
        "kind": target.kind,
        "repository": repository,
        "pr": pr_value,
        "checks": checks if isinstance(checks, list) else [],
        "reviewThreads": threads,
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


def review_commit_oid(review: dict[str, object]) -> str | None:
    commit = review.get("commit")
    if isinstance(commit, dict) and isinstance(commit.get("oid"), str):
        return commit["oid"]
    return None


def compact_thread(thread: dict[str, object]) -> dict[str, object]:
    comments = thread.get("comments")
    nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
    last = nodes[-1] if isinstance(nodes, list) and nodes else {}
    author = last.get("author") if isinstance(last, dict) else None
    return {
        "id": thread.get("id"),
        "path": thread.get("path"),
        "line": thread.get("line") or thread.get("originalLine"),
        "isOutdated": bool(thread.get("isOutdated")),
        "author": author.get("login") if isinstance(author, dict) else None,
        "url": last.get("url") if isinstance(last, dict) else None,
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
    if checks_value is None:
        return [], []
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
) -> bool:
    """Explicit positive predicate for verified merge readiness. Fail closed otherwise."""
    if actions or pending:
        return False
    if not head_sha.strip():
        return False
    if mergeable not in READY_SAFE_MERGEABLE:
        return False
    if merge_state not in READY_SAFE_MERGE_STATES:
        return False
    if not checks:
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


def classify_target(raw: dict[str, object], required_reviewers: Sequence[str]) -> dict[str, object]:
    pr = raw.get("pr")
    if not isinstance(pr, dict):
        raise WatchError("fixture or collector target is missing pr object")
    checks_value = raw.get("checks", [])
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
    missing_reviewers: list[str] = []
    for reviewer in required_reviewers:
        review = reviews.get(normalize_login(reviewer))
        if (
            review is None
            or review.get("state") != "APPROVED"
            or review_commit_oid(review) != head_sha
            or not head_sha
        ):
            missing_reviewers.append(reviewer)

    failed_checks = check_buckets["fail"] + check_buckets["cancel"]
    pending_checks = check_buckets["pending"]
    actions: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []
    mergeable = str(pr.get("mergeable") or "UNKNOWN")
    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN")
    review_decision = str(pr.get("reviewDecision") or "")
    provenance = auto_merge_provenance(pr)

    if mergeable == "CONFLICTING" or merge_state == "DIRTY":
        actions.append({"type": "conflict"})
    if merge_state == "BEHIND":
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
    if review_decision == "CHANGES_REQUESTED":
        actions.append({"type": "changes_requested"})

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
    if not checks and not check_malformations:
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
    if review_decision == "REVIEW_REQUIRED":
        pending.append({"type": "review_required"})
    if missing_reviewers:
        pending.append({"type": "required_reviewers", "reviewers": missing_reviewers})
    if mergeable == "UNKNOWN" or merge_state == "UNKNOWN":
        pending.append({"type": "mergeability"})
    elif mergeable not in READY_SAFE_MERGEABLE | {"CONFLICTING"}:
        pending.append({"type": "mergeability", "mergeable": mergeable})
    # Non-whitelisted merge states (UNSTABLE, HAS_HOOKS, novel values) fail closed.
    if (
        merge_state not in READY_SAFE_MERGE_STATES
        and merge_state not in HANDLED_UNSAFE_MERGE_STATES
    ):
        pending.append({"type": "merge_state", "mergeStateStatus": merge_state})

    pr_state = str(pr.get("state") or "UNKNOWN")
    blocked_reason: str | None = None
    if pr_state == "MERGED" or pr.get("mergedAt"):
        state = "merged"
        actions = []
        pending = []
    elif pr_state != "OPEN":
        state = "blocked"
        blocked_reason = f"pull request is {pr_state.lower()}"
    elif bool(pr.get("isDraft")):
        state = "blocked"
        blocked_reason = "pull request is draft"
    elif provenance is not None:
        # Externally configured auto-merge is terminal and read-only: report provenance,
        # clear dispatch actions, and do not wait on or repair remaining gates.
        state = "auto_merge"
        actions = []
    elif actions:
        state = "actionable"
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
            "autoMergeEnabled": provenance is not None,
            "autoMerge": provenance,
        },
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
        },
        "actions": actions,
        "pending": pending,
    }


def aggregate_state(targets: Sequence[dict[str, object]]) -> str:
    states = [str(target.get("state")) for target in targets]
    if not states:
        return "blocked"
    if "blocked" in states:
        return "blocked"
    if "actionable" in states:
        return "actionable"
    if "pending" in states:
        return "pending"
    if all(state == "merged" for state in states):
        return "merged"
    if all(state in {"merged", "auto_merge"} for state in states):
        return "auto_merge"
    return "ready"


def snapshot(raw_targets: Sequence[dict[str, object]], settings: Settings) -> dict[str, object]:
    targets = [classify_target(target, settings.required_reviewers) for target in raw_targets]
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
        "targets": targets,
        "actions": actions,
        "errors": [],
    }


def load_fixture(path: Path, settings: Settings) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise WatchError(f"fixture file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise WatchError(f"invalid fixture JSON {path}: {error}") from error
    if not isinstance(value, dict) or not isinstance(value.get("targets"), list):
        raise WatchError("fixture must contain a targets array")
    raw_targets = [target for target in value["targets"] if isinstance(target, dict)]
    return snapshot(raw_targets, settings)


def collect_snapshot(settings: Settings, runner: Runner, cwd: Path) -> dict[str, object]:
    if settings.fixture is not None:
        return load_fixture(settings.fixture, settings)
    targets = discover_targets(settings, runner, cwd)
    raw_targets = [collect_target(target, settings, runner) for target in targets]
    return snapshot(raw_targets, settings)


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
    comparable = {key: item for key, item in value.items() if key != "observedAt"}
    return json.dumps(comparable, sort_keys=True, separators=(",", ":"))


def sleep_duration(base: float, jitter: float) -> float:
    if jitter == 0:
        return base
    return max(0.0, base * random.uniform(1 - jitter, 1 + jitter))


def watch(settings: Settings, runner: Runner, cwd: Path) -> int:
    started = time.monotonic()
    consecutive_errors = 0
    last_fingerprint: str | None = None
    error_delay = settings.interval_seconds

    while True:
        if settings.timeout_seconds and time.monotonic() - started >= settings.timeout_seconds:
            timeout_value = error_snapshot(WatchError("watch timeout reached"))
            timeout_value["state"] = "timeout"
            emit(timeout_value, settings.pretty)
            return EXIT_TIMEOUT
        try:
            value = collect_snapshot(settings, runner, cwd)
            consecutive_errors = 0
            error_delay = settings.interval_seconds
        except WatchError as error:
            consecutive_errors += 1
            if not error.retryable or consecutive_errors >= settings.max_errors:
                emit(error_snapshot(error), settings.pretty)
                return EXIT_BLOCKED
            if settings.verbose:
                print(f"retryable watcher error: {error}", file=sys.stderr, flush=True)
            time.sleep(sleep_duration(error_delay, settings.jitter))
            error_delay = min(settings.max_interval_seconds, error_delay * 2)
            continue

        fingerprint = snapshot_fingerprint(value)
        if settings.mode != "watch" or fingerprint != last_fingerprint:
            emit(value, settings.pretty)
            last_fingerprint = fingerprint

        state = str(value["state"])
        if settings.fixture is not None or settings.mode == "once":
            return exit_code(state)
        if settings.mode == "until-actionable" and state != "pending":
            return exit_code(state)
        if settings.mode == "watch" and state in {"ready", "auto_merge", "merged", "blocked"}:
            return exit_code(state)
        time.sleep(sleep_duration(settings.interval_seconds, settings.jitter))


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
    parser.add_argument("--reviewer", action="append", help="required reviewer login; repeatable")
    parser.add_argument("--fixture", help="offline raw snapshot fixture; always observes once")
    parser.add_argument("--print-config", action="store_true", help="print resolved configuration and exit")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    parser.add_argument("--verbose", action="store_true", help="write retry diagnostics to stderr")
    return parser


def resolved_config(settings: Settings) -> dict[str, object]:
    return {
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
        "requiredReviewers": list(settings.required_reviewers),
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
        return watch(settings, Runner(), cwd)
    except WatchError as error:
        emit(error_snapshot(error), bool(getattr(args, "pretty", False)))
        return EXIT_BLOCKED
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
