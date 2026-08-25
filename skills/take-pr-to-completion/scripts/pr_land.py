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
from pathlib import Path
from typing import Sequence


EXIT_OK = 0
EXIT_BLOCKED = 20
WARNING = "This landing request may merge the pull request immediately."
METHOD_POLICY_FIELDS = {
    "merge": "mergeCommitAllowed",
    "rebase": "rebaseMergeAllowed",
    "squash": "squashMergeAllowed",
}
LOCAL_AUTHORITY_SCHEMA = "merls.local_delivery_evidence.v1"
LOCAL_AUTHORITY_KIND = "centralized-local-merls-suite"
LOCAL_AUTHORITY_SCOPE = "merls-local-delivery-authority-only"
LOCAL_COMMAND_RESULT_SCHEMA = "merls.local_command_result.v1"
MERLS_REPORT_SCHEMA = "merls.test_run_result.v1"
MERLS_REQUIRED_PROFILE = "unit,functional,integration-safe"
MERLS_REQUIRED_PASSED = 276
MERLS_REQUIRED_FAILED = 0
MERLS_REQUIRED_SKIPPED = 0
LOCAL_EVIDENCE_MAX_AGE_SECONDS = 24 * 60 * 60
LOCAL_EVIDENCE_FUTURE_SKEW_SECONDS = 5 * 60
HOSTED_UNAVAILABLE_REASON = "account_billing_or_spending_limit"
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
MERLS_POLICY_REQUIREMENTS = {
    "docs/governance/decisions/DEC-GOV-008.yaml": (
        "DECISION_ID: DEC-GOV-008",
        "STATUS: active",
        "repository-local source-only validation as the required progress path",
        "Executed check failures remain evidence",
    ),
    "docs/MERLS_TEST_CENTER.md": (
        "## Source Of Truth",
        "scripts/merls-test-suite.py",
        "manifests/merls-test-catalog.json",
        "centralized in the catalog",
    ),
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


def json_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise LandingError(f"could not read evidence artifact {path}: {error}") from error
    return digest.hexdigest()


def json_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise LandingError(f"{label} not found: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise LandingError(f"{label} is not valid JSON: {error}") from error
    if not isinstance(value, dict):
        raise LandingError(f"{label} must be a JSON object")
    return value


def parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LandingError(f"{label} must be a non-empty UTC timestamp")
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise LandingError(f"{label} is not a valid timestamp") from error
    if parsed.tzinfo is None:
        raise LandingError(f"{label} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def validate_clean_exact_source(repository: Path, expected_head: str) -> None:
    current = run(["git", "rev-parse", "HEAD"], repository).stdout.strip()
    if current != expected_head:
        raise LandingError(
            f"local authority checkout is on {current}, expected exact PR head {expected_head}"
        )
    dirty = run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"], repository
    ).stdout.strip()
    if dirty:
        raise LandingError("local authority requires a clean source tree")


def validate_merls_policy_references(repository: Path, expected_head: str) -> list[str]:
    validated: list[str] = []
    for path, markers in MERLS_POLICY_REQUIREMENTS.items():
        content = run(["git", "show", f"{expected_head}:{path}"], repository).stdout
        missing = [marker for marker in markers if marker not in content]
        if missing:
            raise LandingError(
                f"Merls delivery-authority policy {path} is missing required marker: "
                f"{missing[0]}"
            )
        validated.append(path)
    return validated


def validate_command_results(
    value: object,
    label: str,
    expected_head: str,
    validated_at: datetime,
) -> list[str]:
    if not isinstance(value, list) or not value:
        raise LandingError(f"local authority evidence requires at least one {label} result")
    names: list[str] = []
    for index, entry in enumerate(value):
        if not isinstance(entry, dict):
            raise LandingError(f"{label}[{index}] must be an object")
        name = entry.get("name")
        command = entry.get("command")
        if entry.get("schema") != LOCAL_COMMAND_RESULT_SCHEMA:
            raise LandingError(f"{label}[{index}] has an invalid schema")
        if not isinstance(name, str) or not name.strip():
            raise LandingError(f"{label}[{index}] is missing a name")
        if entry.get("headSha") != expected_head:
            raise LandingError(f"{label}[{index}] was not run for the exact PR head")
        if entry.get("status") != "pass" or entry.get("exitCode") != 0:
            raise LandingError(f"{label}[{index}] did not pass")
        if not isinstance(command, list) or not command or not all(
            isinstance(item, str) and item for item in command
        ):
            raise LandingError(f"{label}[{index}] has an invalid command")
        completed_at = parse_utc(entry.get("completedAt"), f"{label}[{index}].completedAt")
        result_age = (validated_at - completed_at).total_seconds()
        if result_age < -LOCAL_EVIDENCE_FUTURE_SKEW_SECONDS:
            raise LandingError(f"{label}[{index}] completed after evidence validation")
        if result_age > LOCAL_EVIDENCE_MAX_AGE_SECONDS:
            raise LandingError(f"{label}[{index}] result is stale")
        names.append(name.strip())
    return names


def validate_merls_report(path: Path, expected_checksum: object) -> dict[str, object]:
    if not isinstance(expected_checksum, str) or not SHA256_PATTERN.fullmatch(
        expected_checksum
    ):
        raise LandingError("suite report checksum must be a 64-character SHA-256")
    actual_checksum = file_digest(path)
    if actual_checksum != expected_checksum.lower():
        raise LandingError("suite report checksum is invalid")
    report = json_object(path, "centralized Merls suite report")
    run_value = report.get("run")
    if report.get("schema") != MERLS_REPORT_SCHEMA or not isinstance(run_value, dict):
        raise LandingError("centralized Merls suite report schema is invalid")
    if run_value.get("profile") != MERLS_REQUIRED_PROFILE:
        raise LandingError("centralized Merls suite report does not cover the required profiles")
    if run_value.get("status") != "pass":
        raise LandingError("centralized Merls suite report status is not pass")
    run_slug = run_value.get("slug")
    if not isinstance(run_slug, str) or not run_slug:
        raise LandingError("centralized Merls suite report is missing its run slug")
    if report.get("live_changed") != "no":
        raise LandingError("centralized Merls suite report is missing LIVE_CHANGED=no")

    results = report.get("results")
    if not isinstance(results, list) or len(results) != MERLS_REQUIRED_PASSED:
        raise LandingError(
            f"centralized Merls suite must contain exactly {MERLS_REQUIRED_PASSED} results"
        )
    ids = [row.get("test_id") for row in results if isinstance(row, dict)]
    if (
        len(ids) != MERLS_REQUIRED_PASSED
        or any(not isinstance(test_id, str) or not test_id for test_id in ids)
        or len(set(ids)) != MERLS_REQUIRED_PASSED
    ):
        raise LandingError("centralized Merls suite results are partial or duplicated")
    statuses = [row.get("status") for row in results if isinstance(row, dict)]
    passed = sum(status == "pass" for status in statuses)
    failed = sum(status == "fail" for status in statuses)
    skipped = sum(status == "skip" for status in statuses)
    if (passed, failed, skipped) != (
        MERLS_REQUIRED_PASSED,
        MERLS_REQUIRED_FAILED,
        MERLS_REQUIRED_SKIPPED,
    ) or len(statuses) != MERLS_REQUIRED_PASSED:
        raise LandingError("centralized Merls suite result counts are not exactly 276/0/0")

    summary = run_value.get("result_summary")
    validation = run_value.get("validation_summary")
    if not isinstance(summary, dict) or not isinstance(validation, dict):
        raise LandingError("centralized Merls suite summaries are missing")
    if (
        summary.get("pass") != MERLS_REQUIRED_PASSED
        or summary.get("fail", 0) != MERLS_REQUIRED_FAILED
        or summary.get("skip", 0) != MERLS_REQUIRED_SKIPPED
        or set(summary) - {"pass", "fail", "skip"}
    ):
        raise LandingError("centralized Merls suite summary is not exactly 276/0/0")
    if (
        validation.get("total_passes") != MERLS_REQUIRED_PASSED
        or validation.get("failures") != MERLS_REQUIRED_FAILED
        or validation.get("skips") != MERLS_REQUIRED_SKIPPED
    ):
        raise LandingError("centralized Merls validation summary is not exactly 276/0/0")
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "reportSha256": actual_checksum,
        "runSlug": run_slug,
        "completedAt": run_value.get("completed_at"),
    }


def validate_local_evidence(
    repository: Path,
    evidence_path: Path,
    expected_checksum: str,
    expected_head: str,
    target: dict[str, object],
    now: datetime | None = None,
) -> dict[str, object]:
    if not SHA256_PATTERN.fullmatch(expected_checksum):
        raise LandingError("local authority evidence checksum must be a 64-character SHA-256")
    actual_checksum = file_digest(evidence_path)
    if actual_checksum != expected_checksum.lower():
        raise LandingError("local authority evidence checksum is invalid")
    evidence = json_object(evidence_path, "local authority evidence")
    pr = target.get("pr")
    if not isinstance(pr, dict):
        raise LandingError("local authority target is missing pull request data")
    if evidence.get("schema") != LOCAL_AUTHORITY_SCHEMA:
        raise LandingError("local authority evidence schema is invalid")
    if evidence.get("authority") != LOCAL_AUTHORITY_KIND:
        raise LandingError("repository does not identify the centralized Merls suite authority")
    if evidence.get("repository") != target.get("repository"):
        raise LandingError("local authority evidence names the wrong repository")
    if evidence.get("prNumber") != pr.get("number"):
        raise LandingError("local authority evidence names the wrong pull request")
    if evidence.get("headSha") != expected_head:
        raise LandingError("local authority evidence was produced for another commit")
    if evidence.get("sourceTreeClean") is not True:
        raise LandingError("local authority evidence does not attest a clean source tree")
    hosted = evidence.get("hostedChecks")
    if not isinstance(hosted, dict) or hosted.get("status") != "unavailable":
        raise LandingError("evidence must state hosted checks were unavailable, not passed")
    if hosted.get("reasonCode") != HOSTED_UNAVAILABLE_REASON:
        raise LandingError("hosted-check unavailability reason is not an allowed infrastructure reason")
    reason = hosted.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise LandingError("hosted-check unavailability reason is not documented")
    authorization = evidence.get("authorization")
    if not isinstance(authorization, dict) or authorization.get("type") not in {
        "operator",
        "policy",
    }:
        raise LandingError("local authority requires operator authorization or a policy reference")
    authorization_ref = authorization.get("reference")
    if not isinstance(authorization_ref, str) or not authorization_ref.strip():
        raise LandingError("local authority authorization reference is missing")

    validated_at = parse_utc(evidence.get("validatedAt"), "validatedAt")
    current_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current_time - validated_at).total_seconds()
    if age < -LOCAL_EVIDENCE_FUTURE_SKEW_SECONDS:
        raise LandingError("local authority evidence timestamp is in the future")
    if age > LOCAL_EVIDENCE_MAX_AGE_SECONDS:
        raise LandingError("local authority evidence is stale")

    focused = validate_command_results(
        evidence.get("focusedTests"), "focusedTests", expected_head, validated_at
    )
    validators = validate_command_results(
        evidence.get("requiredValidators"),
        "requiredValidators",
        expected_head,
        validated_at,
    )
    suite = evidence.get("suite")
    if not isinstance(suite, dict):
        raise LandingError("local authority evidence is missing the centralized suite artifact")
    if suite.get("headSha") != expected_head:
        raise LandingError("centralized Merls suite was produced for another commit")
    report_path_value = suite.get("reportPath")
    if not isinstance(report_path_value, str) or not report_path_value:
        raise LandingError("centralized Merls suite report path is missing")
    report_path = Path(report_path_value).expanduser()
    if not report_path.is_absolute():
        report_path = evidence_path.parent / report_path
    suite_record = validate_merls_report(report_path.resolve(), suite.get("reportSha256"))
    report_completed_at = parse_utc(
        suite_record["completedAt"], "centralized Merls suite completed_at"
    )
    report_age = (validated_at - report_completed_at).total_seconds()
    if report_age < -LOCAL_EVIDENCE_FUTURE_SKEW_SECONDS:
        raise LandingError("centralized Merls suite completed after evidence validation")
    if report_age > LOCAL_EVIDENCE_MAX_AGE_SECONDS:
        raise LandingError("centralized Merls suite report is stale")
    validate_clean_exact_source(repository, expected_head)
    policy_references = validate_merls_policy_references(repository, expected_head)
    return {
        "hostedChecks": {
            "status": "unavailable",
            "reasonCode": HOSTED_UNAVAILABLE_REASON,
            "reason": reason.strip(),
        },
        "deliveryAuthority": LOCAL_AUTHORITY_KIND,
        "validatedHeadSha": expected_head,
        "suiteResult": {
            "passed": suite_record["passed"],
            "failed": suite_record["failed"],
            "skipped": suite_record["skipped"],
        },
        "suiteRunSlug": suite_record["runSlug"],
        "reportChecksum": suite_record["reportSha256"],
        "evidenceChecksum": actual_checksum,
        "validatedAt": evidence.get("validatedAt"),
        "focusedTests": focused,
        "requiredValidators": validators,
        "authorization": {
            "type": authorization.get("type"),
            "reference": authorization_ref.strip(),
        },
        "policyReferences": policy_references,
        "genericBypass": False,
        "scope": LOCAL_AUTHORITY_SCOPE,
    }


def gh_json(args: Sequence[str], repository: Path, label: str) -> object:
    result = run(args, repository)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise LandingError(f"{label} returned invalid JSON") from error


def validate_zero_step_hosted_runs(
    runs: object,
    job_views: object,
    expected_head: str,
    failing_check_names: Sequence[object],
) -> dict[str, object]:
    if not isinstance(runs, list) or not runs or len(runs) >= 100:
        raise LandingError("hosted workflow evidence is missing or may be partial")
    if not isinstance(job_views, dict):
        raise LandingError("hosted workflow job evidence is malformed")
    jobs: list[dict[str, object]] = []
    workflows: list[dict[str, object]] = []
    for raw_run in runs:
        if not isinstance(raw_run, dict):
            raise LandingError("hosted workflow evidence contains a malformed run")
        run_id = raw_run.get("databaseId")
        if not isinstance(run_id, int) or raw_run.get("headSha") != expected_head:
            raise LandingError("hosted workflow evidence is not tied to the exact PR head")
        if raw_run.get("event") != "pull_request":
            raise LandingError("hosted workflow evidence includes a non-PR run")
        if raw_run.get("status") != "completed" or raw_run.get("conclusion") != "failure":
            raise LandingError("hosted workflows were not uniformly zero-step infrastructure failures")
        view = job_views.get(run_id)
        if not isinstance(view, dict):
            raise LandingError(f"hosted workflow {run_id} is missing job evidence")
        raw_jobs = view.get("jobs")
        if not isinstance(raw_jobs, list) or not raw_jobs:
            raise LandingError(f"hosted workflow {run_id} did not create inspectable jobs")
        for job in raw_jobs:
            if not isinstance(job, dict):
                raise LandingError(f"hosted workflow {run_id} has a malformed job")
            conclusion = str(job.get("conclusion") or "").lower()
            if str(job.get("status") or "").lower() != "completed" or conclusion != "failure":
                raise LandingError("canceled, timed-out, pending, or partial hosted jobs remain blocking")
            steps = job.get("steps")
            if steps not in (None, []):
                raise LandingError("a hosted product-test job ran steps and failed")
            name = job.get("name")
            if not isinstance(name, str) or not name:
                raise LandingError("hosted zero-step job is missing its name")
            jobs.append({"databaseId": job.get("databaseId"), "name": name})
        workflows.append(
            {
                "databaseId": run_id,
                "name": raw_run.get("workflowName"),
                "url": raw_run.get("url"),
            }
        )
    zero_step_names = {str(job["name"]) for job in jobs}
    reported_failures = {
        str(name) for name in failing_check_names if isinstance(name, str) and name
    }
    if reported_failures and not reported_failures.issubset(zero_step_names):
        raise LandingError("a failing hosted check is not explained by a zero-step job")
    return {
        "workflowCount": len(workflows),
        "jobCount": len(jobs),
        "runnableStepCount": 0,
        "workflows": sorted(workflows, key=lambda item: int(item["databaseId"])),
        "jobs": sorted(jobs, key=lambda item: str(item["name"])),
    }


def hosted_unavailability_observation(
    repository: Path,
    repository_name: str,
    expected_head: str,
    failing_check_names: Sequence[object],
) -> dict[str, object]:
    fields = "databaseId,workflowName,status,conclusion,headSha,event,url"
    runs = gh_json(
        [
            "gh",
            "run",
            "list",
            "--repo",
            repository_name,
            "--commit",
            expected_head,
            "--event",
            "pull_request",
            "--limit",
            "100",
            "--json",
            fields,
        ],
        repository,
        "hosted workflow query",
    )
    if not isinstance(runs, list):
        raise LandingError("hosted workflow query returned a non-list")
    job_views: dict[int, object] = {}
    for raw_run in runs:
        if not isinstance(raw_run, dict) or not isinstance(raw_run.get("databaseId"), int):
            raise LandingError("hosted workflow query returned malformed run data")
        run_id = raw_run["databaseId"]
        assert isinstance(run_id, int)
        job_views[run_id] = gh_json(
            [
                "gh",
                "run",
                "view",
                str(run_id),
                "--repo",
                repository_name,
                "--json",
                "jobs",
            ],
            repository,
            f"hosted workflow {run_id} job query",
        )
    return validate_zero_step_hosted_runs(
        runs, job_views, expected_head, failing_check_names
    )


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


def verified_local_authority_target(
    snapshot: dict[str, object],
    expected_head: str,
    mode: str,
    method: str | None,
    repository: Path,
    evidence_path: Path,
    evidence_checksum: str,
) -> tuple[dict[str, object], dict[str, object]]:
    targets = snapshot.get("targets")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(
        targets[0], dict
    ):
        raise LandingError("local authority requires exactly one fresh pull request target")
    target = targets[0]
    pr = target.get("pr")
    if not isinstance(pr, dict):
        raise LandingError("local authority target is missing pull request data")
    current_head = pr.get("headSha")
    if current_head != expected_head:
        raise LandingError(
            f"landing authorization is stale (expected {expected_head}, current {current_head})"
        )
    if pr.get("state") != "OPEN":
        raise LandingError("local authority requires an open pull request")
    if pr.get("mergeable") != "MERGEABLE":
        raise LandingError("local authority requires a mergeable pull request")
    if pr.get("mergeStateStatus") not in {"CLEAN", "UNSTABLE", "BLOCKED"}:
        raise LandingError("local authority cannot explain the current merge state")
    if pr.get("reviewDecision") in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        raise LandingError("local authority cannot replace required review")
    url = pr.get("url")
    if not isinstance(url, str) or not url:
        raise LandingError("local authority target is missing its pull request URL")

    reviews = target.get("reviews")
    if not isinstance(reviews, dict):
        raise LandingError("local authority target is missing review evidence")
    if reviews.get("unresolvedThreadCount") != 0 or reviews.get(
        "missingRequiredReviewers"
    ) not in ([], ()):
        raise LandingError("local authority cannot replace unresolved or missing review")
    checks = target.get("checks")
    if not isinstance(checks, dict):
        raise LandingError("local authority target is missing hosted-check evidence")
    for key in ("pending", "unknown", "malformed"):
        value = checks.get(key)
        if value not in ([], None):
            raise LandingError(f"local authority cannot replace {key} hosted-check state")

    actions = target.get("actions")
    if not isinstance(actions, list):
        raise LandingError("local authority target actions are malformed")
    for action in actions:
        if not isinstance(action, dict):
            raise LandingError("local authority target actions are malformed")
        action_type = action.get("type")
        if action_type == "ci_failure":
            continue
        if action_type == "blocked" and action.get("reason") == (
            "merge is blocked without a reported pending gate"
        ):
            continue
        raise LandingError(f"local authority cannot replace action: {action_type}")
    pending = target.get("pending")
    if not isinstance(pending, list):
        raise LandingError("local authority target pending gates are malformed")
    for gate in pending:
        if not isinstance(gate, dict):
            raise LandingError("local authority target pending gates are malformed")
        if gate.get("type") == "checks" and gate.get("checks") == []:
            continue
        if gate.get("type") == "merge_state" and gate.get("mergeStateStatus") in {
            "UNSTABLE",
            "BLOCKED",
        }:
            continue
        raise LandingError(f"local authority cannot replace pending gate: {gate.get('type')}")

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
        if policy.get(field) is not True:
            raise LandingError(f"fresh repository policy does not allow {method} merges")

    repository_name = target.get("repository")
    if not isinstance(repository_name, str) or not repository_name:
        raise LandingError("local authority target is missing its repository name")
    evidence_record = validate_local_evidence(
        repository,
        evidence_path,
        evidence_checksum,
        expected_head,
        target,
    )
    hosted_record = hosted_unavailability_observation(
        repository,
        repository_name,
        expected_head,
        checks.get("fail") if isinstance(checks.get("fail"), list) else [],
    )
    evidence_record["hostedChecks"] = {
        **evidence_record["hostedChecks"],
        **hosted_record,
    }
    return target, evidence_record


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
    digest = json_digest(policy)
    return policy, digest


def bound_policy_digest(
    policy: dict[str, object], validation_authority: dict[str, object] | None
) -> str:
    if validation_authority is None:
        return json_digest(policy)
    return json_digest(
        {
            "readinessPolicy": policy,
            "validationAuthority": validation_authority,
        }
    )


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
    validation_authority: dict[str, object] | None = None,
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
    if validation_authority is not None:
        payload["validationAuthority"] = validation_authority
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
        "--local-authority-evidence",
        help="checksum-bound Merls local delivery evidence artifact",
    )
    parser.add_argument(
        "--local-authority-checksum",
        help="expected SHA-256 of --local-authority-evidence",
    )
    parser.add_argument("--fixture", help="offline watcher fixture; planning only")
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    repository = Path(args.repo).expanduser().resolve()
    fixture = Path(args.fixture).expanduser().resolve() if args.fixture else None
    config = Path(args.config).expanduser().resolve() if args.config else None
    reviewers = tuple(args.reviewer or ())
    try:
        if args.confirm and fixture is not None:
            raise LandingError("offline fixtures cannot authorize a landing mutation")
        if bool(args.local_authority_evidence) != bool(args.local_authority_checksum):
            raise LandingError(
                "--local-authority-evidence and --local-authority-checksum are required together"
            )
        if args.local_authority_evidence and fixture is not None:
            raise LandingError("offline fixtures cannot authorize local delivery authority")
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
        validation_authority: dict[str, object] | None = None
        if args.local_authority_evidence:
            evidence_path = Path(args.local_authority_evidence).expanduser().resolve()
            target, validation_authority = verified_local_authority_target(
                snapshot,
                head,
                args.mode,
                args.method,
                repository,
                evidence_path,
                args.local_authority_checksum,
            )
        else:
            target = verified_target(snapshot, head, args.mode, args.method)
        policy, _unbound_policy_digest = readiness_policy(snapshot)
        policy_digest = bound_policy_digest(policy, validation_authority)
        plan = plan_payload(
            target,
            head,
            args.mode,
            args.method,
            policy,
            policy_digest,
            validation_authority,
        )
        if not args.confirm:
            emit(plan, args.pretty)
            return EXIT_OK

        if args.policy_digest != policy_digest:
            raise LandingError(
                "resolved readiness policy changed or --policy-digest was not preserved"
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
