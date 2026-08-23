#!/usr/bin/env python3
"""Guarded, per-PR landing request for PR Completion.

This is the only shipped helper allowed to mutate GitHub merge state. It first
re-runs the read-only watcher, requires the exact authorized head to remain
verified ready, and then invokes GitHub CLI without admin or protection bypass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Sequence


EXIT_OK = 0
EXIT_BLOCKED = 20
WARNING = "This landing request may merge the pull request immediately."
REPOSITORY_READINESS_SCHEMA = "pr_completion.repository_readiness.v1"
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
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
        )
    except FileNotFoundError as error:
        raise LandingError(f"required command not found: {args[0]}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no details"
        raise LandingError(f"{' '.join(args[:3])} failed ({result.returncode}): {detail}")
    return result


def watcher_snapshot(
    repository: Path,
    selector: str | None,
    fixture: Path | None,
    config: Path | None,
    no_config: bool,
    reviewers: Sequence[str],
    check_policy: str | None,
    strict_changes_requested: bool,
) -> dict[str, object]:
    watcher = Path(__file__).with_name("pr_watch.py")
    with tempfile.TemporaryDirectory(prefix="pr-completion-land-") as temporary:
        command = [
            sys.executable,
            str(watcher),
            "--mode",
            "once",
            "--cursor",
            str(Path(temporary) / "cursor.json"),
        ]
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
    return landing_target_policy(target, expected_head, mode, method)


def landing_target_policy(
    target: dict[str, object],
    expected_head: str,
    mode: str,
    method: str | None,
) -> dict[str, object]:
    """Recheck exact head plus queue and merge-method policy for one target."""

    pr = target.get("pr")
    if not isinstance(pr, dict):
        raise LandingError("landing target is missing its pull request state")
    current_head = pr.get("headSha")
    if current_head != expected_head:
        raise LandingError(
            f"landing authorization is stale (expected {expected_head}, current {current_head})"
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


def exact_object(value: object, label: str, keys: set[str]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LandingError(f"repository readiness {label} must be an object")
    actual = {str(key) for key in value}
    if actual != keys:
        raise LandingError(f"repository readiness {label} fields are invalid")
    return value


def normalized_repository_readiness_config(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    raw = exact_object(
        value,
        "provider config",
        {
            "provider",
            "providerSchema",
            "verifier",
            "maxAgeSeconds",
            "blockOnExecutedCheckFailure",
        },
    )
    provider = raw.get("provider")
    provider_schema = raw.get("providerSchema")
    verifier = raw.get("verifier")
    max_age = raw.get("maxAgeSeconds")
    safe_identifier = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
    if (
        not isinstance(provider, str)
        or not 1 <= len(provider) <= 96
        or safe_identifier.fullmatch(provider) is None
    ):
        raise LandingError("repository readiness provider is invalid")
    if (
        not isinstance(provider_schema, str)
        or not 1 <= len(provider_schema) <= 128
        or safe_identifier.fullmatch(provider_schema) is None
    ):
        raise LandingError("repository readiness provider schema is invalid")
    if not isinstance(verifier, str) or not verifier:
        raise LandingError("repository readiness verifier is invalid")
    verifier_path = PurePosixPath(verifier)
    if (
        "\\" in verifier
        or verifier_path.is_absolute()
        or verifier_path.suffix != ".py"
        or any(part in {"", ".", ".."} for part in verifier_path.parts)
    ):
        raise LandingError("repository readiness verifier path is not bounded")
    if isinstance(max_age, bool) or not isinstance(max_age, int) or not 1 <= max_age <= 604800:
        raise LandingError("repository readiness maxAgeSeconds is invalid")
    if raw.get("blockOnExecutedCheckFailure") is not True:
        raise LandingError("repository readiness must block executed GitHub check failures")
    return {
        "provider": provider,
        "providerSchema": provider_schema,
        "verifier": verifier_path.as_posix(),
        "maxAgeSeconds": max_age,
        "blockOnExecutedCheckFailure": True,
    }


def attestation_digest(value: dict[str, object]) -> str:
    payload = dict(value)
    payload.pop("attestationDigest", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def readiness_timestamp(value: object, now: datetime) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value:
        raise LandingError("repository readiness evaluatedAt is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise LandingError("repository readiness evaluatedAt is invalid") from error
    if parsed.tzinfo is None:
        raise LandingError("repository readiness evaluatedAt must include a timezone")
    evaluated = parsed.astimezone(timezone.utc)
    if evaluated > now:
        raise LandingError("repository readiness evaluatedAt cannot be in the future")
    return value, evaluated


def validated_repository_readiness(
    value: object,
    *,
    config: dict[str, object],
    target: dict[str, object],
    expected_head: str,
    candidate_tree: str,
    now: datetime | None = None,
) -> tuple[dict[str, object], str]:
    """Validate the generic normalized artifact emitted by the configured verifier."""

    raw = exact_object(
        value,
        "attestation",
        {
            "schema",
            "version",
            "provider",
            "repository",
            "pullRequest",
            "candidateHeadSha",
            "candidateTreeSha",
            "baseSha",
            "readinessResult",
            "sourceValidationStatus",
            "evidenceDigest",
            "evaluatedAt",
            "reviewResult",
            "authority",
            "attestationDigest",
        },
    )
    if raw.get("schema") != REPOSITORY_READINESS_SCHEMA or raw.get("version") != 1:
        raise LandingError("repository readiness schema is unknown")
    provider = exact_object(raw.get("provider"), "provider", {"id", "schema"})
    if (
        provider.get("id") != config["provider"]
        or provider.get("schema") != config["providerSchema"]
    ):
        raise LandingError("repository readiness provider identity is not configured")
    pr = target.get("pr")
    if not isinstance(pr, dict):
        raise LandingError("repository readiness target PR state is missing")
    if raw.get("repository") != target.get("repository"):
        raise LandingError("repository readiness repository identity changed")
    if raw.get("pullRequest") != pr.get("number"):
        raise LandingError("repository readiness pull request identity changed")
    head = raw.get("candidateHeadSha")
    tree = raw.get("candidateTreeSha")
    base = raw.get("baseSha")
    if (
        not isinstance(head, str)
        or SHA_PATTERN.fullmatch(head) is None
        or head != expected_head
    ):
        raise LandingError("repository readiness candidate head changed")
    if (
        not isinstance(tree, str)
        or SHA_PATTERN.fullmatch(tree) is None
        or tree != candidate_tree
    ):
        raise LandingError("repository readiness candidate tree changed")
    if (
        not isinstance(base, str)
        or SHA_PATTERN.fullmatch(base) is None
        or base != pr.get("baseSha")
    ):
        raise LandingError("repository readiness base changed")
    if raw.get("readinessResult") != "ready":
        raise LandingError("repository readiness result is not ready")
    if raw.get("sourceValidationStatus") != "PASS":
        raise LandingError("repository readiness source validation is not PASS")
    evidence_digest = raw.get("evidenceDigest")
    if (
        not isinstance(evidence_digest, str)
        or DIGEST_PATTERN.fullmatch(evidence_digest) is None
    ):
        raise LandingError("repository readiness evidence digest is invalid")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    _timestamp, evaluated = readiness_timestamp(raw.get("evaluatedAt"), current_time)
    max_age = config["maxAgeSeconds"]
    assert isinstance(max_age, int)
    if (current_time - evaluated).total_seconds() > max_age:
        raise LandingError("repository readiness attestation is stale")
    review_result = raw.get("reviewResult")
    if review_result is not None and (
        not isinstance(review_result, str) or not review_result or len(review_result) > 128
    ):
        raise LandingError("repository readiness reviewResult is invalid")
    authority = exact_object(
        raw.get("authority"),
        "authority",
        {"operatorApprovalGranted", "mergeAuthorityGranted"},
    )
    if authority != {"operatorApprovalGranted": False, "mergeAuthorityGranted": False}:
        raise LandingError("repository readiness cannot grant operator or merge authority")
    supplied_attestation_digest = raw.get("attestationDigest")
    computed_digest = attestation_digest(raw)
    if supplied_attestation_digest != computed_digest:
        raise LandingError("repository readiness attestation digest mismatch")
    return dict(raw), computed_digest


def verified_repository_files(
    repository: Path,
    expected_head: str,
    base_sha: object,
    config_path: object,
    verifier_relative: str,
) -> tuple[str, Path]:
    """Bind provider policy to the exact checkout and its pre-existing base."""

    repository = repository.resolve()
    if not isinstance(base_sha, str) or SHA_PATTERN.fullmatch(base_sha) is None:
        raise LandingError("repository readiness base commit is unavailable")
    local_head = run(["git", "rev-parse", "HEAD"], repository).stdout.strip()
    if local_head != expected_head:
        raise LandingError("repository readiness requires an exact-head local checkout")
    candidate_tree = run(["git", "rev-parse", "HEAD^{tree}"], repository).stdout.strip()
    if SHA_PATTERN.fullmatch(candidate_tree) is None:
        raise LandingError("repository readiness candidate tree is unavailable")
    config_file = repository / ".pr-completion.json"
    expected_config = config_file.resolve()
    if not isinstance(config_path, str) or Path(config_path).resolve() != expected_config:
        raise LandingError("repository readiness requires the repository-root config")
    verifier_file = repository.joinpath(*PurePosixPath(verifier_relative).parts)
    verifier = verifier_file.resolve()
    try:
        verifier.relative_to(repository)
    except ValueError as error:
        raise LandingError("repository readiness verifier escapes the repository") from error
    relative_verifier_parts = verifier_file.relative_to(repository).parts
    verifier_components = [
        repository.joinpath(*relative_verifier_parts[:index])
        for index in range(1, len(relative_verifier_parts) + 1)
    ]
    if (
        config_file.is_symlink()
        or any(component.is_symlink() for component in verifier_components)
        or not config_file.is_file()
        or not verifier.is_file()
    ):
        raise LandingError("repository readiness config or verifier is not a regular tracked file")
    for relative in (".pr-completion.json", verifier_relative):
        run(["git", "ls-files", "--error-unmatch", "--", relative], repository)
        candidate_entry = run(
            ["git", "ls-tree", expected_head, "--", relative], repository
        ).stdout.strip()
        base_entry = run(
            ["git", "ls-tree", base_sha, "--", relative], repository
        ).stdout.strip()
        if not candidate_entry or candidate_entry != base_entry:
            raise LandingError(
                "repository readiness provider config and verifier must pre-exist unchanged on the PR base"
            )
    run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD",
            "--",
            ".pr-completion.json",
            verifier_relative,
        ],
        repository,
    )
    return candidate_tree, verifier


def repository_observation(repository: Path) -> tuple[str, str, str]:
    """Capture enough local state to prove the verifier was observational."""

    head = run(["git", "rev-parse", "HEAD"], repository).stdout.strip()
    tree = run(["git", "rev-parse", "HEAD^{tree}"], repository).stdout.strip()
    status = run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"], repository
    ).stdout
    return head, tree, status


def invoke_repository_verifier(
    repository: Path,
    verifier: Path,
    artifact_path: Path,
) -> object:
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise LandingError("repository readiness artifact must be a regular file")
    result = run(
        [sys.executable, "-I", "-B", str(verifier), "--artifact", str(artifact_path.resolve())],
        repository,
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LandingError("repository readiness verifier returned invalid JSON") from error


def repository_readiness_target(
    snapshot: dict[str, object],
    repository: Path,
    artifact_path: Path,
    expected_head: str,
    mode: str,
    method: str | None,
    policy: dict[str, object],
    now: datetime | None = None,
) -> tuple[dict[str, object], dict[str, object], str]:
    """Accept source qualification only through a configured read-only verifier."""

    config = normalized_repository_readiness_config(policy.get("repositoryReadiness"))
    if config is None:
        raise LandingError("repository readiness is not configured")
    target = repository_readiness_live_target(snapshot, expected_head, mode, method)
    pr = target["pr"]
    assert isinstance(pr, dict)
    candidate_tree, verifier = verified_repository_files(
        repository,
        expected_head,
        pr.get("baseSha"),
        policy.get("configPath"),
        str(config["verifier"]),
    )
    before_verifier = repository_observation(repository)
    verifier_output = invoke_repository_verifier(repository, verifier, artifact_path)
    if repository_observation(repository) != before_verifier:
        raise LandingError("repository readiness verifier changed the local repository")
    readiness, digest = validated_repository_readiness(
        verifier_output,
        config=config,
        target=target,
        expected_head=expected_head,
        candidate_tree=candidate_tree,
        now=now,
    )
    return target, readiness, digest


def repository_readiness_live_target(
    snapshot: dict[str, object],
    expected_head: str,
    mode: str,
    method: str | None,
) -> dict[str, object]:
    """Reconcile the alternate readiness path against fresh GitHub state."""

    targets = snapshot.get("targets")
    if (
        not isinstance(targets, list)
        or len(targets) != 1
        or not isinstance(targets[0], dict)
    ):
        raise LandingError("repository readiness requires exactly one pull request target")
    target = targets[0]
    if snapshot.get("state") not in {"pending", "ready"} or target.get("state") not in {
        "pending",
        "ready",
    }:
        raise LandingError("repository readiness cannot bypass a watcher blocker")
    pr = target.get("pr")
    if not isinstance(pr, dict) or pr.get("state") != "OPEN":
        raise LandingError("repository readiness requires an open pull request")
    if pr.get("headSha") != expected_head:
        raise LandingError("repository readiness authorization is stale")
    if pr.get("mergeable") != "MERGEABLE" or pr.get("mergeStateStatus") != "CLEAN":
        raise LandingError("repository readiness merge policy is not safely known")
    if pr.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        raise LandingError("repository readiness cannot bypass a review gate")
    actions = target.get("actions")
    if actions != []:
        raise LandingError("repository readiness cannot bypass watcher actions")
    pending = target.get("pending")
    if not isinstance(pending, list):
        raise LandingError("repository readiness watcher pending state is malformed")
    for item in pending:
        if (
            not isinstance(item, dict)
            or item.get("type") != "checks"
            or item.get("checks") != []
        ):
            raise LandingError("repository readiness cannot bypass a non-absent-check pending gate")
    checks = target.get("checks")
    if not isinstance(checks, dict):
        raise LandingError("repository readiness GitHub check state is unknown")
    for key in ("fail", "pending", "unknown", "malformed"):
        if checks.get(key) != []:
            raise LandingError("repository readiness has contradictory or ambiguous GitHub checks")
    reviews = target.get("reviews")
    if not isinstance(reviews, dict):
        raise LandingError("repository readiness review state is unknown")
    if (
        reviews.get("unresolvedThreadCount") != 0
        or reviews.get("missingRequiredReviewers") != []
    ):
        raise LandingError("repository readiness cannot bypass review threads or reviewers")
    return landing_target_policy(target, expected_head, mode, method)


def readiness_policy(snapshot: dict[str, object]) -> tuple[dict[str, object], str]:
    raw = snapshot.get("policy")
    if not isinstance(raw, dict):
        raise LandingError("read-only watcher did not report its resolved readiness policy")
    policy = {
        "source": raw.get("source"),
        "configPath": raw.get("configPath"),
        "checkPolicy": raw.get("checkPolicy"),
        "strictChangesRequested": raw.get("strictChangesRequested"),
        "requiredReviewers": raw.get("requiredReviewers"),
        "repositoryReadiness": normalized_repository_readiness_config(
            raw.get("repositoryReadiness")
        ),
    }
    if policy["source"] not in {
        "no-config",
        "explicit-config",
        "discovered-config",
        "defaults",
    }:
        raise LandingError("resolved readiness policy source is invalid")
    config_path = policy["configPath"]
    if policy["source"] in {"explicit-config", "discovered-config"}:
        if not isinstance(config_path, str) or not config_path:
            raise LandingError("resolved readiness config path is invalid")
    elif config_path is not None:
        raise LandingError("resolved readiness policy unexpectedly reports a config path")
    if policy["checkPolicy"] not in {"all", "required"}:
        raise LandingError("resolved readiness check policy is invalid")
    if not isinstance(policy["strictChangesRequested"], bool):
        raise LandingError("resolved changes-requested policy is invalid")
    reviewers = policy["requiredReviewers"]
    if not isinstance(reviewers, list) or not all(isinstance(item, str) for item in reviewers):
        raise LandingError("resolved required-reviewer policy is invalid")
    digest = hashlib.sha256(
        json.dumps(policy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return policy, digest


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


def plan_payload(
    target: dict[str, object],
    head: str,
    mode: str,
    method: str | None,
    policy: dict[str, object],
    policy_digest: str,
    repository_readiness: dict[str, object] | None = None,
    repository_readiness_digest: str | None = None,
) -> dict[str, object]:
    pr = target["pr"]
    assert isinstance(pr, dict)
    payload: dict[str, object] = {
        "schemaVersion": 1,
        "state": "confirmation_required",
        "requiresConfirmation": True,
        "warning": WARNING,
        "repository": target.get("repository"),
        "pr": {"number": pr.get("number"), "url": pr.get("url")},
        "headSha": head,
        "mode": mode,
        "method": method,
        "readinessPolicy": policy,
        "readinessPolicyDigest": policy_digest,
    }
    if repository_readiness is not None:
        payload["readinessSource"] = "repository"
        payload["repositoryReadiness"] = repository_readiness
        payload["repositoryReadinessDigest"] = repository_readiness_digest
    return payload


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Request an explicitly approved PR landing action for an exact head SHA.",
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
    parser.add_argument("--head", required=True, help="exact authorized pull request head SHA")
    parser.add_argument("--mode", required=True, choices=("auto", "queue"))
    parser.add_argument("--method", choices=("merge", "squash", "rebase"))
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="perform the landing request after a fresh exact-head readiness check",
    )
    parser.add_argument(
        "--policy-digest",
        help="resolved readiness-policy digest emitted by the confirmation plan",
    )
    parser.add_argument(
        "--repository-readiness",
        help="artifact consumed only by the explicitly configured repository verifier",
    )
    parser.add_argument(
        "--repository-readiness-digest",
        help="repository-readiness digest emitted by the confirmation plan",
    )
    parser.add_argument("--fixture", help="offline watcher fixture; planning only")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    repository = Path(args.repo).expanduser().resolve()
    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
    config = Path(args.config).expanduser().resolve() if args.config else None
    repository_readiness_path = (
        Path(args.repository_readiness).expanduser()
        if args.repository_readiness
        else None
    )
    reviewers = tuple(args.reviewer or ())
    try:
        if args.confirm and fixture is not None:
            raise LandingError("offline fixtures cannot authorize a landing mutation")
        if args.repository_readiness_digest and repository_readiness_path is None:
            raise LandingError(
                "--repository-readiness-digest requires --repository-readiness"
            )
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
        )
        policy, policy_digest = readiness_policy(snapshot)
        repository_readiness: dict[str, object] | None = None
        repository_readiness_digest: str | None = None
        if repository_readiness_path is None:
            target = verified_target(snapshot, head, args.mode, args.method)
        else:
            target, repository_readiness, repository_readiness_digest = (
                repository_readiness_target(
                    snapshot,
                    repository,
                    repository_readiness_path,
                    head,
                    args.mode,
                    args.method,
                    policy,
                )
            )
        plan = plan_payload(
            target,
            head,
            args.mode,
            args.method,
            policy,
            policy_digest,
            repository_readiness,
            repository_readiness_digest,
        )
        if not args.confirm:
            emit(plan, args.pretty)
            return EXIT_OK

        if args.policy_digest != policy_digest:
            raise LandingError(
                "resolved readiness policy changed or --policy-digest was not preserved"
            )
        if (
            repository_readiness is not None
            and args.repository_readiness_digest != repository_readiness_digest
        ):
            raise LandingError(
                "repository readiness changed or --repository-readiness-digest was not preserved"
            )
        if repository_readiness is not None:
            final_snapshot = watcher_snapshot(
                repository,
                args.pr,
                fixture,
                config,
                args.no_config,
                reviewers,
                args.check_policy,
                args.strict_changes_requested,
            )
            final_policy, final_policy_digest = readiness_policy(final_snapshot)
            if final_policy_digest != policy_digest:
                raise LandingError("resolved readiness policy changed before landing")
            final_target = repository_readiness_live_target(
                final_snapshot, head, args.mode, args.method
            )
            final_config = normalized_repository_readiness_config(
                final_policy.get("repositoryReadiness")
            )
            assert final_config is not None
            final_readiness, final_digest = validated_repository_readiness(
                repository_readiness,
                config=final_config,
                target=final_target,
                expected_head=head,
                candidate_tree=str(repository_readiness["candidateTreeSha"]),
            )
            if final_digest != repository_readiness_digest:
                raise LandingError("repository readiness changed before landing")
            target = final_target
            repository_readiness = final_readiness

        pr = target["pr"]
        assert isinstance(pr, dict)
        url = str(pr["url"])
        command = landing_command(url, head, args.mode, args.method)
        result = run(command, repository)
        emit(
            {
                **plan,
                "state": "landing_requested",
                "requiresConfirmation": False,
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
