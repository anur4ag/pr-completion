"""Regressions drawn from the Codex/Claude lifecycle audit; all GitHub calls mocked."""
import copy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from unittest import mock

from test_pr_watch import pr_watch as w, settings, FIXTURES


def raw():
    return json.loads((FIXTURES / "ready-to-merge.json").read_text())["targets"][0]


def classify(value, configured=None):
    return w.snapshot([value], configured or settings(reviewers=tuple(w.DEFAULTS["requiredReviewers"])))


class ReviewLifecycleTests(unittest.TestCase):
    def test_native_required_policy_distinguishes_empty_from_missing_checks(self):
        configured = replace(settings(), check_policy="required")
        value = raw()
        value["pr"]["mergeStateStatus"] = "UNSTABLE"
        self.assertEqual(classify(value, configured)["state"], "ready")
        value["checks"] = []
        self.assertEqual(classify(value, configured)["state"], "ready")
        for missing in (None, {}, [None]):
            value["checks"] = missing
            self.assertNotEqual(classify(value, configured)["state"], "ready")
        value.pop("checks")
        self.assertNotEqual(classify(value, configured)["state"], "ready")

    def test_queue_admission_preserves_known_gates(self):
        value = raw()
        value.update(isMergeQueueEnabled=True)
        value["pr"]["mergeStateStatus"] = "BLOCKED"
        self.assertEqual(classify(value)["state"], "ready")
        value["checks"][0].update(bucket="fail", state="FAILURE")
        self.assertEqual(classify(value)["state"], "actionable")

    def test_default_review_policy_cannot_skip_both_bots(self):
        value = raw()
        value["pr"]["reviews"] = []
        value["pr"]["reviewDecision"] = ""
        result = classify(value)
        self.assertNotEqual(result["state"], "ready")
        self.assertEqual(result["targets"][0]["reviews"]["missingRequiredReviewers"], w.DEFAULTS["requiredReviewers"])

    def test_comment_does_not_erase_approval_or_force_another_pass_on_new_head(self):
        value = raw()
        value["pr"]["headRefOid"] = "incremental-head"
        value["pr"]["reviews"].append({"author": {"login": "coderabbitai"}, "state": "COMMENTED",
                                        "submittedAt": "2026-09-15T00:01:00Z", "body": "",
                                        "commit": {"oid": "incremental-head"}})
        result = classify(value)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["actions"], [])

    def test_push_waits_for_automatic_review_registration_without_requesting_a_pass(self):
        value = raw()
        value["pr"]["headRefOid"] = "new-head"
        result = classify(value)
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["actions"], [])
        value["pr"]["statusCheckRollup"] = [{"context": "CodeRabbit", "state": "SUCCESS"}]
        self.assertEqual(classify(value)["state"], "ready")

    def test_codex_positive_reaction_is_review_completion_not_approval(self):
        value = raw()
        value["pr"]["reviews"] = value["pr"]["reviews"][:1]
        value["pr"]["reactions"] = [{"content": "THUMBS_UP", "createdAt": "2026-09-15T00:02:00Z",
                                      "user": {"login": "chatgpt-codex-connector[bot]"}}]
        self.assertEqual(classify(value)["state"], "ready")
        value["pr"]["reactions"].append({"content": "EYES", "createdAt": "2026-09-15T00:03:00Z",
                                         "user": {"login": "chatgpt-codex-connector[bot]"}})
        self.assertEqual(classify(value)["state"], "pending")

    def test_bodies_need_evidence_and_edited_feedback_is_not_silently_acknowledged(self):
        value = raw()
        value["pr"]["reviews"][0]["body"] = "Material finding in review summary"
        with tempfile.TemporaryDirectory() as directory:
            configured = replace(settings(), cursor_path=Path(directory) / "cursor.json")
            first = classify(value, configured)
            token = first["targets"][0]["reviews"]["feedback"][0]["token"]
            self.assertEqual(first["state"], "actionable")
            with self.assertRaises(w.WatchError):
                w.acknowledge_feedback(configured, first, [token], "addressed", "")
            w.acknowledge_feedback(configured, first, [token], "addressed", "pushed fix abc; regression passed")
            value["pr"]["headRefOid"] = "after-fix"
            self.assertEqual(classify(value, configured)["state"], "ready")
            value["pr"]["reviews"][0]["body"] += " with new finding"
            edited = classify(value, configured)
            self.assertEqual(edited["state"], "actionable")
            with self.assertRaises(w.WatchError):
                w.acknowledge_feedback(configured, edited, [token], "addressed", "stale evidence")
            w.feedback_path(configured).write_text(json.dumps({token: {}}))
            with self.assertRaises(w.WatchError):
                classify(value, configured)

    def test_top_level_comment_requires_triage_too(self):
        value = raw()
        value["pr"]["comments"] = [{"id": "C_1", "body": "Race in cleanup", "author": {"login": "reviewer"}}]
        result = classify(value)
        self.assertEqual(result["actions"][0]["type"], "review_feedback")

    def test_approval_fallback_waits_for_triage_and_automatic_review_and_is_not_spammed(self):
        value = raw()
        value["pr"]["reviewDecision"] = "REVIEW_REQUIRED"
        self.assertEqual(classify(value)["actions"][0]["type"], "approval_needed")
        value["pr"]["statusCheckRollup"] = [{"name": "CodeRabbit", "status": "IN_PROGRESS"}]
        self.assertEqual(classify(value)["state"], "pending")
        self.assertEqual(classify(value)["actions"], [])
        value["pr"]["statusCheckRollup"] = []
        value["pr"]["comments"] = [{"id": "request", "body": "@coderabbitai approve",
                                    "createdAt": "2026-09-15T00:02:00Z"}]
        self.assertEqual(classify(value)["state"], "pending")
        self.assertEqual(classify(value)["actions"], [])
        value["reviewThreads"] = [{"id": "thread", "isResolved": False}]
        self.assertNotIn("approval_needed", [a["type"] for a in classify(value)["actions"]])

    def test_external_and_own_enrollment_keep_ci_repairs(self):
        value = raw()
        value["pr"]["autoMergeRequest"] = {"enabledBy": {"login": "owner"}}
        value["checks"][0].update(bucket="fail", state="FAILURE")
        for head in (None, value["pr"]["headRefOid"]):
            result = classify(value, replace(settings(), await_merge_head=head, await_merge_mode="auto"))
            self.assertEqual(result["state"], "actionable")
            self.assertIn("ci_failure", [a["type"] for a in result["actions"]])


class ObservationTests(unittest.TestCase):
    def test_one_monitor_survives_ready_repair_enrollment_until_merged(self):
        values = []
        for stage in ("ready", "failure", "enrolled", "merged"):
            value = raw()
            if stage == "failure":
                value["checks"][0].update(bucket="fail", state="FAILURE")
                value["pr"]["autoMergeRequest"] = {}
            if stage == "enrolled":
                value["pr"]["autoMergeRequest"] = {}
            if stage == "merged":
                value["pr"].update(state="MERGED", mergedAt="2026-09-15T00:03:00Z", mergeCommit={"oid": "merged-sha"})
            values.append(classify(value))
        with tempfile.TemporaryDirectory() as directory:
            configured = replace(settings(), mode="watch", output_path=Path(directory) / "latest.json")
            output = io.StringIO()
            with mock.patch.object(w, "collect_snapshot", side_effect=values), mock.patch.object(w.time, "sleep"), redirect_stdout(output):
                code = w.watch(configured, w.Runner(), Path(directory))
            states = [json.loads(line)["state"] for line in output.getvalue().splitlines()]
            self.assertEqual(states, ["ready", "actionable", "awaiting_merge", "merged"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(configured.output_path.read_text())["targets"][0]["pr"]["mergeCommit"]["oid"], "merged-sha")

    def test_independent_ready_pr_survives_waiting_or_failed_other_target(self):
        ready_target = raw()
        pending_target = copy.deepcopy(ready_target)
        pending_target["pr"]["reviewDecision"] = "REVIEW_REQUIRED"
        pending_target["checks"][0].update(bucket="pending", state="IN_PROGRESS")
        self.assertEqual(w.snapshot([ready_target, pending_target], settings())["state"], "ready")
        targets = (w.Target(Path("/one"), "1", "explicit"), w.Target(Path("/two"), "2", "explicit"))
        with mock.patch.object(w, "discover_targets", return_value=targets), mock.patch.object(
            w, "collect_target", side_effect=[w.WatchError("missing permission"), ready_target]
        ):
            observed = w.collect_snapshot(settings(), w.Runner(), Path.cwd())
        self.assertEqual(observed["state"], "ready")
        self.assertEqual(len(observed["targets"]), 2)
        self.assertTrue(observed["actions"])

    def test_stalled_wait_yields_diagnosis_without_requesting_review(self):
        pending = raw()
        pending["checks"][0].update(bucket="pending", state="IN_PROGRESS")
        tick = [0.0]
        def sleep(_):
            tick[0] += 901
        output = io.StringIO()
        with mock.patch.object(w.time, "monotonic", side_effect=lambda: tick[0]), mock.patch.object(
            w.time, "sleep", side_effect=sleep
        ), mock.patch.object(w, "collect_snapshot", side_effect=lambda *a, **k: classify(pending)), redirect_stdout(output):
            w.watch(replace(settings(), mode="until-actionable"), w.Runner(), Path.cwd())
        result = json.loads(output.getvalue())
        self.assertEqual(result["actions"][-1]["type"], "diagnose_wait")

    def test_restart_retains_age_of_an_unchanged_wait(self):
        pending = raw()
        pending["checks"][0].update(bucket="pending", state="IN_PROGRESS")
        with tempfile.TemporaryDirectory() as directory:
            configured = replace(settings(), mode="until-actionable", cursor_path=Path(directory) / "cursor.json")
            previous = classify(pending, configured)
            previous["waitingSince"] = "2020-01-01T00:00:00Z"
            w.write_cursor(previous, configured.cursor_path)
            output = io.StringIO()
            with mock.patch.object(w, "collect_snapshot", return_value=classify(pending, configured)), redirect_stdout(output):
                w.watch(configured, w.Runner(), Path.cwd())
            self.assertEqual(json.loads(output.getvalue())["actions"][-1]["type"], "diagnose_wait")

    def test_hung_cli_is_bounded_and_nontransient_denial_is_not_retried(self):
        with mock.patch.object(w.subprocess, "run", side_effect=subprocess.TimeoutExpired("gh", 60)) as run:
            with self.assertRaises(w.WatchError) as raised:
                w.Runner().run(["gh", "api"], Path.cwd())
            self.assertTrue(raised.exception.retryable)
            self.assertLessEqual(run.call_args.kwargs["timeout"], 60)
        with mock.patch.object(w, "collect_snapshot", side_effect=w.WatchError("No active turn for permission")) as collect, redirect_stdout(io.StringIO()):
            self.assertEqual(w.watch(settings(), w.Runner(), Path.cwd()), w.EXIT_BLOCKED)
            collect.assert_called_once()


class GithubCollectionTests(unittest.TestCase):
    def test_sibling_failures_retry_with_a_bound_and_then_stop_repeating(self):
        targets = (w.Target(Path("/one"), "1", "explicit"), w.Target(Path("/two"), "2", "explicit"))
        configured = replace(settings(), max_errors=2)
        runner = w.Runner()
        calls = []
        def collect(target, *_):
            calls.append(target)
            if target == targets[0]:
                raise w.WatchError("temporary service failure", retryable=True)
            return raw()
        with mock.patch.object(w, "discover_targets", return_value=targets), mock.patch.object(w, "collect_target", side_effect=collect):
            first = w.collect_snapshot(configured, runner, Path.cwd())
            self.assertEqual(first["targets"][-1]["state"], "pending")
            for _ in range(2):
                later = w.collect_snapshot(configured, runner, Path.cwd())
                self.assertEqual(later["targets"][-1]["state"], "blocked")
                self.assertEqual(later["state"], "ready")
        self.assertEqual(calls.count(targets[0]), 2)


    def test_empty_output_or_permissions_are_not_empty_required_checks(self):
        for code, stderr in ((0, ""), (1, "permission denied")):
            with mock.patch.object(w.Runner, "run", return_value=subprocess.CompletedProcess(
                ["gh", "pr", "checks"], code, stdout="", stderr=stderr
            )):
                with self.assertRaises(w.WatchError):
                    w.Runner().json(["gh", "pr", "checks"], Path.cwd(), frozenset({0, 1}))

    def page(self, reviews=(), more=False, head="head-ready"):
        connection = lambda nodes=(), nxt=False: {"nodes": list(nodes), "pageInfo": {"hasNextPage": nxt, "endCursor": "next" if nxt else None}}
        return {"data": {"repository": {"pullRequest": {
            "headRefOid": head, "isMergeQueueEnabled": False, "mergeQueueEntry": None,
            "reviews": connection(reviews, more), "reviewThreads": connection(),
            "comments": connection(), "reactions": connection(),
        }}}}

    def test_combined_connections_paginate_without_losing_or_duplicating_feedback(self):
        runner = mock.Mock()
        runner.json.side_effect = [self.page([{"id": "r1"}], True), self.page([{"id": "r2"}])]
        value = w.pull_request_auxiliary_state(runner, Path.cwd(), "org/repo", 7, "github.example")
        self.assertEqual([r["id"] for r in value["reviews"]], ["r1", "r2"])
        second = runner.json.call_args.args[0]
        self.assertIn("reviewCursor=next", second)
        self.assertIn("github.example", second)

    def test_missing_graphql_data_and_head_races_fail_closed(self):
        for pages in ([{"data": {}}], [{"errors": [{"message": "denied"}]}],
                      [self.page([{"id": "r1"}], True), self.page(head="new-head")]):
            runner = mock.Mock()
            runner.json.side_effect = pages
            with self.assertRaises(w.WatchError):
                w.pull_request_auxiliary_state(runner, Path.cwd(), "org/repo", 7, "github.com")

    def test_repo_policy_is_cached_checks_use_native_required_filter_and_head_is_checked(self):
        runner = w.Runner()
        repo = {"nameWithOwner": "example/current", "url": "https://github.com/example/current",
                "mergeCommitAllowed": True, "rebaseMergeAllowed": True, "squashMergeAllowed": True}
        pr = raw()["pr"]
        target = w.Target(Path.cwd(), "404", "explicit")
        with mock.patch.object(runner, "json", side_effect=[repo, pr, raw()["checks"], self.page(),
                                                           copy.deepcopy(pr), raw()["checks"], self.page()]) as call:
            for _ in range(2):
                w.collect_target(target, replace(settings(), check_policy="required"), runner)
            commands = [c.args[0] for c in call.call_args_list]
            self.assertEqual(sum(c[1:3] == ["repo", "view"] for c in commands), 1)
            self.assertTrue(all("--required" in c for c in commands if c[1:3] == ["pr", "checks"]))
