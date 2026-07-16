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


if __name__ == "__main__":
    unittest.main()
