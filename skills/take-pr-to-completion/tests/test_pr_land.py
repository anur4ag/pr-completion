"""Guarded landing helper regressions."""

from __future__ import annotations

import importlib.util
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
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


def local_snapshot(head: str = "head-local") -> dict[str, object]:
    return {
        "state": "actionable",
        "policy": {
            "source": "defaults",
            "configPath": None,
            "checkPolicy": "required",
            "strictChangesRequested": False,
            "requiredReviewers": [],
        },
        "targets": [
            {
                "state": "actionable",
                "repository": "example/merls",
                "repositoryPolicy": {
                    "mergeCommitAllowed": True,
                    "rebaseMergeAllowed": True,
                    "squashMergeAllowed": True,
                },
                "pr": {
                    "number": 81,
                    "url": "https://github.com/example/merls/pull/81",
                    "state": "OPEN",
                    "headSha": head,
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "UNSTABLE",
                    "reviewDecision": "APPROVED",
                    "isMergeQueueEnabled": False,
                },
                "checks": {
                    "total": 1,
                    "pass": [],
                    "fail": ["tests"],
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
                "actions": [{"type": "ci_failure", "checks": ["tests"]}],
                "pending": [
                    {"type": "merge_state", "mergeStateStatus": "UNSTABLE"}
                ],
            }
        ],
    }


def write_local_evidence(
    directory: Path,
    *,
    head: str = "head-local",
    passed: int = 276,
    failed: int = 0,
    skipped: int = 0,
    live_changed: str | None = "no",
    validated_at: datetime | None = None,
) -> tuple[Path, str]:
    evidence_time = validated_at or datetime.now(timezone.utc)
    statuses = ["pass"] * passed + ["fail"] * failed + ["skip"] * skipped
    report: dict[str, object] = {
        "schema": pr_land.MERLS_REPORT_SCHEMA,
        "run": {
            "slug": "test-run-local",
            "profile": pr_land.MERLS_REQUIRED_PROFILE,
            "status": "pass" if failed == 0 and skipped == 0 else "fail",
            "completed_at": evidence_time.isoformat().replace("+00:00", "Z"),
            "result_summary": {
                key: statuses.count(key)
                for key in ("pass", "fail", "skip")
                if statuses.count(key)
            },
            "validation_summary": {
                "total_passes": passed,
                "failures": failed,
                "skips": skipped,
            },
        },
        "results": [
            {"test_id": f"test-{index:03d}", "status": status}
            for index, status in enumerate(statuses)
        ],
    }
    if live_changed is not None:
        report["live_changed"] = live_changed
    report_path = directory / "merls-suite.json"
    report_path.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
    report_checksum = hashlib.sha256(report_path.read_bytes()).hexdigest()

    command_result = {
        "schema": pr_land.LOCAL_COMMAND_RESULT_SCHEMA,
        "name": "focused landing validation",
        "headSha": head,
        "command": ["python3", "scripts/validate-focused.py"],
        "status": "pass",
        "exitCode": 0,
        "completedAt": evidence_time.isoformat().replace("+00:00", "Z"),
    }
    evidence = {
        "schema": pr_land.LOCAL_AUTHORITY_SCHEMA,
        "authority": pr_land.LOCAL_AUTHORITY_KIND,
        "repository": "example/merls",
        "prNumber": 81,
        "headSha": head,
        "validatedAt": evidence_time.isoformat().replace("+00:00", "Z"),
        "sourceTreeClean": True,
        "hostedChecks": {
            "status": "unavailable",
            "reasonCode": pr_land.HOSTED_UNAVAILABLE_REASON,
            "reason": "Repository account billing or spending limit prevented job startup.",
        },
        "authorization": {"type": "policy", "reference": "DEC-GOV-008"},
        "focusedTests": [command_result],
        "requiredValidators": [
            {**command_result, "name": "required Merls validator"}
        ],
        "suite": {
            "headSha": head,
            "reportPath": report_path.name,
            "reportSha256": report_checksum,
        },
    }
    evidence_path = directory / "local-authority.json"
    evidence_path.write_text(
        json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8"
    )
    return evidence_path, hashlib.sha256(evidence_path.read_bytes()).hexdigest()


def zero_step_runs(head: str = "head-local") -> tuple[list[dict[str, object]], dict[int, object]]:
    runs = [
        {
            "databaseId": 101,
            "workflowName": "CI",
            "status": "completed",
            "conclusion": "failure",
            "headSha": head,
            "event": "pull_request",
            "url": "https://github.com/example/merls/actions/runs/101",
        }
    ]
    views = {
        101: {
            "jobs": [
                {
                    "databaseId": 202,
                    "name": "tests",
                    "status": "completed",
                    "conclusion": "failure",
                    "steps": None,
                }
            ]
        }
    }
    return runs, views


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


class MerlsLocalAuthorityTests(unittest.TestCase):
    def validate_evidence(
        self,
        evidence_path: Path,
        checksum: str,
        *,
        expected_head: str = "head-local",
        now: datetime | None = None,
    ) -> dict[str, object]:
        target = local_snapshot(expected_head)["targets"][0]
        assert isinstance(target, dict)
        with mock.patch.object(
            pr_land, "validate_clean_exact_source"
        ), mock.patch.object(
            pr_land,
            "validate_merls_policy_references",
            return_value=list(pr_land.MERLS_POLICY_REQUIREMENTS),
        ):
            return pr_land.validate_local_evidence(
                Path(evidence_path.parent),
                evidence_path,
                checksum,
                expected_head,
                target,
                now=now,
            )

    def test_billing_zero_step_condition_with_exact_local_evidence_is_allowed(self):
        snapshot = local_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(Path(directory))
            output = io.StringIO()
            hosted = {
                "workflowCount": 1,
                "jobCount": 1,
                "runnableStepCount": 0,
                "workflows": [],
                "jobs": [],
            }
            with mock.patch.object(
                pr_land, "watcher_snapshot", return_value=snapshot
            ), mock.patch.object(
                pr_land, "validate_clean_exact_source"
            ), mock.patch.object(
                pr_land,
                "validate_merls_policy_references",
                return_value=list(pr_land.MERLS_POLICY_REQUIREMENTS),
            ), mock.patch.object(
                pr_land,
                "hosted_unavailability_observation",
                return_value=hosted,
            ), redirect_stdout(output):
                code = pr_land.main(
                    [
                        "--repo",
                        directory,
                        "--pr",
                        "81",
                        "--head",
                        "head-local",
                        "--mode",
                        "auto",
                        "--method",
                        "squash",
                        "--check-policy",
                        "required",
                        "--local-authority-evidence",
                        str(evidence_path),
                        "--local-authority-checksum",
                        checksum,
                    ]
                )

        self.assertEqual(code, pr_land.EXIT_OK)
        value = json.loads(output.getvalue())
        authority = value["validationAuthority"]
        self.assertEqual(value["state"], "confirmation_required")
        self.assertEqual(authority["hostedChecks"]["status"], "unavailable")
        self.assertEqual(authority["hostedChecks"]["runnableStepCount"], 0)
        self.assertEqual(
            authority["suiteResult"], {"passed": 276, "failed": 0, "skipped": 0}
        )
        self.assertFalse(authority["genericBypass"])

    def test_hosted_product_test_failure_is_blocked(self):
        runs, views = zero_step_runs()
        jobs = views[101]["jobs"]
        assert isinstance(jobs, list) and isinstance(jobs[0], dict)
        jobs[0]["steps"] = [
            {"name": "Run tests", "status": "completed", "conclusion": "failure"}
        ]
        with self.assertRaisesRegex(pr_land.LandingError, "ran steps and failed"):
            pr_land.validate_zero_step_hosted_runs(runs, views, "head-local", ["tests"])

    def test_canceled_or_timed_out_product_tests_are_blocked(self):
        for conclusion in ("cancelled", "timed_out"):
            with self.subTest(conclusion=conclusion):
                runs, views = zero_step_runs()
                jobs = views[101]["jobs"]
                assert isinstance(jobs, list) and isinstance(jobs[0], dict)
                jobs[0]["conclusion"] = conclusion
                with self.assertRaisesRegex(pr_land.LandingError, "remain blocking"):
                    pr_land.validate_zero_step_hosted_runs(
                        runs, views, "head-local", ["tests"]
                    )

    def test_evidence_from_wrong_sha_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(
                Path(directory), head="another-head"
            )
            with self.assertRaisesRegex(pr_land.LandingError, "another commit"):
                self.validate_evidence(evidence_path, checksum)

    def test_suite_from_wrong_sha_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, _checksum = write_local_evidence(Path(directory))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            evidence["suite"]["headSha"] = "another-head"
            evidence_path.write_text(
                json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8"
            )
            checksum = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            with self.assertRaisesRegex(pr_land.LandingError, "another commit"):
                self.validate_evidence(evidence_path, checksum)

    def test_invalid_evidence_checksum_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, _checksum = write_local_evidence(Path(directory))
            with self.assertRaisesRegex(pr_land.LandingError, "checksum is invalid"):
                self.validate_evidence(evidence_path, "0" * 64)

    def test_non_exact_suite_counts_are_blocked(self):
        cases = ((275, 0, 0), (275, 1, 0), (275, 0, 1))
        for passed, failed, skipped in cases:
            with self.subTest(passed=passed, failed=failed, skipped=skipped):
                with tempfile.TemporaryDirectory() as directory:
                    evidence_path, checksum = write_local_evidence(
                        Path(directory),
                        passed=passed,
                        failed=failed,
                        skipped=skipped,
                    )
                    with self.assertRaisesRegex(
                        pr_land.LandingError, "exactly|must contain|status is not pass"
                    ):
                        self.validate_evidence(evidence_path, checksum)

    def test_missing_live_changed_no_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(
                Path(directory), live_changed=None
            )
            with self.assertRaisesRegex(pr_land.LandingError, "LIVE_CHANGED=no"):
                self.validate_evidence(evidence_path, checksum)

    def test_stale_evidence_is_blocked(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(
                Path(directory),
                validated_at=now - timedelta(days=2),
            )
            with self.assertRaisesRegex(pr_land.LandingError, "stale"):
                self.validate_evidence(evidence_path, checksum, now=now)

    def test_non_clean_source_tree_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(Path(directory))
            target = local_snapshot()["targets"][0]
            assert isinstance(target, dict)
            with mock.patch.object(
                pr_land,
                "validate_clean_exact_source",
                side_effect=pr_land.LandingError("local authority requires a clean source tree"),
            ), mock.patch.object(
                pr_land,
                "validate_merls_policy_references",
                return_value=list(pr_land.MERLS_POLICY_REQUIREMENTS),
            ), self.assertRaisesRegex(pr_land.LandingError, "clean source tree"):
                pr_land.validate_local_evidence(
                    Path(directory),
                    evidence_path,
                    checksum,
                    "head-local",
                    target,
                )

    def test_pr_head_move_after_validation_is_blocked(self):
        snapshot = local_snapshot("moved-head")
        with tempfile.TemporaryDirectory() as directory:
            evidence_path, checksum = write_local_evidence(Path(directory))
            output = io.StringIO()
            with mock.patch.object(
                pr_land, "watcher_snapshot", return_value=snapshot
            ), mock.patch.object(pr_land, "run"), redirect_stdout(output):
                code = pr_land.main(
                    [
                        "--repo",
                        directory,
                        "--head",
                        "head-local",
                        "--mode",
                        "auto",
                        "--method",
                        "squash",
                        "--local-authority-evidence",
                        str(evidence_path),
                        "--local-authority-checksum",
                        checksum,
                    ]
                )
        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("stale", json.loads(output.getvalue())["errors"][0])

    def test_normal_hosted_green_plan_is_unchanged(self):
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
        self.assertNotIn("validationAuthority", value)


if __name__ == "__main__":
    unittest.main()
