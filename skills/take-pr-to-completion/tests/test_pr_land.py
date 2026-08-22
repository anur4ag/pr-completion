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
from datetime import datetime, timezone
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
HEAD_SHA = "a" * 40
TREE_SHA = "b" * 40
BASE_SHA = "c" * 40
EVIDENCE_DIGEST = "sha256:" + "d" * 64
NOW = datetime(2026, 8, 22, 12, 0, 0, tzinfo=timezone.utc)


def repository_config() -> dict[str, object]:
    return {
        "provider": "example-validator",
        "providerSchema": "example.readiness.v1",
        "verifier": "scripts/verify-readiness.py",
        "maxAgeSeconds": 3600,
        "blockOnExecutedCheckFailure": True,
    }


def repository_snapshot() -> dict[str, object]:
    return {
        "state": "pending",
        "policy": {
            "source": "discovered-config",
            "configPath": "/repo/.pr-completion.json",
            "checkPolicy": "all",
            "strictChangesRequested": False,
            "requiredReviewers": [],
            "repositoryReadiness": repository_config(),
        },
        "targets": [
            {
                "state": "pending",
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
                    "headSha": HEAD_SHA,
                    "baseSha": BASE_SHA,
                    "mergeable": "MERGEABLE",
                    "mergeStateStatus": "CLEAN",
                    "reviewDecision": None,
                    "isMergeQueueEnabled": False,
                },
                "checks": {
                    "total": 0,
                    "pass": [],
                    "fail": [],
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
                "actions": [],
                "pending": [
                    {
                        "type": "checks",
                        "checks": [],
                        "reason": "empty or missing check output",
                    }
                ],
            }
        ],
    }


def repository_attestation(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema": pr_land.REPOSITORY_READINESS_SCHEMA,
        "version": 1,
        "provider": {"id": "example-validator", "schema": "example.readiness.v1"},
        "repository": "example/project",
        "pullRequest": 7,
        "candidateHeadSha": HEAD_SHA,
        "candidateTreeSha": TREE_SHA,
        "baseSha": BASE_SHA,
        "readinessResult": "ready",
        "sourceValidationStatus": "PASS",
        "evidenceDigest": EVIDENCE_DIGEST,
        "evaluatedAt": "2026-08-22T11:30:00Z",
        "reviewResult": "PASS_WITH_NONBLOCKING_FOLLOWUP",
        "authority": {
            "operatorApprovalGranted": False,
            "mergeAuthorityGranted": False,
        },
    }
    value.update(changes)
    value["attestationDigest"] = pr_land.attestation_digest(value)
    return value


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


class RepositoryReadinessTests(unittest.TestCase):
    def validate(self, value: object) -> tuple[dict[str, object], str]:
        target = repository_snapshot()["targets"][0]
        assert isinstance(target, dict)
        return pr_land.validated_repository_readiness(
            value,
            config=repository_config(),
            target=target,
            expected_head=HEAD_SHA,
            candidate_tree=TREE_SHA,
            now=NOW,
        )

    def test_normalized_attestation_binds_exact_candidate(self):
        value = repository_attestation()
        normalized, digest = self.validate(value)
        self.assertEqual(normalized["candidateHeadSha"], HEAD_SHA)
        self.assertEqual(normalized["candidateTreeSha"], TREE_SHA)
        self.assertEqual(normalized["baseSha"], BASE_SHA)
        self.assertEqual(digest, value["attestationDigest"])

    def test_repository_path_accepts_absent_github_checks(self):
        snapshot = repository_snapshot()
        value = repository_attestation()
        policy, _digest = pr_land.readiness_policy(snapshot)
        with mock.patch.object(
            pr_land,
            "verified_repository_files",
            return_value=(TREE_SHA, Path("verify.py")),
        ), mock.patch.object(
            pr_land,
            "invoke_repository_verifier",
            return_value=value,
        ):
            target, normalized, digest = pr_land.repository_readiness_target(
                snapshot,
                Path("."),
                Path("artifact.json"),
                HEAD_SHA,
                "auto",
                "merge",
                policy,
                now=NOW,
            )

        self.assertEqual(target["checks"]["total"], 0)
        self.assertEqual(normalized["readinessResult"], "ready")
        self.assertEqual(digest, value["attestationDigest"])

    def test_external_plan_requires_confirmation_and_binds_digest(self):
        snapshot = repository_snapshot()
        value = repository_attestation()
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land,
            "repository_readiness_target",
            return_value=(snapshot["targets"][0], value, value["attestationDigest"]),
        ), mock.patch.object(pr_land, "run") as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--repository-readiness",
                    "artifact.json",
                    "--head",
                    HEAD_SHA,
                    "--mode",
                    "auto",
                    "--method",
                    "merge",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_OK)
        result = json.loads(output.getvalue())
        self.assertEqual(result["state"], "confirmation_required")
        self.assertEqual(result["readinessSource"], "repository")
        self.assertEqual(result["repositoryReadinessDigest"], value["attestationDigest"])
        self.assertTrue(result["requiresConfirmation"])
        mutation.assert_not_called()

    def test_external_confirmation_uses_existing_landing_command(self):
        snapshot = repository_snapshot()
        value = repository_attestation(
            evaluatedAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        _policy, policy_digest = pr_land.readiness_policy(snapshot)
        mutation_result = subprocess.CompletedProcess([], 0, "accepted", "")
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land,
            "repository_readiness_target",
            return_value=(snapshot["targets"][0], value, value["attestationDigest"]),
        ), mock.patch.object(
            pr_land, "run", return_value=mutation_result
        ) as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--repository-readiness",
                    "artifact.json",
                    "--repository-readiness-digest",
                    str(value["attestationDigest"]),
                    "--head",
                    HEAD_SHA,
                    "--mode",
                    "auto",
                    "--method",
                    "merge",
                    "--policy-digest",
                    policy_digest,
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_OK)
        self.assertEqual(json.loads(output.getvalue())["state"], "landing_requested")
        mutation.assert_called_once_with(
            [
                GH,
                "pr",
                "merge",
                "https://github.com/example/project/pull/7",
                "--match-head-commit",
                HEAD_SHA,
                "--auto",
                "--merge",
            ],
            Path(".").resolve(),
        )

    def test_external_confirmation_rechecks_live_state_after_verification(self):
        snapshot = repository_snapshot()
        changed = repository_snapshot()
        changed["targets"][0]["pr"]["baseSha"] = "e" * 40
        value = repository_attestation(
            evaluatedAt=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        )
        _policy, policy_digest = pr_land.readiness_policy(snapshot)
        output = io.StringIO()
        with mock.patch.object(
            pr_land, "watcher_snapshot", side_effect=[snapshot, changed]
        ), mock.patch.object(
            pr_land,
            "repository_readiness_target",
            return_value=(snapshot["targets"][0], value, value["attestationDigest"]),
        ), mock.patch.object(pr_land, "run") as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--repository-readiness",
                    "artifact.json",
                    "--repository-readiness-digest",
                    str(value["attestationDigest"]),
                    "--head",
                    HEAD_SHA,
                    "--mode",
                    "auto",
                    "--method",
                    "merge",
                    "--policy-digest",
                    policy_digest,
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("base changed", json.loads(output.getvalue())["errors"][0])
        mutation.assert_not_called()

    def test_unconfigured_or_unknown_provider_is_rejected(self):
        snapshot = repository_snapshot()
        policy, _digest = pr_land.readiness_policy(snapshot)
        policy["repositoryReadiness"] = None
        with self.assertRaisesRegex(pr_land.LandingError, "not configured"):
            pr_land.repository_readiness_target(
                snapshot, Path("."), Path("artifact.json"), HEAD_SHA, "auto", "merge", policy
            )
        value = repository_attestation(
            provider={"id": "unknown", "schema": "unknown.v1"}
        )
        with self.assertRaisesRegex(pr_land.LandingError, "provider identity"):
            self.validate(value)

    def test_malformed_and_unknown_schema_are_rejected(self):
        malformed = repository_attestation()
        malformed.pop("candidateTreeSha")
        with self.assertRaisesRegex(pr_land.LandingError, "fields are invalid"):
            self.validate(malformed)
        with self.assertRaisesRegex(pr_land.LandingError, "schema is unknown"):
            self.validate(repository_attestation(schema="other.v1"))

    def test_changed_head_tree_base_repository_or_pr_is_rejected(self):
        cases = (
            ({"candidateHeadSha": "e" * 40}, "head changed"),
            ({"candidateTreeSha": "e" * 40}, "tree changed"),
            ({"baseSha": "e" * 40}, "base changed"),
            ({"repository": "other/project"}, "repository identity"),
            ({"pullRequest": 8}, "pull request identity"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(
                pr_land.LandingError, message
            ):
                self.validate(repository_attestation(**changes))

    def test_stale_and_future_attestations_are_rejected(self):
        with self.assertRaisesRegex(pr_land.LandingError, "stale"):
            self.validate(repository_attestation(evaluatedAt="2026-08-22T10:00:00Z"))
        with self.assertRaisesRegex(pr_land.LandingError, "future"):
            self.validate(repository_attestation(evaluatedAt="2026-08-22T12:01:00Z"))

    def test_evidence_or_attestation_digest_mismatch_is_rejected(self):
        with self.assertRaisesRegex(pr_land.LandingError, "evidence digest"):
            self.validate(repository_attestation(evidenceDigest="not-a-digest"))
        tampered = repository_attestation()
        tampered["evidenceDigest"] = "sha256:" + "e" * 64
        with self.assertRaisesRegex(pr_land.LandingError, "attestation digest mismatch"):
            self.validate(tampered)

    def test_nonready_failed_or_authority_claiming_artifact_is_rejected(self):
        cases = (
            ({"readinessResult": "blocked"}, "result is not ready"),
            ({"sourceValidationStatus": "FAIL"}, "not PASS"),
            (
                {
                    "authority": {
                        "operatorApprovalGranted": True,
                        "mergeAuthorityGranted": False,
                    }
                },
                "cannot grant",
            ),
        )
        for changes, message in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(
                pr_land.LandingError, message
            ):
                self.validate(repository_attestation(**changes))

    def test_verifier_failure_is_rejected(self):
        snapshot = repository_snapshot()
        policy, _digest = pr_land.readiness_policy(snapshot)
        with mock.patch.object(
            pr_land,
            "verified_repository_files",
            return_value=(TREE_SHA, Path("verify.py")),
        ), mock.patch.object(
            pr_land,
            "invoke_repository_verifier",
            side_effect=pr_land.LandingError("configured verifier failed"),
        ), self.assertRaisesRegex(pr_land.LandingError, "configured verifier failed"):
            pr_land.repository_readiness_target(
                snapshot,
                Path("."),
                Path("artifact.json"),
                HEAD_SHA,
                "auto",
                "merge",
                policy,
            )

    def test_changed_live_head_review_or_merge_policy_is_rejected(self):
        cases = []
        changed_head = repository_snapshot()
        changed_head["targets"][0]["pr"]["headSha"] = "e" * 40
        cases.append((changed_head, "stale"))
        review = repository_snapshot()
        review["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        cases.append((review, "review gate"))
        merge = repository_snapshot()
        merge["targets"][0]["pr"]["mergeStateStatus"] = "UNKNOWN"
        cases.append((merge, "merge policy"))
        github_failure = repository_snapshot()
        github_failure["targets"][0]["actions"] = [{"type": "ci_failure"}]
        github_failure["targets"][0]["checks"]["fail"] = ["executed-test"]
        cases.append((github_failure, "watcher actions"))
        for snapshot, message in cases:
            policy, _digest = pr_land.readiness_policy(snapshot)
            with self.subTest(message=message), self.assertRaisesRegex(
                pr_land.LandingError, message
            ):
                pr_land.repository_readiness_target(
                    snapshot,
                    Path("."),
                    Path("artifact.json"),
                    HEAD_SHA,
                    "auto",
                    "merge",
                    policy,
                )

    def test_nonempty_or_ambiguous_check_pending_is_rejected(self):
        snapshot = repository_snapshot()
        snapshot["targets"][0]["pending"][0]["checks"] = ["running-test"]
        policy, _digest = pr_land.readiness_policy(snapshot)
        with self.assertRaisesRegex(pr_land.LandingError, "pending gate"):
            pr_land.repository_readiness_target(
                snapshot, Path("."), Path("artifact.json"), HEAD_SHA, "auto", "merge", policy
            )

    def test_confirmation_from_prior_attestation_is_rejected(self):
        snapshot = repository_snapshot()
        value = repository_attestation()
        _policy, policy_digest = pr_land.readiness_policy(snapshot)
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land,
            "repository_readiness_target",
            return_value=(snapshot["targets"][0], value, value["attestationDigest"]),
        ), mock.patch.object(pr_land, "run") as mutation, redirect_stdout(output):
            code = pr_land.main(
                [
                    "--repository-readiness",
                    "artifact.json",
                    "--repository-readiness-digest",
                    "sha256:" + "f" * 64,
                    "--head",
                    HEAD_SHA,
                    "--mode",
                    "auto",
                    "--method",
                    "merge",
                    "--policy-digest",
                    policy_digest,
                    "--confirm",
                ]
            )

        self.assertEqual(code, pr_land.EXIT_BLOCKED)
        self.assertIn("readiness changed", json.loads(output.getvalue())["errors"][0])
        mutation.assert_not_called()

    def test_config_and_verifier_must_be_tracked_at_exact_head(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            (repository / "scripts").mkdir()
            config = repository / ".pr-completion.json"
            verifier = repository / "scripts" / "verify-readiness.py"
            config.write_text("{}\n", encoding="utf-8")
            verifier.write_text("print('{}')\n", encoding="utf-8")
            subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.name", "PR Completion Test"],
                cwd=repository,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "test@example.invalid"],
                cwd=repository,
                check=True,
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "provider"], cwd=repository, check=True, capture_output=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            (repository / "candidate.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "add", "candidate.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "candidate"], cwd=repository, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repository,
                check=True,
                text=True,
                capture_output=True,
            ).stdout.strip()
            tree, resolved = pr_land.verified_repository_files(
                repository,
                head,
                base,
                str(config),
                "scripts/verify-readiness.py",
            )
            self.assertRegex(tree, r"^[0-9a-f]{40}$")
            self.assertEqual(resolved, verifier.resolve())
            verifier.write_text("raise RuntimeError()\n", encoding="utf-8")
            with self.assertRaises(pr_land.LandingError):
                pr_land.verified_repository_files(
                    repository,
                    head,
                    base,
                    str(config),
                    "scripts/verify-readiness.py",
                )

    def test_candidate_cannot_introduce_its_own_readiness_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            subprocess.run(["git", "init", "-b", "main"], cwd=repository, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "PR Completion Test"], cwd=repository, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repository, check=True)
            (repository / "base.txt").write_text("base\n", encoding="utf-8")
            subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "base"], cwd=repository, check=True, capture_output=True)
            base = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True
            ).stdout.strip()
            (repository / "scripts").mkdir()
            config = repository / ".pr-completion.json"
            config.write_text("{}\n", encoding="utf-8")
            (repository / "scripts" / "verify-readiness.py").write_text(
                "print('{}')\n", encoding="utf-8"
            )
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(["git", "commit", "-m", "self qualifying provider"], cwd=repository, check=True, capture_output=True)
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True
            ).stdout.strip()

            with self.assertRaisesRegex(pr_land.LandingError, "pre-exist unchanged"):
                pr_land.verified_repository_files(
                    repository,
                    head,
                    base,
                    str(config),
                    "scripts/verify-readiness.py",
                )

    def test_verifier_repository_mutation_is_detected(self):
        snapshot = repository_snapshot()
        policy, _digest = pr_land.readiness_policy(snapshot)
        observations = [(HEAD_SHA, TREE_SHA, ""), (HEAD_SHA, TREE_SHA, "? changed\n")]
        with mock.patch.object(
            pr_land,
            "verified_repository_files",
            return_value=(TREE_SHA, Path("verify.py")),
        ), mock.patch.object(
            pr_land, "invoke_repository_verifier", return_value=repository_attestation()
        ), mock.patch.object(
            pr_land, "repository_observation", side_effect=observations
        ), self.assertRaisesRegex(pr_land.LandingError, "changed the local repository"):
            pr_land.repository_readiness_target(
                snapshot,
                Path("."),
                Path("artifact.json"),
                HEAD_SHA,
                "auto",
                "merge",
                policy,
                now=NOW,
            )


if __name__ == "__main__":
    unittest.main()
