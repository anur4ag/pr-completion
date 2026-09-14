"""Protected landing under standing task authorization; no live GitHub mutations."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts/pr_land.py"
FIXTURES = Path(__file__).parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("pr_land", SCRIPT_PATH)
pr_land = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_land)
GH = "g" + "h"


def ready():
    return {"state": "ready", "policy": {"requiredReviewers": ["coderabbitai", "chatgpt-codex-connector"]},
            "targets": [{"state": "ready", "repository": "example/project",
                         "repositoryPolicy": {"mergeCommitAllowed": True, "rebaseMergeAllowed": True,
                                              "squashMergeAllowed": True},
                         "pr": {"number": 7, "url": "https://github.com/example/project/pull/7",
                                "headSha": "abc123", "isMergeQueueEnabled": False}}]}


class LandingTests(unittest.TestCase):
    args = ["--head", "abc123", "--mode", "auto", "--method", "squash"]

    def invoke(self, snapshot, args=None):
        output = io.StringIO()
        with mock.patch.object(pr_land, "watcher_snapshot", return_value=snapshot), mock.patch.object(
            pr_land, "run", return_value=subprocess.CompletedProcess([], 0, "accepted", "")
        ) as mutation, redirect_stdout(output):
            code = pr_land.main(args or self.args)
        return code, json.loads(output.getvalue()), mutation

    def test_authorized_invocation_lands_without_confirmation_roundtrip(self):
        code, value, mutation = self.invoke(ready())
        self.assertEqual(code, 0)
        self.assertEqual(value["state"], "landing_requested")
        self.assertTrue(value["requestedAt"].endswith("Z"))
        mutation.assert_called_once()
        self.assertEqual(mutation.call_args.args[0], [GH, "pr", "merge",
            "https://github.com/example/project/pull/7", "--match-head-commit", "abc123", "--auto", "--squash"])

    def test_new_head_is_revalidated_then_can_land_under_same_task_authorization(self):
        snapshot = ready()
        snapshot["targets"][0]["pr"]["headSha"] = "new-head"
        code, value, mutation = self.invoke(snapshot)
        self.assertEqual(code, 20)
        self.assertIn("head changed", value["errors"][0])
        mutation.assert_not_called()
        code, value, mutation = self.invoke(snapshot, ["--head", "new-head", *self.args[2:]])
        self.assertEqual(value["state"], "landing_requested")
        self.assertIn("new-head", mutation.call_args.args[0])

    def test_new_failure_or_review_gate_prevents_landing(self):
        for state in ("pending", "actionable", "blocked", "awaiting_merge"):
            with self.subTest(state=state):
                snapshot = ready()
                snapshot["state"] = state
                code, value, mutation = self.invoke(snapshot)
                self.assertEqual(code, 20)
                mutation.assert_not_called()

    def test_already_merged_duplicate_owner_does_not_mutate(self):
        code, value, mutation = self.invoke({"state": "merged", "targets": []})
        self.assertEqual(code, 0)
        self.assertEqual(value["state"], "merged")
        mutation.assert_not_called()

    def test_dry_run_is_optional_and_never_mutates(self):
        code, value, mutation = self.invoke(ready(), [*self.args, "--dry-run"])
        self.assertEqual(value["state"], "landing_planned")
        mutation.assert_not_called()

    def test_queue_policy_and_allowed_methods_are_enforced(self):
        snapshot = ready()
        snapshot["targets"][0]["pr"]["isMergeQueueEnabled"] = True
        code, _, mutation = self.invoke(snapshot)
        self.assertEqual(code, 20)
        mutation.assert_not_called()
        code, _, mutation = self.invoke(snapshot, ["--head", "abc123", "--mode", "queue"])
        self.assertEqual(code, 0)
        self.assertEqual(mutation.call_args.args[0][-2:], ["--match-head-commit", "abc123"])
        self.assertNotIn("--admin", mutation.call_args.args[0])
        for allowed in (False, None):
            snapshot = ready()
            snapshot["targets"][0]["repositoryPolicy"]["squashMergeAllowed"] = allowed
            code, _, mutation = self.invoke(snapshot)
            self.assertEqual(code, 20)
            mutation.assert_not_called()

    def test_invalid_modes_do_not_construct_alternate_mutations(self):
        with self.assertRaises(pr_land.LandingError):
            pr_land.landing_command("url", "sha", "auto", None)
        with self.assertRaises(pr_land.LandingError):
            pr_land.landing_command("url", "sha", "queue", "squash")

    def test_watcher_revalidation_preserves_policy_and_feedback_cursor(self):
        completed = subprocess.CompletedProcess([], 0, json.dumps(ready()), "")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(pr_land, "run", return_value=completed) as run:
            root = Path(directory)
            pr_land.watcher_snapshot(root, "7", None, root / "policy.json", False,
                                     ("coderabbitai", "chatgpt-codex-connector"), "required", True,
                                     root / "cursor.json", True)
            command = run.call_args.args[0]
            self.assertEqual(command.count("--reviewer"), 2)
            for flag, expected in (("--config", str(root / "policy.json")), ("--cursor", str(root / "cursor.json")),
                                   ("--check-policy", "required"), ("--mode", "once")):
                self.assertEqual(command[command.index(flag) + 1], expected)
            self.assertIn("--strict-changes-requested", command)
            self.assertIn("--require-approval", command)
            pr_land.watcher_snapshot(root, None, None, None, True, (), None, False)
            self.assertIn("--no-config", run.call_args.args[0])
            self.assertNotIn("--require-approval", run.call_args.args[0])
            pr_land.watcher_snapshot(root, None, None, None, False, (), None, False)
            self.assertNotIn("--no-config", run.call_args.args[0])

    def test_offline_fixture_can_only_plan(self):
        base = [sys.executable, str(SCRIPT_PATH), "--fixture", str(FIXTURES / "ready-to-merge.json"),
                "--head", "head-ready", "--mode", "auto", "--method", "squash"]
        refused = subprocess.run(base, capture_output=True, text=True, timeout=10)
        self.assertEqual(refused.returncode, 20)
        self.assertIn("cannot authorize", refused.stdout)
        planned = subprocess.run([*base, "--dry-run"], capture_output=True, text=True, timeout=10)
        self.assertEqual(planned.returncode, 0, planned.stderr + planned.stdout)
        self.assertEqual(json.loads(planned.stdout)["state"], "landing_planned")

    def test_command_timeout_requires_reconciliation(self):
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("command", 60)):
            with self.assertRaisesRegex(pr_land.LandingError, "reconcile"):
                pr_land.run(["command"], Path.cwd())


if __name__ == "__main__":
    unittest.main()
