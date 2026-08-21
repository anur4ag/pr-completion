#!/usr/bin/env python3
"""Guarded, per-PR landing request for PR Completion.

This is the only shipped helper allowed to mutate GitHub merge state. It first
re-runs the read-only watcher, requires the exact authorized head to remain
verified ready or to satisfy the explicit zero-step CI evidence contract, and
then invokes GitHub CLI without admin or protection bypass.
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
from pathlib import Path
from typing import Sequence


EXIT_OK = 0
EXIT_BLOCKED = 20
WARNING = "This landing request may merge the pull request immediately."
ZERO_STEP_WAIVER_MODE = "ZERO_STEP_CI_EXCEPTION"
ZERO_STEP_FAILURE_CLASS = "TRANSPORT_BEFORE_SOURCE_EXECUTION"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
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
    """Verify exact-head, queue, and merge-method policy for a landing target."""
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
        raise LandingError(f"zero-step CI waiver {label} must be an object")
    actual = {str(key) for key in value}
    if actual != keys:
        missing = sorted(keys - actual)
        unexpected = sorted(actual - keys)
        detail = []
        if missing:
            detail.append(f"missing={','.join(missing)}")
        if unexpected:
            detail.append(f"unexpected={','.join(unexpected)}")
        raise LandingError(
            f"zero-step CI waiver {label} fields are invalid ({'; '.join(detail)})"
        )
    return value


def non_empty_text(value: object, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise LandingError(f"zero-step CI waiver {label} must be explicit and non-empty")
    return value.strip()


def exact_head(value: object, expected_head: str, label: str) -> str:
    head = non_empty_text(value, label)
    if SHA_PATTERN.fullmatch(head) is None:
        raise LandingError(f"zero-step CI waiver {label} must be a full lowercase commit SHA")
    if head != expected_head:
        raise LandingError(
            f"zero-step CI waiver is stale (expected {expected_head}, {label}={head})"
        )
    return head


def approval_timestamp(value: object) -> str:
    timestamp = non_empty_text(value, "operatorApproval.approvedAt")
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise LandingError(
            "zero-step CI waiver operatorApproval.approvedAt must be an ISO-8601 timestamp"
        ) from error
    if parsed.tzinfo is None:
        raise LandingError(
            "zero-step CI waiver operatorApproval.approvedAt must include a timezone"
        )
    return timestamp


def load_zero_step_waiver(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LandingError(f"zero-step CI waiver evidence file not found: {path}") from error
    except json.JSONDecodeError as error:
        raise LandingError(f"zero-step CI waiver evidence is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise LandingError("zero-step CI waiver evidence must be a JSON object")
    return value


def validated_zero_step_waiver(
    value: dict[str, object],
    expected_head: str,
    failed_checks: Sequence[str],
) -> tuple[dict[str, object], str]:
    """Validate and normalize an explicit, exact-head zero-step CI exception record."""
    raw = exact_object(
        value,
        "evidence",
        {
            "schemaVersion",
            "headSha",
            "sourceValidationStatus",
            "ciTransportStatus",
            "ciSourceExecutionStatus",
            "operatorWaiver",
            "landingMode",
            "waiverReason",
            "independentValidation",
            "operatorApproval",
            "ciFailureEvidence",
        },
    )
    if raw["schemaVersion"] != 1:
        raise LandingError("zero-step CI waiver schemaVersion must be 1")
    exact_head(raw["headSha"], expected_head, "headSha")
    required_values = {
        "sourceValidationStatus": "PASS",
        "ciTransportStatus": "FAIL_ZERO_STEP",
        "ciSourceExecutionStatus": "NONE",
        "operatorWaiver": "APPROVED",
        "landingMode": ZERO_STEP_WAIVER_MODE,
    }
    for field, expected in required_values.items():
        if raw[field] != expected:
            raise LandingError(f"zero-step CI waiver {field} must equal {expected}")
    reason = non_empty_text(raw["waiverReason"], "waiverReason", minimum=20)

    independent = exact_object(
        raw["independentValidation"],
        "independentValidation",
        {"headSha", "requiredChecks"},
    )
    exact_head(
        independent["headSha"], expected_head, "independentValidation.headSha"
    )
    required_checks = independent["requiredChecks"]
    if not isinstance(required_checks, list) or not required_checks:
        raise LandingError(
            "zero-step CI waiver independentValidation.requiredChecks must be non-empty"
        )
    normalized_validations: list[dict[str, object]] = []
    for index, item in enumerate(required_checks):
        check = exact_object(
            item,
            f"independentValidation.requiredChecks[{index}]",
            {"name", "required", "status", "evidenceRef"},
        )
        if check["required"] is not True:
            raise LandingError("zero-step CI waiver validation evidence must be required")
        if check["status"] != "PASS":
            raise LandingError("zero-step CI waiver validation evidence must report PASS")
        normalized_validations.append(
            {
                "name": non_empty_text(check["name"], "validation name"),
                "required": True,
                "status": "PASS",
                "evidenceRef": non_empty_text(
                    check["evidenceRef"], "validation evidenceRef"
                ),
            }
        )

    approval = exact_object(
        raw["operatorApproval"],
        "operatorApproval",
        {"headSha", "status", "principal", "approvedAt", "approvalRef"},
    )
    exact_head(approval["headSha"], expected_head, "operatorApproval.headSha")
    if approval["status"] != "APPROVED":
        raise LandingError("zero-step CI waiver operator approval must equal APPROVED")
    normalized_approval = {
        "headSha": expected_head,
        "status": "APPROVED",
        "principal": non_empty_text(approval["principal"], "operatorApproval.principal"),
        "approvedAt": approval_timestamp(approval["approvedAt"]),
        "approvalRef": non_empty_text(
            approval["approvalRef"], "operatorApproval.approvalRef"
        ),
    }

    failures = raw["ciFailureEvidence"]
    if not isinstance(failures, list) or not failures:
        raise LandingError("zero-step CI waiver ciFailureEvidence must be non-empty")
    normalized_failures: list[dict[str, object]] = []
    evidence_names: list[str] = []
    for index, item in enumerate(failures):
        failure = exact_object(
            item,
            f"ciFailureEvidence[{index}]",
            {
                "checkName",
                "runId",
                "jobId",
                "runnerId",
                "executedStepCount",
                "failureClass",
                "evidenceRef",
            },
        )
        check_name = non_empty_text(failure["checkName"], "ciFailureEvidence.checkName")
        runner_id = failure["runnerId"]
        step_count = failure["executedStepCount"]
        run_id = failure["runId"]
        job_id = failure["jobId"]
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise LandingError("zero-step CI waiver requires a positive Actions runId")
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id <= 0:
            raise LandingError("zero-step CI waiver requires a positive Actions jobId")
        if isinstance(runner_id, bool) or not isinstance(runner_id, int) or runner_id != 0:
            raise LandingError(
                "zero-step CI waiver requires runnerId=0 for every failed check"
            )
        if isinstance(step_count, bool) or not isinstance(step_count, int) or step_count != 0:
            raise LandingError(
                "zero-step CI waiver requires executedStepCount=0 for every failed check"
            )
        if failure["failureClass"] != ZERO_STEP_FAILURE_CLASS:
            raise LandingError(
                "zero-step CI waiver rejects source/test failures and unknown failure classes"
            )
        evidence_names.append(check_name)
        normalized_failures.append(
            {
                "checkName": check_name,
                "runId": run_id,
                "jobId": job_id,
                "runnerId": 0,
                "executedStepCount": 0,
                "failureClass": ZERO_STEP_FAILURE_CLASS,
                "evidenceRef": non_empty_text(
                    failure["evidenceRef"], "ciFailureEvidence.evidenceRef"
                ),
            }
        )
    if len(set(evidence_names)) != len(evidence_names):
        raise LandingError("zero-step CI waiver contains duplicate CI failure evidence")
    if sorted(evidence_names) != sorted(failed_checks):
        raise LandingError(
            "zero-step CI waiver evidence does not exactly cover current failed checks"
        )

    normalized = {
        "HEAD_SHA": expected_head,
        "SOURCE_VALIDATION_STATUS": "PASS",
        "CI_TRANSPORT_STATUS": "FAIL_ZERO_STEP",
        "CI_SOURCE_EXECUTION_STATUS": "NONE",
        "OPERATOR_WAIVER": "APPROVED",
        "LANDING_MODE": ZERO_STEP_WAIVER_MODE,
        "LANDING_EXCEPTION_REASON": reason,
        "INDEPENDENT_VALIDATION_EVIDENCE": normalized_validations,
        "CI_ZERO_STEP_EVIDENCE": normalized_failures,
        "OPERATOR_APPROVAL_EVIDENCE": normalized_approval,
    }
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return normalized, digest


def command_json(args: Sequence[str], repository: Path, label: str) -> dict[str, object]:
    result = run(args, repository)
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LandingError(f"{label} returned invalid JSON") from error
    if not isinstance(value, dict):
        raise LandingError(f"{label} returned a non-object response")
    return value


def verify_live_zero_step_evidence(
    repository: Path,
    target: dict[str, object],
    waiver: dict[str, object],
) -> None:
    """Re-fetch each Actions job/run and prove zero execution for the current head."""
    owner_repo = target.get("repository")
    pr = target.get("pr")
    failures = waiver.get("CI_ZERO_STEP_EVIDENCE")
    if not isinstance(owner_repo, str) or not owner_repo:
        raise LandingError("zero-step CI waiver target repository is unknown")
    if not isinstance(pr, dict) or not isinstance(pr.get("headSha"), str):
        raise LandingError("zero-step CI waiver target head is unknown")
    if not isinstance(failures, list) or not failures:
        raise LandingError("zero-step CI waiver normalized CI evidence is missing")
    expected_head = str(pr["headSha"])
    for item in failures:
        if not isinstance(item, dict):
            raise LandingError("zero-step CI waiver normalized CI evidence is malformed")
        run_id = item.get("runId")
        job_id = item.get("jobId")
        check_name = item.get("checkName")
        job = command_json(
            ["gh", "api", f"repos/{owner_repo}/actions/jobs/{job_id}"],
            repository,
            "GitHub Actions job evidence",
        )
        workflow_run = command_json(
            ["gh", "api", f"repos/{owner_repo}/actions/runs/{run_id}"],
            repository,
            "GitHub Actions run evidence",
        )
        if job.get("id") != job_id or job.get("run_id") != run_id:
            raise LandingError("zero-step CI waiver Actions job/run identity mismatch")
        if job.get("name") != check_name:
            raise LandingError("zero-step CI waiver Actions check name mismatch")
        if job.get("head_sha") != expected_head or workflow_run.get("head_sha") != expected_head:
            raise LandingError("zero-step CI waiver Actions evidence is stale for the current head")
        if job.get("status") != "completed" or job.get("conclusion") != "failure":
            raise LandingError("zero-step CI waiver Actions job is not a completed failure")
        if workflow_run.get("status") != "completed" or workflow_run.get("conclusion") != "failure":
            raise LandingError("zero-step CI waiver Actions run is not a completed failure")
        if job.get("runner_id") != 0 or job.get("runner_name") not in {"", None}:
            raise LandingError("zero-step CI waiver live evidence shows runner assignment")
        steps = job.get("steps")
        if not isinstance(steps, list) or steps:
            raise LandingError("zero-step CI waiver live evidence shows executed or unknown steps")
        if job.get("html_url") != item.get("evidenceRef"):
            raise LandingError("zero-step CI waiver Actions evidence URL mismatch")


def zero_step_waiver_target(
    snapshot: dict[str, object],
    evidence: dict[str, object],
    expected_head: str,
    mode: str,
    method: str | None,
) -> tuple[dict[str, object], dict[str, object], str]:
    """Require that a watcher failure is exclusively an evidenced zero-step transport failure."""
    policy = snapshot.get("policy")
    if not isinstance(policy, dict) or policy.get("checkPolicy") != "all":
        raise LandingError("zero-step CI waiver requires checkPolicy=all")
    targets = snapshot.get("targets")
    if snapshot.get("state") != "actionable":
        raise LandingError(
            f"zero-step CI waiver requires actionable CI failure state (state={snapshot.get('state')})"
        )
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        raise LandingError("zero-step CI waiver requires exactly one pull request target")
    if snapshot.get("errors") != []:
        raise LandingError("zero-step CI waiver rejects watcher errors or unknown error state")
    target = targets[0]
    if target.get("state") != "actionable":
        raise LandingError("zero-step CI waiver target is not actionable")
    pr = target.get("pr")
    if not isinstance(pr, dict):
        raise LandingError("zero-step CI waiver target is missing pull request state")
    if pr.get("state") != "OPEN":
        raise LandingError("zero-step CI waiver requires an open pull request")
    if pr.get("mergeable") != "MERGEABLE" or pr.get("mergeStateStatus") != "UNSTABLE":
        raise LandingError(
            "zero-step CI waiver requires MERGEABLE with mergeStateStatus=UNSTABLE"
        )
    if pr.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        raise LandingError("zero-step CI waiver cannot bypass a review gate")
    reviews = target.get("reviews")
    if not isinstance(reviews, dict):
        raise LandingError("zero-step CI waiver review state is unknown")
    if reviews.get("unresolvedThreadCount") != 0 or reviews.get("missingRequiredReviewers") != []:
        raise LandingError("zero-step CI waiver cannot bypass review threads or reviewers")
    actions = target.get("actions")
    if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict):
        raise LandingError("zero-step CI waiver requires exactly one CI failure action")
    if actions[0].get("type") != "ci_failure":
        raise LandingError("zero-step CI waiver cannot bypass non-CI actions")
    snapshot_actions = snapshot.get("actions")
    if (
        not isinstance(snapshot_actions, list)
        or len(snapshot_actions) != 1
        or not isinstance(snapshot_actions[0], dict)
        or snapshot_actions[0].get("type") != "ci_failure"
        or snapshot_actions[0].get("repository") != target.get("repository")
    ):
        raise LandingError("zero-step CI waiver aggregate action state is inconsistent")
    checks = target.get("checks")
    if not isinstance(checks, dict):
        raise LandingError("zero-step CI waiver check state is unknown")
    expected_check_fields = {
        "total",
        "pass",
        "fail",
        "pending",
        "skipping",
        "unknown",
        "malformed",
    }
    if set(checks) != expected_check_fields:
        raise LandingError("zero-step CI waiver check buckets are incomplete or ambiguous")
    for bucket in ("pass", "fail", "pending", "skipping", "unknown", "malformed"):
        if not isinstance(checks[bucket], list):
            raise LandingError(f"zero-step CI waiver {bucket} check bucket is unknown")
    total = checks["total"]
    observed_total = sum(
        len(checks[bucket])
        for bucket in ("pass", "fail", "pending", "skipping", "unknown")
    )
    if isinstance(total, bool) or not isinstance(total, int) or total != observed_total:
        raise LandingError("zero-step CI waiver check totals are inconsistent")
    failed_checks = checks.get("fail")
    if (
        not isinstance(failed_checks, list)
        or not failed_checks
        or not all(isinstance(item, str) and item.strip() for item in failed_checks)
    ):
        raise LandingError("zero-step CI waiver requires named failed checks")
    if actions[0].get("checks") != failed_checks:
        raise LandingError("zero-step CI waiver action/check evidence is inconsistent")
    if snapshot_actions[0].get("checks") != failed_checks:
        raise LandingError("zero-step CI waiver aggregate check evidence is inconsistent")
    for bucket in ("pending", "unknown", "malformed"):
        if checks[bucket]:
            raise LandingError(f"zero-step CI waiver rejects {bucket} check state")
    pending = target.get("pending")
    if (
        not isinstance(pending, list)
        or len(pending) != 1
        or not isinstance(pending[0], dict)
        or pending[0].get("type") != "merge_state"
        or pending[0].get("mergeStateStatus") != "UNSTABLE"
    ):
        raise LandingError("zero-step CI waiver rejects additional or ambiguous pending gates")
    target = landing_target_policy(target, expected_head, mode, method)
    normalized, digest = validated_zero_step_waiver(
        evidence, expected_head, tuple(str(item) for item in failed_checks)
    )
    return target, normalized, digest


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
    waiver: dict[str, object] | None = None,
    waiver_digest: str | None = None,
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
    if waiver is not None:
        payload.update(waiver)
        payload["zeroStepCiWaiverEvidenceDigest"] = waiver_digest
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
        "--zero-step-ci-waiver",
        help="explicit exact-head zero-step CI waiver evidence JSON",
    )
    parser.add_argument(
        "--waiver-evidence-digest",
        help="zero-step CI waiver evidence digest emitted by the confirmation plan",
    )
    parser.add_argument("--fixture", help="offline watcher fixture; planning only")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    repository = Path(args.repo).expanduser().resolve()
    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
    config = Path(args.config).expanduser().resolve() if args.config else None
    waiver_path = (
        Path(args.zero_step_ci_waiver).expanduser().resolve()
        if args.zero_step_ci_waiver
        else None
    )
    reviewers = tuple(args.reviewer or ())
    try:
        if args.confirm and fixture is not None:
            raise LandingError("offline fixtures cannot authorize a landing mutation")
        if args.waiver_evidence_digest and waiver_path is None:
            raise LandingError("--waiver-evidence-digest requires --zero-step-ci-waiver")
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
        waiver: dict[str, object] | None = None
        waiver_digest: str | None = None
        if waiver_path is None:
            target = verified_target(snapshot, head, args.mode, args.method)
        else:
            target, waiver, waiver_digest = zero_step_waiver_target(
                snapshot,
                load_zero_step_waiver(waiver_path),
                head,
                args.mode,
                args.method,
            )
            if fixture is None:
                verify_live_zero_step_evidence(repository, target, waiver)
        plan = plan_payload(
            target,
            head,
            args.mode,
            args.method,
            policy,
            policy_digest,
            waiver,
            waiver_digest,
        )
        if not args.confirm:
            emit(plan, args.pretty)
            return EXIT_OK

        if args.policy_digest != policy_digest:
            raise LandingError(
                "resolved readiness policy changed or --policy-digest was not preserved"
            )
        if waiver is not None and args.waiver_evidence_digest != waiver_digest:
            raise LandingError(
                "zero-step CI waiver evidence changed or --waiver-evidence-digest was not preserved"
            )

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
