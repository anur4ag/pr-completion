"""Watcher and guarded autonomous-landing safety regressions.

Forbidden mutation payloads used as scanner inputs live under
tests/safety-scanner-fixtures/ (data-only, marker + path-aware exemption).
This module must not rely on a file-level marker to hide executable code.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = SKILL_ROOT.parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "pr_watch.py"
SAFETY_SCRIPT = PLUGIN_ROOT / "scripts" / "check-merge-ready-safety.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SAFETY_FIXTURES = Path(__file__).resolve().parent / "safety-scanner-fixtures"
SKILL_MD = SKILL_ROOT / "SKILL.md"
LANDER_PATH = SKILL_ROOT / "scripts" / "pr_land.py"


SPEC = importlib.util.spec_from_file_location("pr_watch", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load pr_watch module")
pr_watch = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = pr_watch
SPEC.loader.exec_module(pr_watch)

SAFETY_SPEC = importlib.util.spec_from_file_location(
    "check_merge_ready_safety",
    SAFETY_SCRIPT,
)
if SAFETY_SPEC is None or SAFETY_SPEC.loader is None:
    raise RuntimeError("could not load check-merge-ready-safety module")
safety = importlib.util.module_from_spec(SAFETY_SPEC)
sys.modules[SAFETY_SPEC.name] = safety
SAFETY_SPEC.loader.exec_module(safety)


def settings(
    fixture: Path | None = None,
    reviewers=(),
    strict_changes_requested=False,
    cursor_path=None,
    observations_path=None,
):
    return pr_watch.Settings(
        mode="once",
        interval_seconds=30,
        max_interval_seconds=120,
        timeout_seconds=0,
        jitter=0,
        max_errors=5,
        discover="current",
        max_depth=4,
        check_policy="all",
        policy_source="defaults",
        config_path=None,
        strict_changes_requested=strict_changes_requested,
        required_reviewers=tuple(reviewers),
        targets=(),
        cursor_path=cursor_path,
        observations_path=observations_path,
        await_merge_head=None,
        await_merge_mode=None,
        await_merge_since=None,
        await_merge_grace_seconds=60,
        fixture=fixture,
        pretty=False,
        verbose=False,
    )


class RepositoryDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        layout = json.loads((FIXTURES / "repository-layout.json").read_text())
        for entry in layout["entries"]:
            repository = self.root / entry["path"]
            repository.mkdir(parents=True, exist_ok=True)
            marker = repository / ".git"
            if entry["marker"] == "directory":
                marker.mkdir()
            else:
                marker.write_text(entry.get("content", "gitdir: ../gitdir\n"))
        self.repositories = pr_watch.scan_repositories(self.root, 4)

    def tearDown(self):
        self.temporary.cleanup()

    def test_current_repository_fixture(self):
        current = [target for target in self.repositories if target.kind == "current"]
        self.assertEqual([target.path for target in current], [self.root.resolve()])

    def test_nested_repository_fixture(self):
        nested = [target for target in self.repositories if target.kind == "nested"]
        self.assertEqual(
            [target.path for target in nested],
            [(self.root / "packages/nested-repository").resolve()],
        )

    def test_submodule_fixture(self):
        submodules = [target for target in self.repositories if target.kind == "submodule"]
        self.assertEqual(
            [target.path for target in submodules],
            [(self.root / "traycer-submodule").resolve()],
        )


class PullRequestStateTests(unittest.TestCase):
    def fixture_snapshot(self, name: str):
        path = FIXTURES / name
        return pr_watch.load_fixture(path, settings(path))

    def test_pending_ci_fixture(self):
        value = self.fixture_snapshot("pending-ci.json")
        self.assertEqual(value["state"], "pending")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_OBSERVED)
        self.assertEqual(value["targets"][0]["checks"]["pending"], ["compile"])

    def test_review_comment_fixture(self):
        value = self.fixture_snapshot("review-comment.json")
        self.assertEqual(value["state"], "actionable")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_OBSERVED)
        self.assertEqual(value["actions"][0]["type"], "review_threads")
        self.assertEqual(value["actions"][0]["count"], 1)

    def test_changes_requested_without_threads_while_checks_pending_waits_for_rerun(self):
        raw = json.loads((FIXTURES / "pending-ci.json").read_text())
        raw["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        value = pr_watch.snapshot(raw["targets"], settings())

        self.assertEqual(value["state"], "pending")
        self.assertNotIn("changes_requested", [action["type"] for action in value["actions"]])
        self.assertIn(
            "review_rerun",
            [item["type"] for item in value["targets"][0]["pending"]],
        )

    def test_changes_requested_without_pending_checks_remains_actionable(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        raw["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        value = pr_watch.snapshot(raw["targets"], settings())

        self.assertEqual(value["state"], "actionable")
        self.assertIn("changes_requested", [action["type"] for action in value["actions"]])

    def test_changes_requested_with_unresolved_threads_is_actionable_while_checks_pending(self):
        raw = json.loads((FIXTURES / "review-comment.json").read_text())
        raw["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        raw["targets"][0]["checks"] = [
            {"name": "review", "state": "IN_PROGRESS", "bucket": "pending"}
        ]
        value = pr_watch.snapshot(raw["targets"], settings())

        self.assertEqual(value["state"], "actionable")
        action_types = [action["type"] for action in value["actions"]]
        self.assertIn("review_threads", action_types)
        self.assertIn("changes_requested", action_types)

    def test_strict_changes_requested_restores_always_actionable_behavior(self):
        raw = json.loads((FIXTURES / "pending-ci.json").read_text())
        raw["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        value = pr_watch.snapshot(
            raw["targets"], settings(strict_changes_requested=True)
        )

        self.assertEqual(value["state"], "actionable")
        self.assertIn("changes_requested", [action["type"] for action in value["actions"]])

    def test_conflict_fixture(self):
        value = self.fixture_snapshot("conflict.json")
        self.assertEqual(value["state"], "actionable")
        self.assertIn("conflict", [action["type"] for action in value["actions"]])

    def test_ready_state_is_merge_ready_not_merge_action(self):
        value = self.fixture_snapshot("ready-to-merge.json")
        self.assertEqual(value["state"], "ready")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_OBSERVED)
        self.assertEqual(value["actions"], [])
        target = value["targets"][0]
        self.assertEqual(target["pr"]["headSha"], "head-ready")
        self.assertFalse(target["pr"]["autoMergeEnabled"])
        self.assertIsNone(target["pr"]["autoMerge"])
        self.assertEqual(target["checks"]["fail"], [])
        self.assertEqual(target["checks"]["pending"], [])
        self.assertEqual(target["reviews"]["unresolvedThreadCount"], 0)

    def test_unknown_check_bucket_is_not_ready(self):
        value = self.fixture_snapshot("unknown-check-bucket.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        pending_types = [item["type"] for item in value["targets"][0]["pending"]]
        self.assertIn("unknown_checks", pending_types)

    def test_empty_checks_is_not_ready(self):
        value = self.fixture_snapshot("empty-checks.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        self.assertTrue(
            any(item.get("type") == "checks" for item in value["targets"][0]["pending"])
        )

    def test_unstable_merge_state_is_not_ready(self):
        value = self.fixture_snapshot("unstable-merge-state.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        self.assertTrue(
            any(
                item.get("type") == "merge_state"
                and item.get("mergeStateStatus") == "UNSTABLE"
                for item in value["targets"][0]["pending"]
            )
        )

    def test_has_hooks_merge_state_is_not_ready(self):
        value = self.fixture_snapshot("has-hooks-merge-state.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        self.assertTrue(
            any(
                item.get("type") == "merge_state"
                and item.get("mergeStateStatus") == "HAS_HOOKS"
                for item in value["targets"][0]["pending"]
            )
        )

    def test_unknown_merge_state_is_not_ready(self):
        path = FIXTURES / "ready-to-merge.json"
        raw = json.loads(path.read_text())
        raw["targets"][0]["pr"]["mergeStateStatus"] = "UNKNOWN"
        value = pr_watch.snapshot(raw["targets"], settings())
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")

    def test_missing_head_sha_is_not_ready(self):
        value = self.fixture_snapshot("missing-head-sha.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        self.assertTrue(
            any(item.get("type") == "head_sha" for item in value["targets"][0]["pending"])
        )

    def test_incoherent_pass_with_failure_state_is_not_ready(self):
        value = self.fixture_snapshot("incoherent-pass-failure.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        details = []
        for item in value["targets"][0]["pending"]:
            if item.get("type") == "malformed_checks":
                details.extend(item.get("details") or [])
        self.assertTrue(
            any(item.get("reason") == "incoherent check bucket/state" for item in details),
            details,
        )
        # Must not be classified into a healthy pass bucket.
        self.assertEqual(value["targets"][0]["checks"]["pass"], [])

    def test_incoherent_pass_with_in_progress_state_is_not_ready(self):
        value = self.fixture_snapshot("incoherent-pass-in-progress.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        details = []
        for item in value["targets"][0]["pending"]:
            if item.get("type") == "malformed_checks":
                details.extend(item.get("details") or [])
        self.assertTrue(
            any(
                item.get("reason") == "incoherent check bucket/state"
                and item.get("state") == "IN_PROGRESS"
                for item in details
            ),
            details,
        )
        self.assertEqual(value["targets"][0]["checks"]["pass"], [])

    def test_malformed_check_rows_are_not_silently_dropped(self):
        value = self.fixture_snapshot("malformed-check-row.json")
        self.assertNotEqual(value["state"], "ready")
        self.assertEqual(value["state"], "pending")
        pending = value["targets"][0]["pending"]
        malformed = next(item for item in pending if item.get("type") == "malformed_checks")
        self.assertGreaterEqual(malformed["count"], 3)
        reasons = {detail.get("reason") for detail in malformed["details"]}
        self.assertIn("check row is not an object", reasons)
        self.assertIn("missing or empty check name", reasons)
        self.assertIn("missing or empty check bucket", reasons)
        self.assertEqual(value["targets"][0]["checks"]["malformed"], malformed["details"])
        # Valid row is retained.
        self.assertEqual(value["targets"][0]["checks"]["pass"], ["compile"])

    def test_external_auto_merge_is_terminal_read_only_with_provenance(self):
        value = self.fixture_snapshot("external-auto-merge.json")
        self.assertEqual(value["state"], "auto_merge")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_OBSERVED)
        self.assertEqual(value["actions"], [])
        target = value["targets"][0]
        self.assertTrue(target["pr"]["autoMergeEnabled"])
        provenance = target["pr"]["autoMerge"]
        self.assertIsInstance(provenance, dict)
        self.assertTrue(provenance["enabled"])
        self.assertEqual(provenance["enabledAt"], "2026-07-13T12:00:00Z")
        self.assertEqual(provenance["enabledBy"], {"login": "alice"})
        self.assertEqual(provenance["mergeMethod"], "SQUASH")

    def test_external_auto_merge_with_pending_ci_is_terminal(self):
        value = self.fixture_snapshot("external-auto-merge-pending-ci.json")
        self.assertEqual(value["state"], "auto_merge")
        self.assertEqual(value["actions"], [])
        provenance = value["targets"][0]["pr"]["autoMerge"]
        self.assertEqual(provenance["enabledBy"], {"login": "release-bot"})
        self.assertEqual(provenance["mergeMethod"], "SQUASH")
        # Pending gates remain visible for reporting but do not reclassify state.
        self.assertTrue(value["targets"][0]["pending"] or value["targets"][0]["checks"]["pending"])

    def test_await_merge_overrides_external_auto_merge_terminal_state(self):
        raw = json.loads((FIXTURES / "external-auto-merge.json").read_text())
        current_head = raw["targets"][0]["pr"]["headRefOid"]
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": current_head,
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "awaiting_merge")
        self.assertEqual(value["actions"], [])
        self.assertEqual(
            value["targets"][0]["pending"][0]["type"], "merge_completion"
        )
        self.assertTrue(value["targets"][0]["landingAuthorization"]["current"])

    def test_await_merge_blocks_when_auto_merge_enrollment_disappears(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-ready",
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["actions"][0]["type"], "landing_enrollment_missing")

    def test_await_merge_allows_bounded_enrollment_propagation_grace(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-ready",
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(
            raw["targets"], configured, allow_missing_landing_evidence=True
        )

        self.assertEqual(value["state"], "awaiting_merge")
        self.assertTrue(value["targets"][0]["pending"][0]["evidencePending"])

    def test_await_merge_queue_requires_current_enrollment(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        raw["targets"][0]["mergeQueueEntry"] = {
            "id": "MQE_1",
            "state": "QUEUED",
            "position": 2,
            "headCommit": {"oid": "head-ready"},
        }
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-ready",
                "await_merge_mode": "queue",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "awaiting_merge")
        self.assertEqual(
            value["targets"][0]["pr"]["mergeQueueEntry"]["state"], "QUEUED"
        )

    def test_await_merge_blocks_when_queue_enrollment_disappears(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-ready",
                "await_merge_mode": "queue",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["actions"][0]["type"], "landing_enrollment_missing")

    def test_await_merge_blocks_rejected_queue_enrollment(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        raw["targets"][0]["mergeQueueEntry"] = {
            "id": "MQE_1",
            "state": "UNMERGEABLE",
            "position": 2,
            "headCommit": {"oid": "head-ready"},
        }
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-ready",
                "await_merge_mode": "queue",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["actions"][0]["type"], "landing_enrollment_rejected")
        self.assertEqual(value["actions"][0]["queueState"], "UNMERGEABLE")

    def test_await_merge_blocks_when_authorized_head_is_stale(self):
        raw = json.loads((FIXTURES / "ready-to-merge.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "older-head",
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "blocked")
        action = value["actions"][0]
        self.assertEqual(action["type"], "authorization_stale")
        self.assertEqual(action["expectedHead"], "older-head")
        self.assertEqual(action["currentHead"], "head-ready")

    def test_external_auto_merge_with_failing_ci_is_terminal(self):
        value = self.fixture_snapshot("external-auto-merge-failing-ci.json")
        self.assertEqual(value["state"], "auto_merge")
        self.assertEqual(value["actions"], [])
        provenance = value["targets"][0]["pr"]["autoMerge"]
        self.assertEqual(provenance["enabledBy"], {"login": "alice"})
        self.assertEqual(provenance["mergeMethod"], "MERGE")
        self.assertEqual(provenance["commitHeadline"], "ship it")
        self.assertEqual(value["targets"][0]["checks"]["fail"], ["tests"])

    def test_empty_object_auto_merge_request_is_terminal_with_enabled_true(self):
        value = self.fixture_snapshot("external-auto-merge-empty-object.json")
        self.assertEqual(value["state"], "auto_merge")
        self.assertEqual(value["actions"], [])
        provenance = value["targets"][0]["pr"]["autoMerge"]
        self.assertEqual(provenance, {"enabled": True})
        self.assertTrue(value["targets"][0]["pr"]["autoMergeEnabled"])
        self.assertEqual(value["targets"][0]["checks"]["fail"], ["tests"])

    def test_merged_state_is_terminal_without_actions(self):
        value = self.fixture_snapshot("merged.json")
        self.assertEqual(value["state"], "merged")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_OBSERVED)
        self.assertEqual(value["actions"], [])
        self.assertEqual(value["targets"][0]["pr"]["state"], "MERGED")

    def test_await_merge_observes_merged_as_terminal_success(self):
        raw = json.loads((FIXTURES / "merged.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "head-merged",
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "merged")
        self.assertEqual(value["actions"], [])

    def test_await_merge_rejects_different_merged_head(self):
        raw = json.loads((FIXTURES / "merged.json").read_text())
        configured = settings()
        configured = pr_watch.Settings(
            **{
                **configured.__dict__,
                "await_merge_head": "authorized-head",
                "await_merge_mode": "auto",
            }
        )
        value = pr_watch.snapshot(raw["targets"], configured)

        self.assertEqual(value["state"], "blocked")
        self.assertEqual(value["actions"][0]["type"], "authorization_stale")

    def test_blocked_state_uses_nonzero_exit(self):
        value = self.fixture_snapshot("blocked.json")
        self.assertEqual(value["state"], "blocked")
        self.assertEqual(pr_watch.exit_code(value["state"]), pr_watch.EXIT_BLOCKED)
        self.assertIn(
            "blocked",
            [action["type"] for action in value["actions"]],
        )

    def test_required_reviewer_must_approve_current_head(self):
        path = FIXTURES / "ready-to-merge.json"
        raw = json.loads(path.read_text())
        raw["targets"][0]["pr"]["reviews"] = [
            {
                "author": {"login": "coderabbitai[bot]"},
                "state": "APPROVED",
                "submittedAt": "2026-07-13T00:00:00Z",
                "commit": {"oid": "older-head"},
            }
        ]
        value = pr_watch.snapshot(raw["targets"], settings(reviewers=("coderabbitai",)))
        self.assertEqual(value["state"], "pending")
        self.assertEqual(
            value["targets"][0]["reviews"]["missingRequiredReviewers"],
            ["coderabbitai"],
        )

    def test_stale_head_after_push_invalidates_prior_ready(self):
        """A push changes head SHA; prior ready classification must not stick."""
        path = FIXTURES / "ready-to-merge.json"
        before = json.loads(path.read_text())
        ready = pr_watch.snapshot(before["targets"], settings())
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["targets"][0]["pr"]["headSha"], "head-ready")

        after = json.loads(path.read_text())
        after["targets"][0]["pr"]["headRefOid"] = "head-after-push"
        after["targets"][0]["pr"]["reviews"] = [
            {
                "author": {"login": "coderabbitai[bot]"},
                "state": "APPROVED",
                "submittedAt": "2026-07-13T00:00:00Z",
                "commit": {"oid": "head-ready"},
            }
        ]
        restarted = pr_watch.snapshot(
            after["targets"],
            settings(reviewers=("coderabbitai",)),
        )
        self.assertNotEqual(restarted["state"], "ready")
        self.assertEqual(restarted["state"], "pending")
        self.assertEqual(
            restarted["targets"][0]["pr"]["headSha"],
            "head-after-push",
        )
        self.assertEqual(
            restarted["targets"][0]["reviews"]["missingRequiredReviewers"],
            ["coderabbitai"],
        )


class ConfigurationTests(unittest.TestCase):
    def test_no_config_policy_source_is_reported_and_suppresses_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".pr-completion.json").write_text(
                json.dumps({"version": 1, "checkPolicy": "required"}),
                encoding="utf-8",
            )
            args = pr_watch.argument_parser().parse_args(["--no-config"])
            value = pr_watch.build_settings(args, root)
            resolved = pr_watch.resolved_config(value)

        self.assertEqual(value.check_policy, "all")
        self.assertEqual(value.policy_source, "no-config")
        self.assertIsNone(value.config_path)
        self.assertEqual(resolved["policySource"], "no-config")
        self.assertIsNone(resolved["configPath"])

    def test_default_cursor_uses_repository_git_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".git").mkdir()
            args = pr_watch.argument_parser().parse_args(["--no-config"])
            value = pr_watch.build_settings(args, root)

            self.assertEqual(value.cursor_path, pr_watch.default_cursor_path(root))
            self.assertEqual(
                value.cursor_path,
                root.resolve() / ".git" / "pr-completion" / "pr-watch-cursors.json",
            )

    def test_cli_values_override_repository_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".pr-completion.json"
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "mode": "once",
                        "intervalSeconds": 45,
                        "requiredReviewers": ["coderabbitai"],
                        "targets": [{"path": ".", "pr": 123}],
                    }
                )
            )
            args = pr_watch.argument_parser().parse_args(
                [
                    "--config",
                    str(config),
                    "--interval",
                    "5",
                    "--reviewer",
                    "codex",
                ]
            )
            value = pr_watch.build_settings(args, root)
            self.assertEqual(value.interval_seconds, 5)
            self.assertEqual(value.required_reviewers, ("codex",))
            self.assertEqual(value.targets[0].path, root.resolve())
            self.assertEqual(value.targets[0].selector, "123")
            resolved = pr_watch.resolved_config(value)
            self.assertEqual(resolved["intervalSeconds"], 5)
            self.assertEqual(resolved["targets"][0]["pr"], "123")

    def test_new_config_and_cli_fields_are_resolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / ".pr-completion.json"
            config.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "strictChangesRequested": False,
                        "cursorPath": "state/cursor.json",
                        "observationsPath": "state/observations.ndjson",
                    }
                )
            )
            args = pr_watch.argument_parser().parse_args(
                [
                    "--config",
                    str(config),
                    "--strict-changes-requested",
                    "--cursor",
                    str(root / "override-cursor.json"),
                    "--observations-file",
                    str(root / "override-observations.ndjson"),
                ]
            )
            value = pr_watch.build_settings(args, root)
            resolved = pr_watch.resolved_config(value)

            self.assertTrue(value.strict_changes_requested)
            self.assertEqual(value.cursor_path, (root / "override-cursor.json").resolve())
            self.assertEqual(
                value.observations_path,
                (root / "override-observations.ndjson").resolve(),
            )
            self.assertTrue(resolved["strictChangesRequested"])
            self.assertEqual(
                resolved["cursorPath"], str((root / "override-cursor.json").resolve())
            )
            self.assertEqual(
                resolved["observationsPath"],
                str((root / "override-observations.ndjson").resolve()),
            )

    def test_await_merge_is_cli_only_and_single_target(self):
        parser = pr_watch.argument_parser()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = parser.parse_args(
                [
                    "--no-config",
                    "--target",
                    ".=1",
                    "--target",
                    ".=2",
                    "--await-merge",
                    "head-ready",
                    "--await-merge-mode",
                    "auto",
                ]
            )
            with self.assertRaisesRegex(pr_watch.WatchError, "exactly one"):
                pr_watch.build_settings(args, root)

    def test_await_merge_grace_is_finite_and_capped(self):
        parser = pr_watch.argument_parser()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for invalid in ("inf", "nan", "60.1"):
                args = parser.parse_args(
                    [
                        "--no-config",
                        "--target",
                        ".=1",
                        "--await-merge",
                        "head-ready",
                        "--await-merge-mode",
                        "auto",
                        "--await-merge-since",
                        "2026-01-01T00:00:00Z",
                        "--await-merge-grace",
                        invalid,
                    ]
                )
                with self.assertRaises(pr_watch.WatchError, msg=invalid):
                    pr_watch.build_settings(args, root)

    def test_await_merge_requires_durable_request_timestamp(self):
        parser = pr_watch.argument_parser()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = parser.parse_args(
                [
                    "--no-config",
                    "--target",
                    ".=1",
                    "--await-merge",
                    "head-ready",
                    "--await-merge-mode",
                    "auto",
                ]
            )
            with self.assertRaisesRegex(pr_watch.WatchError, "await-merge-since"):
                pr_watch.build_settings(args, root)


class BackgroundRunnerTests(unittest.TestCase):
    def test_actionable_observation_is_process_success_with_consumable_json(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--fixture",
                str(FIXTURES / "review-comment.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "actionable")
        self.assertEqual(value["actions"][0]["type"], "review_threads")

    def test_ready_fixture_process_exits_zero_without_actions(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "ready")
        self.assertEqual(value["actions"], [])

    def test_external_auto_merge_process_exits_zero(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--fixture",
                str(FIXTURES / "external-auto-merge.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "auto_merge")

    def test_awaiting_merge_keeps_until_actionable_process_alive(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
                "--await-merge",
                "head-ready",
                "--await-merge-mode",
                "auto",
                "--await-merge-since",
                pr_watch.utc_now(),
                "--interval",
                "0.01",
                "--max-interval",
                "0.01",
                "--jitter",
                "0",
                "--timeout",
                "0.04",
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, pr_watch.EXIT_TIMEOUT, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "timeout")

    def test_blocked_observation_remains_process_failure(self):
        self.assertEqual(pr_watch.exit_code("blocked"), pr_watch.EXIT_BLOCKED)
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "once",
                "--fixture",
                str(FIXTURES / "blocked.json"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, pr_watch.EXIT_BLOCKED, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["state"], "blocked")

    def test_cursor_suppresses_repeated_actionable_and_emits_changed_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cursor = root / "cursor.json"
            changed_fixture = root / "changed.json"
            raw = json.loads((FIXTURES / "review-comment.json").read_text())
            raw["targets"][0]["pr"]["headRefOid"] = "head-review-changed"
            changed_fixture.write_text(json.dumps(raw))
            common = [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--cursor",
                str(cursor),
                "--interval",
                "0.01",
                "--max-interval",
                "0.01",
                "--jitter",
                "0",
            ]

            first = subprocess.run(
                [*common, "--fixture", str(FIXTURES / "review-comment.json")],
                text=True,
                capture_output=True,
                check=False,
            )
            repeated = subprocess.run(
                [
                    *common,
                    "--fixture",
                    str(FIXTURES / "review-comment.json"),
                    "--timeout",
                    "0.04",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            changed = subprocess.run(
                [*common, "--fixture", str(changed_fixture)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(first.stdout)["state"], "actionable")
            self.assertEqual(repeated.returncode, pr_watch.EXIT_TIMEOUT, repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["state"], "timeout")
            self.assertEqual(changed.returncode, 0, changed.stderr)
            changed_value = json.loads(changed.stdout)
            self.assertEqual(changed_value["state"], "actionable")
            self.assertEqual(
                changed_value["targets"][0]["pr"]["headSha"], "head-review-changed"
            )

    def test_terminal_state_exits_even_when_cursor_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = Path(directory) / "cursor.json"
            command = [
                sys.executable,
                str(SCRIPT_PATH),
                "--mode",
                "until-actionable",
                "--cursor",
                str(cursor),
                "--fixture",
                str(FIXTURES / "ready-to-merge.json"),
            ]

            first = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            second = subprocess.run(
                command, text=True, capture_output=True, check=False
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(first.stdout)["state"], "ready")
            self.assertEqual(json.loads(second.stdout)["state"], "ready")

    def test_observations_file_appends_valid_ndjson_across_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            observations = Path(directory) / "observations.ndjson"
            for fixture in ("pending-ci.json", "ready-to-merge.json"):
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPT_PATH),
                        "--mode",
                        "once",
                        "--observations-file",
                        str(observations),
                        "--fixture",
                        str(FIXTURES / fixture),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

            lines = observations.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            values = [json.loads(line) for line in lines]
            self.assertEqual([value["state"] for value in values], ["pending", "ready"])

    def test_strict_changes_requested_flag_restores_actionable_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "changes-requested-pending.json"
            raw = json.loads((FIXTURES / "pending-ci.json").read_text())
            raw["targets"][0]["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
            fixture.write_text(json.dumps(raw))
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    "--mode",
                    "until-actionable",
                    "--strict-changes-requested",
                    "--fixture",
                    str(fixture),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            value = json.loads(result.stdout)
            self.assertEqual(value["state"], "actionable")
            self.assertIn(
                "changes_requested", [action["type"] for action in value["actions"]]
            )


class GuardedLandingSafetyContractTests(unittest.TestCase):
    # Use the real skill text as the synthetic-bundle contract (already scanned).
    CONTRACT_SKILL = SKILL_MD.read_text(encoding="utf-8")

    def _payload(self, name: str) -> str:
        path = SAFETY_FIXTURES / name
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def _payload_body(self, name: str) -> str:
        """Fixture body without leading marker comment lines."""
        lines = self._payload(name).splitlines()
        body: list[str] = []
        for line in lines:
            if line.strip().startswith("#") and safety.TEST_DATA_EXEMPTION_MARKER in line:
                continue
            if line.strip().startswith("# Intended destination"):
                continue
            body.append(line)
        return "\n".join(body).lstrip("\n") + ("\n" if body else "")

    def test_skill_requires_per_pr_exact_head_confirmation(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        lowered = text.lower()
        self.assertIn("explicit per-pr confirmation", lowered)
        self.assertIn("current head sha", lowered)
        self.assertIn("scripts/pr_land.py", lowered)
        self.assertIn("may " + "merge " + "immediately", lowered)
        self.assertIn("never use `--admin`", lowered)
        self.assertIn("silence", lowered)
        self.assertIn("--policy-digest", lowered)
        self.assertIn("--await-merge-since", lowered)

    def test_skill_requires_background_json_consumption(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("always read and parse the durable output", text.lower())
        self.assertIn("before yielding or ending the turn", text.lower())

    def test_skill_requires_restart_after_push(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("a push invalidates every prior observation", text.lower())
        self.assertIn("relaunch against the new head", text.lower())

    def test_skill_observes_external_auto_merge_without_reconfiguration(self):
        text = SKILL_MD.read_text(encoding="utf-8")
        self.assertIn("another actor already configured auto-merge", text.lower())
        self.assertIn("never", text.lower())
        self.assertIn("disable an externally configured landing action", text.lower())

    def test_commit_skill_has_direct_handoff_and_phase_only_recursion_guard(self):
        text = (
            PLUGIN_ROOT / "skills" / "commit-workspace-changes" / "SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Direct lifecycle mode", text)
        self.assertIn("Phase-only child mode", text)
        self.assertIn("Explicit local-only mode", text)
        self.assertIn("exactly once", text)

    def test_release_safety_check_passes_public_skill_bundle(self):
        self.assertTrue(SAFETY_SCRIPT.is_file(), SAFETY_SCRIPT)
        result = subprocess.run(
            [sys.executable, str(SAFETY_SCRIPT), "--root", str(PLUGIN_ROOT)],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def _bundle_with(self, relative_path: str, content: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "skills" / "take-pr-to-completion"
            skill_dir.mkdir(parents=True)
            (root / ".claude-plugin").mkdir()
            (skill_dir / "SKILL.md").write_text(self.CONTRACT_SKILL, encoding="utf-8")
            lander = skill_dir / "scripts" / "pr_land.py"
            lander.parent.mkdir(parents=True, exist_ok=True)
            lander.write_text(LANDER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            lander.chmod(0o755)
            target = skill_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            # Match production helpers that ship as executable scripts.
            if relative_path.endswith((".sh", ".py")) and "scripts/" in relative_path:
                target.chmod(0o755)
            if relative_path.endswith(".sh") and "/helpers/" in relative_path:
                target.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(SAFETY_SCRIPT), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            return result.returncode, result.stderr

    def _bundle_with_runtime_symlink(
        self,
        outside: bool,
        alias_name: str = "alias.py",
        broken: bool = False,
    ) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / "skills" / "take-pr-to-completion"
            skill_dir.mkdir(parents=True)
            (root / ".claude-plugin").mkdir()
            (skill_dir / "SKILL.md").write_text(self.CONTRACT_SKILL, encoding="utf-8")
            scripts = skill_dir / "scripts"
            scripts.mkdir()
            lander = scripts / "pr_land.py"
            lander.write_text(LANDER_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            lander.chmod(0o755)
            target = root / "missing.py" if broken else root / "outside.py" if outside else lander
            if outside and not broken:
                target.write_text("print('outside')\n", encoding="utf-8")
                target.chmod(0o755)
            alias = scripts / alias_name
            try:
                alias.symlink_to(target)
            except OSError as error:
                self.skipTest(f"symlinks unavailable on this platform: {error}")
            result = subprocess.run(
                [sys.executable, str(SAFETY_SCRIPT), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            return result.returncode, result.stderr

    def test_release_safety_check_rejects_cli_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-cli-merge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_rejects_in_repo_runtime_symlink(self):
        code, err = self._bundle_with_runtime_symlink(outside=False)
        self.assertEqual(code, 1, err)
        self.assertIn("symlinks are forbidden", err)

    def test_release_safety_check_rejects_out_of_root_runtime_symlink(self):
        code, err = self._bundle_with_runtime_symlink(outside=True)
        self.assertEqual(code, 1, err)
        self.assertIn("symlinks are forbidden", err)

    def test_release_safety_check_rejects_broken_excluded_suffix_symlink(self):
        code, err = self._bundle_with_runtime_symlink(
            outside=True,
            alias_name="broken.pyc",
            broken=True,
        )
        self.assertEqual(code, 1, err)
        self.assertIn("symlinks are forbidden", err)

    def test_release_safety_check_rejects_lander_without_confirmation_guard(self):
        content = LANDER_PATH.read_text(encoding="utf-8").replace(
            '"--confirm"', '"--approve-without-guard"'
        )
        code, err = self._bundle_with("scripts/pr_land.py", content)
        self.assertEqual(code, 1, err)
        self.assertIn("explicit confirmation flag", err)

    def test_release_safety_check_rejects_lander_confirmation_control_flow_bypass(self):
        content = LANDER_PATH.read_text(encoding="utf-8").replace(
            "if not args.confirm:", "if False:", 1
        )
        code, err = self._bundle_with("scripts/pr_land.py", content)
        self.assertEqual(code, 1, err)
        self.assertIn("audited runtime digest changed", err)

    def test_release_safety_check_rejects_second_merge_surface_in_lander(self):
        content = LANDER_PATH.read_text(encoding="utf-8") + (
            "\nUNSAFE = [\"g\" + \"h\", \"pr\", \"merge\", \"--admin\"]\n"
        )
        code, err = self._bundle_with("scripts/pr_land.py", content)
        self.assertEqual(code, 1, err)
        self.assertIn("no admin bypass", err)

    def test_release_safety_check_rejects_mixed_negation_bypass(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-mixed-negation.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_rejects_mixed_negation_worry_bypass(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-mixed-negation-worry.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("enable " + "auto-merge", err.lower())

    def test_release_safety_check_rejects_rest_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-rest-merge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("REST pull merge endpoint", err)

    def test_release_safety_check_rejects_graphql_automerge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-graphql-automerge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("enablePull" + "RequestAutoMerge", err)

    def test_release_safety_check_rejects_python_subprocess_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-python-subprocess.txt"),
        )
        self.assertEqual(code, 1, err)
        subprocess_label = "subprocess " + "gh " + "pr " + "merge"
        argv_label = "python argv " + "gh " + "pr " + "merge"
        self.assertTrue(subprocess_label in err or argv_label in err, err)

    def test_release_safety_check_rejects_constant_folded_python_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            'import subprocess\nsubprocess.run(["g" + "h", "pr", "merge", "--admin"])\n',
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_rejects_subprocess_module_alias(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            'import subprocess as sp\nsp.run(["g" + "h", "pr", "merge", "--admin"])\n',
        )
        self.assertEqual(code, 1, err)
        self.assertIn("audited digest allowlist", err)

    def test_release_safety_check_rejects_subprocess_function_alias(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            'from subprocess import run\nrun(["g" + "h", "pr", "merge", "--admin"])\n',
        )
        self.assertEqual(code, 1, err)
        self.assertIn("audited digest allowlist", err)

    def test_release_safety_check_rejects_constant_folded_force_push(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            'import subprocess\nsubprocess.run(["git", "push", "--" + "force", "origin", "HEAD"])\n',
        )
        self.assertEqual(code, 1, err)
        self.assertIn("git " + "push " + "--" + "force", err)

    def test_release_safety_check_rejects_unclassifiable_process_command(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            'import subprocess\ncommand = get_command()\nsubprocess.run(command)\n',
        )
        self.assertEqual(code, 1, err)
        self.assertIn("unclassifiable process command", err)

    def test_release_safety_check_rejects_wrapped_cli_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.sh",
            self._payload_body("payload-wrapped-cli.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_rejects_variable_expanded_shell_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.sh",
            "#!/bin/sh\ntool=gh\n$tool pr merge --squash\n",
        )
        self.assertEqual(code, 1, err)
        self.assertIn("dynamic shell command", err)

    def test_release_safety_check_rejects_quoted_shell_command_expansion(self):
        for payload in (
            'command="gh pr"\n$command merge 7\n',
            'tool="gh"\n"$tool" pr merge --squash\n',
            'tool="gh"\n"${tool}" pr merge --squash\n',
        ):
            code, err = self._bundle_with("scripts/bad.sh", "#!/bin/sh\n" + payload)
            self.assertEqual(code, 1, err)
            self.assertIn("audited digest allowlist", err)

    def test_release_safety_check_rejects_force_push(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-git-force.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("git " + "push " + "--" + "force", err)

    def test_release_safety_check_rejects_gh_api_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-gh-api-merge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh api", err.lower())

    def test_release_safety_check_rejects_alias_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.sh",
            self._payload_body("payload-alias-merge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertTrue(
            "alias" in err.lower() or ("gh " + "pr " + "merge") in err,
            err,
        )

    def test_release_safety_check_rejects_gh_alias_set_merge(self):
        code, err = self._bundle_with(
            "scripts/bad.sh",
            self._payload_body("payload-gh-alias-set.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("alias", err.lower())

    def test_release_safety_check_rejects_reordered_auto_merge_phrase(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-reordered-automerge.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("auto" + "-merge", err.lower())

    def test_release_safety_check_rejects_test_executable_without_exemption(self):
        code, err = self._bundle_with(
            "tests/helpers/merge.sh",
            self._payload_body("payload-test-helper-no-marker.sh.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_rejects_marker_on_production_script(self):
        """Exact regression: marker in skills/**/scripts/bad.py must not exempt."""
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload("payload-production-marker-bypass.py.txt"),
        )
        self.assertEqual(code, 1, err)
        subprocess_label = "subprocess " + "gh " + "pr " + "merge"
        argv_label = "python argv " + "gh " + "pr " + "merge"
        self.assertTrue(
            ("gh " + "pr " + "merge") in err
            or subprocess_label in err
            or argv_label in err,
            err,
        )
        # Confirm the payload exercised admin merge without a contiguous command phrase here.
        self.assertIn("--" + "admin", self._payload("payload-production-marker-bypass.py.txt"))

    def test_release_safety_check_rejects_marker_on_executable_test_helper(self):
        code, err = self._bundle_with(
            "tests/helpers/merge.sh",
            self._payload("payload-test-helper-with-marker.sh.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_allows_data_only_fixture_subtree(self):
        code, err = self._bundle_with(
            "tests/safety-scanner-fixtures/allowed-data-only.txt",
            self._payload("allowed-data-only.txt"),
        )
        self.assertEqual(code, 0, err)

    def test_release_safety_check_rejects_marker_on_python_under_fixture_tree(self):
        # Executable-class suffixes under the fixture tree are still scanned.
        code, err = self._bundle_with(
            "tests/safety-scanner-fixtures/evil.py",
            "# "
            + safety.TEST_DATA_EXEMPTION_MARKER
            + "\n"
            + self._payload_body("payload-python-subprocess.txt"),
        )
        self.assertEqual(code, 1, err)

    def test_release_safety_check_rejects_broad_do_not_as_authorization_cover(self):
        code, err = self._bundle_with(
            "scripts/bad.py",
            self._payload_body("payload-broad-do-not.txt"),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("gh " + "pr " + "merge", err)

    def test_release_safety_check_allows_known_prohibition_language(self):
        findings = safety.check_skill_bundle(PLUGIN_ROOT)
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
