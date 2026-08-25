"""Guarded landing helper regressions."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "pr_land.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

SPEC = importlib.util.spec_from_file_location("pr_land", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load pr_land module")
pr_land = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_land
SPEC.loader.exec_module(pr_land)
GH = "g" + "h"
WAIVER_HEAD = "a" * 40


def zero_step_snapshot(head: str = WAIVER_HEAD) -> dict[str, object]:
    failed = ["Source-only suite twice from clean state"]
    return {
        "state": "actionable",
        "policy": {
            "source": "no-config",
            "configPath": None,
            "checkPolicy": "all",
            "strictChangesRequested": False,
            "requiredReviewers": [],
        },
        "targets": [
            {
                "state": "actionable",
                "repository": "example/project",
                "repositoryPolicy": {
                    "mergeCommitAllowed": True,
                    "rebaseMergeAllowed": True,
                    "squashMergeAllowed": True,
                },
                "pr": {
                    "number": 7,
                    "url": "https://github.com/example/project/pull/7",
                    "state": "OPEN",
                    "headSha": head,
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "UNSTABLE",
                    "reviewDecision": None,
                    "isMergeQueueEnabled": False,
                },
                "checks": {
                    "total": 1,
                    "pass": [],
                    "fail": failed,
                    "pending": [],
                    "skipping": [],
                    "unknown": [],
                    "malformed": [],
                },
                "reviews": {
                    "unresolvedThreadCount": 0,
                    "requiredReviewers": [],
                    "missingRequiredReviewers": [],
                },
                "actions": [{"type": "ci_failure", "checks": failed}],
                "pending": [
                    {"type": "merge_state", "mergeStateStatus": "UNSTABLE"}
                ],
            }
        ],
        "actions": [
            {
                "repository": "example/project",
                "type": "ci_failure",
                "checks": failed,
            }
        ],
        "errors": [],
    }


def zero_step_evidence(head: str = WAIVER_HEAD) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "headSha": head,
        "sourceValidationStatus": "PASS",
        "ciTransportStatus": "FAIL_ZERO_STEP",
        "ciSourceExecutionStatus": "NONE",
        "operatorWaiver": "APPROVED",
        "landingMode": "ZERO_STEP_CI_EXCEPTION",
        "waiverReason": (
            "GitHub Actions failed before runner assignment or source execution."
        ),
        "independentValidation": {
            "headSha": head,
            "requiredChecks": [
                {
                    "name": "Windows and Linux landing-helper validation",
                    "required": True,
                    "status": "PASS",
                    "evidenceRef": "evidence://issue-296/local-validation",
                }
            ],
        },
        "operatorApproval": {
            "headSha": head,
            "status": "APPROVED",
            "principal": "repository-owner",
            "approvedAt": "2026-08-21T19:36:19Z",
            "approvalRef": "issue://296/operator-approval",
        },
        "ciFailureEvidence": [
            {
                "checkName": "Source-only suite twice from clean state",
                "runId": 32515352499,
                "jobId": 96875663837,
                "runnerId": 0,
                "executedStepCount": 0,
                "failureClass": "TRANSPORT_BEFORE_SOURCE_EXECUTION",
                "evidenceRef": (
                    "https://github.com/knudsonb2/merls-openclaw/actions/runs/"
                    "32515352499/job/96875663837"
                ),
            }
        ],
    }


class LandingCommandTests(unittest.TestCase):
    def test_auto_command_is_exact_head_guarded(self):
        self.assertEqual(
            pr_land.landing_command(
                "https://github.com/example/project/pull/7",
                "abc123",
                "auto",
                "squash",
            ),
            [
                GH,
                "pr",
                "merge",
                "https://github.com/example/project/pull/7",
                "--match-head-commit",
                "abc123",
                "--auto",
                "--squash",
            ],
        )

    def test_queue_command_has_no_strategy_or_admin_bypass(self):
        command = pr_land.landing_command(
            "https://github.com/example/project/pull/7",
            "abc123",
            "queue",
            None,
        )
        self.assertEqual(
            command,
            [
                GH,
                "pr",
                "merge",
                "https://github.com/example/project/pull/7",
                "--match-head-commit",
                "abc123",
            ],
        )
        self.assertNotIn("--admin", command)

    def test_auto_requires_method_and_queue_rejects_method(self):
        with self.assertRaisesRegex(pr_land.LandingError, "requires --method"):
            pr_land.landing_command("url", "head", "auto", None)
        with self.assertRaisesRegex(pr_land.LandingError, "does not accept"):
            pr_land.landing_command("url", "head", "queue", "squash")


class LandingCliTests(unittest.TestCase):
    def test_live_revalidation_preserves_repository_watcher_config(self):
        snapshot = {
            "state": "ready",
            "targets": [
                {
                    "state": "ready",
                    "repository": "example/project",
                    "pr": {
                        "number": 7,
                        "url": "https://github.com/example/project/pull/7",
                        "headSha": "abc123",
                    },
                }
            ],
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(snapshot), "")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pr_land, "run", return_value=completed
        ) as invoked:
            pr_land.watcher_snapshot(
                Path(directory), None, None, None, False, (), None, False
            )

        command = invoked.call_args.args[0]
        self.assertNotIn("--no-config", command)
        self.assertIn("--target", command)

    def test_live_revalidation_forwards_cli_readiness_policy(self):
        snapshot = {"state": "pending", "targets": [], "policy": {}}
        completed = subprocess.CompletedProcess([], 0, json.dumps(snapshot), "")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pr_land, "run", return_value=completed
        ) as invoked:
            config = Path(directory) / "policy.json"
            pr_land.watcher_snapshot(
                Path(directory),
                "7",
                None,
                config,
                False,
                ("alice", "bob"),
                "required",
                True,
            )

        command = invoked.call_args.args[0]
        self.assertEqual(command.count("--reviewer"), 2)
        self.assertIn("alice", command)
        self.assertIn("bob", command)
        self.assertEqual(command[command.index("--check-policy") + 1], "required")
        self.assertEqual(command[command.index("--config") + 1], str(config))
        self.assertIn("--strict-changes-requested", command)

    def test_live_revalidation_preserves_no_config_policy_source(self):
        snapshot = {"state": "pending", "targets": [], "policy": {}}
        completed = subprocess.CompletedProcess([], 0, json.dumps(snapshot), "")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pr_land, "run", return_value=completed
        ) as invoked:
            pr_land.watcher_snapshot(
                Path(directory), None, None, None, True, (), None, False
            )

        command = invoked.call_args.args[0]
        self.assertIn("--no-config", command)
        self.assertNotIn("--config", command)

    def test_readiness_policy_digest_binds_no_config_source(self):
        base = {
            "checkPolicy": "all",
            "strictChangesRequested": False,
            "requiredReviewers": [],
        }
        no_config = {
            "policy": {**base, "source": "no-config", "configPath": None}
        }
        discovered = {
            "policy": {
                **base,
                "source": "discovered-config",
                "configPath": "/repo/.pr-completion.json",
            }
        }
        self.assertNotEqual(
            pr_land.readiness_policy(no_config)[1],
            pr_land.readiness_policy(discovered)[1],
        )

    def test_confirm_fails_when_configured_reviewer_gate_disappears(self):
        pending = {
            "state": "pending",
            "targets": [
                {
                    "state": "pending",
                    "repository": "example/project",
                    "pr": {
                        "number": 7,
                        "url": "https://github.com/example/project/pull/7",
                        "headSha": "abc123",
                    },
                    "pending": [
                        {"type": "required_reviewers", "reviewers": ["alice"]}
                    ],
                }
            ],
        }
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=pending), mock.patch.object(
            pr_land, "run"
        ) as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--head",
                    "abc123",
                    "--mode",
                    "auto",
                    "--method",
                    "squash",
                    "--reviewer",
                    "alice",
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertEqual(json.loads(output.getvalue())["state"], "blocked")
        mutation.assert_not_called()

    def test_verified_target_rejects_queue_policy_flip(self):
        target = {
            "state": "ready",
            "repository": "example/project",
            "repositoryPolicy": {
                "mergeCommitAllowed": True,
                "rebaseMergeAllowed": True,
                "squashMergeAllowed": True,
            },
            "pr": {
                "number": 7,
                "url": "https://github.com/example/project/pull/7",
                "headSha": "abc123",
                "isMergeQueueEnabled": False,
            },
        }
        snapshot = {"state": "ready", "targets": [target]}
        with self.assertRaisesRegex(pr_land.LandingError, "queue is not enabled"):
            pr_land.verified_target(snapshot, "abc123", "queue", None)

        target["pr"]["isMergeQueueEnabled"] = True
        with self.assertRaisesRegex(pr_land.LandingError, "requires the merge queue"):
            pr_land.verified_target(snapshot, "abc123", "auto", "squash")

    def test_verified_target_rejects_disallowed_or_unknown_method_policy(self):
        target = {
            "state": "ready",
            "repository": "example/project",
            "repositoryPolicy": {
                "mergeCommitAllowed": True,
                "rebaseMergeAllowed": True,
                "squashMergeAllowed": False,
            },
            "pr": {
                "number": 7,
                "url": "https://github.com/example/project/pull/7",
                "headSha": "abc123",
                "isMergeQueueEnabled": False,
            },
        }
        snapshot = {"state": "ready", "targets": [target]}
        with self.assertRaisesRegex(pr_land.LandingError, "does not allow squash"):
            pr_land.verified_target(snapshot, "abc123", "auto", "squash")

        target["repositoryPolicy"]["squashMergeAllowed"] = None
        with self.assertRaisesRegex(pr_land.LandingError, "does not report"):
            pr_land.verified_target(snapshot, "abc123", "auto", "squash")

    def test_confirm_requires_unchanged_resolved_policy_digest(self):
        snapshot = {
            "state": "ready",
            "policy": {
                "source": "defaults",
                "configPath": None,
                "checkPolicy": "all",
                "strictChangesRequested": False,
                "requiredReviewers": ["alice"],
            },
            "targets": [
                {
                    "state": "ready",
                    "repository": "example/project",
                    "repositoryPolicy": {
                        "mergeCommitAllowed": True,
                        "rebaseMergeAllowed": True,
                        "squashMergeAllowed": True,
                    },
                    "pr": {
                        "number": 7,
                        "url": "https://github.com/example/project/pull/7",
                        "headSha": "abc123",
                        "isMergeQueueEnabled": False,
                    },
                }
            ],
        }
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land, "run"
        ) as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--head",
                    "abc123",
                    "--mode",
                    "auto",
                    "--method",
                    "squash",
                    "--reviewer",
                    "alice",
                    "--policy-digest",
                    "stale-policy",
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("policy changed", json.loads(output.getvalue())["errors"][0])
        mutation.assert_not_called()

    def test_confirm_emits_durable_landing_request_timestamp(self):
        snapshot = {
            "state": "ready",
            "policy": {
                "source": "defaults",
                "configPath": None,
                "checkPolicy": "all",
                "strictChangesRequested": False,
                "requiredReviewers": [],
            },
            "targets": [
                {
                    "state": "ready",
                    "repository": "example/project",
                    "repositoryPolicy": {
                        "mergeCommitAllowed": True,
                        "rebaseMergeAllowed": True,
                        "squashMergeAllowed": True,
                    },
                    "pr": {
                        "number": 7,
                        "url": "https://github.com/example/project/pull/7",
                        "headSha": "abc123",
                        "isMergeQueueEnabled": False,
                    },
                }
            ],
        }
        _policy, digest = pr_land.readiness_policy(snapshot)
        mutation_result = subprocess.CompletedProcess([], 0, "accepted", "")
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land, "run", return_value=mutation_result
        ) as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--head",
                    "abc123",
                    "--mode",
                    "auto",
                    "--method",
                    "squash",
                    "--policy-digest",
                    digest,
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_OK)
        value = json.loads(output.getvalue())
        self.assertEqual(value["state"], "landing_requested")
        self.assertTrue(value["requestedAt"].endswith("Z"))
        mutation.assert_called_once()

    def test_fixture_plan_requires_confirmation_without_mutating(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
                "--head",
                "head-ready",
                "--mode",
                "auto",
                "--method",
                "squash",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "confirmation_required")
        self.assertTrue(value["requiresConfirmation"])
        self.assertEqual(value["headSha"], "head-ready")
        self.assertIn("may merge", value["warning"])
        self.assertNotIn("command", value)

    def test_stale_fixture_head_is_blocked(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
                "--head",
                "older-head",
                "--mode",
                "queue",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, pr_land.EXIT_BLOCKED)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "blocked")
        self.assertIn("stale", value["errors"][0])

    def test_fixture_cannot_be_used_with_confirm(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
                "--head",
                "head-ready",
                "--mode",
                "queue",
                "--confirm",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, pr_land.EXIT_BLOCKED)
        self.assertIn("cannot authorize", json.loads(result.stdout)["errors"][0])


class ZeroStepCiWaiverTests(unittest.TestCase):
    def test_actionable_failure_without_explicit_waiver_remains_blocked(self):
        output = io.StringIO()
        with mock.patch.object(
            pr_land, "watcher_snapshot", return_value=zero_step_snapshot()
        ), mock.patch.object(pr_land, "run") as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--head",
                    WAIVER_HEAD,
                    "--mode",
                    "auto",
                    "--method",
                    "squash",
                    "--no-config",
                    "--check-policy",
                    "all",
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("not verified ready", json.loads(output.getvalue())["errors"][0])
        mutation.assert_not_called()

    def test_exact_head_zero_step_evidence_is_accepted_and_auditable(self):
        _target, audit, digest = pr_land.zero_step_waiver_target(
            zero_step_snapshot(),
            zero_step_evidence(),
            WAIVER_HEAD,
            "auto",
            "squash",
        )

        self.assertEqual(audit["HEAD_SHA"], WAIVER_HEAD)
        self.assertEqual(audit["SOURCE_VALIDATION_STATUS"], "PASS")
        self.assertEqual(audit["CI_TRANSPORT_STATUS"], "FAIL_ZERO_STEP")
        self.assertEqual(audit["CI_SOURCE_EXECUTION_STATUS"], "NONE")
        self.assertEqual(audit["OPERATOR_WAIVER"], "APPROVED")
        self.assertEqual(audit["LANDING_MODE"], "ZERO_STEP_CI_EXCEPTION")
        self.assertEqual(len(digest), 64)

    def test_plan_records_exception_and_confirmation_binds_evidence_digest(self):
        snapshot = zero_step_snapshot()
        policy, policy_digest = pr_land.readiness_policy(snapshot)
        evidence = zero_step_evidence()
        _normalized, evidence_digest = pr_land.validated_zero_step_waiver(
            evidence,
            WAIVER_HEAD,
            ["Source-only suite twice from clean state"],
        )
        mutation_result = subprocess.CompletedProcess([], 0, "accepted", "")
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "waiver.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(
                pr_land, "watcher_snapshot", return_value=snapshot
            ), mock.patch.object(
                pr_land, "verify_live_zero_step_evidence"
            ) as live_evidence, mock.patch.object(
                pr_land, "run", return_value=mutation_result
            ) as mutation, redirect_stdout(output):
                code = pr_land.main(
                    [
                        "--head",
                        WAIVER_HEAD,
                        "--mode",
                        "auto",
                        "--method",
                        "squash",
                        "--no-config",
                        "--check-policy",
                        "all",
                        "--zero-step-ci-waiver",
                        str(evidence_path),
                        "--policy-digest",
                        policy_digest,
                        "--waiver-evidence-digest",
                        evidence_digest,
                        "--confirm",
                    ]
                )

        self.assertEqual(code, pr_land.EXIT_OK)
        value = json.loads(output.getvalue())
        self.assertEqual(value["state"], "landing_requested")
        self.assertEqual(value["LANDING_MODE"], "ZERO_STEP_CI_EXCEPTION")
        self.assertEqual(value["zeroStepCiWaiverEvidenceDigest"], evidence_digest)
        self.assertEqual(value["readinessPolicy"], policy)
        live_evidence.assert_called_once()
        mutation.assert_called_once()

    def test_live_actions_job_and_run_prove_zero_execution(self):
        target, audit, _digest = pr_land.zero_step_waiver_target(
            zero_step_snapshot(),
            zero_step_evidence(),
            WAIVER_HEAD,
            "auto",
            "squash",
        )
        job = {
            "id": 96875663837,
            "run_id": 32515352499,
            "name": "Source-only suite twice from clean state",
            "head_sha": WAIVER_HEAD,
            "status": "completed",
            "conclusion": "failure",
            "runner_id": 0,
            "runner_name": "",
            "steps": [],
            "html_url": (
                "https://github.com/knudsonb2/merls-openclaw/actions/runs/"
                "32515352499/job/96875663837"
            ),
        }
        workflow_run = {
            "id": 32515352499,
            "head_sha": WAIVER_HEAD,
            "status": "completed",
            "conclusion": "failure",
        }
        results = [
            subprocess.CompletedProcess([], 0, json.dumps(job), ""),
            subprocess.CompletedProcess([], 0, json.dumps(workflow_run), ""),
        ]
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pr_land, "run", side_effect=results
        ) as invoked:
            pr_land.verify_live_zero_step_evidence(Path(directory), target, audit)

        self.assertEqual(invoked.call_count, 2)

    def test_live_actions_evidence_with_steps_is_rejected(self):
        target, audit, _digest = pr_land.zero_step_waiver_target(
            zero_step_snapshot(),
            zero_step_evidence(),
            WAIVER_HEAD,
            "auto",
            "squash",
        )
        job = {
            "id": 96875663837,
            "run_id": 32515352499,
            "name": "Source-only suite twice from clean state",
            "head_sha": WAIVER_HEAD,
            "status": "completed",
            "conclusion": "failure",
            "runner_id": 0,
            "runner_name": "",
            "steps": [{"name": "checkout", "conclusion": "failure"}],
            "html_url": (
                "https://github.com/knudsonb2/merls-openclaw/actions/runs/"
                "32515352499/job/96875663837"
            ),
        }
        workflow_run = {
            "id": 32515352499,
            "head_sha": WAIVER_HEAD,
            "status": "completed",
            "conclusion": "failure",
        }
        results = [
            subprocess.CompletedProcess([], 0, json.dumps(job), ""),
            subprocess.CompletedProcess([], 0, json.dumps(workflow_run), ""),
        ]
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            pr_land, "run", side_effect=results
        ):
            with self.assertRaisesRegex(pr_land.LandingError, "executed or unknown steps"):
                pr_land.verify_live_zero_step_evidence(Path(directory), target, audit)

    def test_changed_pr_head_is_rejected(self):
        with self.assertRaisesRegex(pr_land.LandingError, "stale"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot("b" * 40),
                zero_step_evidence(),
                WAIVER_HEAD,
                "auto",
                "squash",
            )

    def test_one_or_more_executed_steps_are_rejected(self):
        evidence = zero_step_evidence()
        evidence["ciFailureEvidence"][0]["executedStepCount"] = 1
        with self.assertRaisesRegex(pr_land.LandingError, "executedStepCount=0"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_actual_test_failure_is_rejected(self):
        evidence = zero_step_evidence()
        evidence["ciFailureEvidence"][0]["failureClass"] = "SOURCE_TEST_FAILURE"
        with self.assertRaisesRegex(pr_land.LandingError, "source/test failures"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_unknown_source_execution_state_is_rejected(self):
        evidence = zero_step_evidence()
        evidence["ciSourceExecutionStatus"] = "UNKNOWN"
        with self.assertRaisesRegex(pr_land.LandingError, "must equal NONE"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_missing_independent_validation_is_rejected(self):
        evidence = zero_step_evidence()
        evidence["independentValidation"]["requiredChecks"] = []
        with self.assertRaisesRegex(pr_land.LandingError, "must be non-empty"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_missing_operator_approval_is_rejected(self):
        evidence = zero_step_evidence()
        evidence["operatorApproval"]["status"] = "MISSING"
        with self.assertRaisesRegex(pr_land.LandingError, "approval must equal APPROVED"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_missing_waiver_reason_is_rejected(self):
        evidence = zero_step_evidence()
        evidence["waiverReason"] = ""
        with self.assertRaisesRegex(pr_land.LandingError, "waiverReason"):
            pr_land.zero_step_waiver_target(
                zero_step_snapshot(), evidence, WAIVER_HEAD, "auto", "squash"
            )

    def test_ordinary_unstable_pr_without_failed_checks_is_rejected(self):
        snapshot = zero_step_snapshot()
        snapshot["state"] = "pending"
        snapshot["targets"][0]["state"] = "pending"
        snapshot["targets"][0]["actions"] = []
        snapshot["targets"][0]["checks"]["fail"] = []
        with self.assertRaisesRegex(pr_land.LandingError, "actionable CI failure"):
            pr_land.zero_step_waiver_target(
                snapshot, zero_step_evidence(), WAIVER_HEAD, "auto", "squash"
            )

    def test_current_failed_checks_must_be_covered_exactly(self):
        snapshot = zero_step_snapshot()
        failures = ["Source-only suite twice from clean state", "Another failed check"]
        snapshot["targets"][0]["checks"]["fail"] = failures
        snapshot["targets"][0]["checks"]["total"] = 2
        snapshot["targets"][0]["actions"][0]["checks"] = failures
        snapshot["actions"][0]["checks"] = failures
        with self.assertRaisesRegex(pr_land.LandingError, "exactly cover"):
            pr_land.zero_step_waiver_target(
                snapshot, zero_step_evidence(), WAIVER_HEAD, "auto", "squash"
            )

    def test_required_check_filter_cannot_hide_failed_checks(self):
        snapshot = zero_step_snapshot()
        snapshot["policy"]["checkPolicy"] = "required"
        with self.assertRaisesRegex(pr_land.LandingError, "checkPolicy=all"):
            pr_land.zero_step_waiver_target(
                snapshot, zero_step_evidence(), WAIVER_HEAD, "auto", "squash"
            )

    def test_confirm_requires_unchanged_waiver_evidence_digest(self):
        snapshot = zero_step_snapshot()
        _policy, policy_digest = pr_land.readiness_policy(snapshot)
        evidence = zero_step_evidence()
        with tempfile.TemporaryDirectory() as directory:
            evidence_path = Path(directory) / "waiver.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(
                pr_land, "watcher_snapshot", return_value=snapshot
            ), mock.patch.object(
                pr_land, "verify_live_zero_step_evidence"
            ), mock.patch.object(pr_land, "run") as mutation, redirect_stdout(output):
                code = pr_land.main(
                    [
                        "--head",
                        WAIVER_HEAD,
                        "--mode",
                        "auto",
                        "--method",
                        "squash",
                        "--no-config",
                        "--check-policy",
                        "all",
                        "--zero-step-ci-waiver",
                        str(evidence_path),
                        "--policy-digest",
                        policy_digest,
                        "--waiver-evidence-digest",
                        "stale-evidence",
                        "--confirm",
                    ]
                )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("evidence changed", json.loads(output.getvalue())["errors"][0])
        mutation.assert_not_called()


if __name__ == "__main__":
    unittest.main()
