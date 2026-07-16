#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import hashlib
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "skills"
    / "gh-review-comment-triage"
    / "scripts"
    / "fetch_review_threads.py"
)
SPEC = importlib.util.spec_from_file_location("fetch_review_threads", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)

SAFETY_SCRIPT = ROOT / "scripts" / "check-merge-ready-safety.py"
SAFETY_SPEC = importlib.util.spec_from_file_location(
    "fetch_review_threads_safety", SAFETY_SCRIPT
)
assert SAFETY_SPEC and SAFETY_SPEC.loader
SAFETY = importlib.util.module_from_spec(SAFETY_SPEC)
sys.modules[SAFETY_SPEC.name] = SAFETY
SAFETY_SPEC.loader.exec_module(SAFETY)


class FetchReviewThreadsTests(unittest.TestCase):
    def test_helper_matches_audited_runtime_pin(self) -> None:
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(content.encode("utf-8")).hexdigest(),
            SAFETY.AUDITED_RUNTIME_SHA256[SAFETY.AUDITED_FETCHER],
        )
        findings = []
        SAFETY.verify_audited_runtime(
            SCRIPT, SAFETY.AUDITED_FETCHER, content, findings
        )
        SAFETY.scan_audited_fetcher(SCRIPT, content, findings)
        self.assertEqual(findings, [])

    def test_helper_digest_drift_is_rejected(self) -> None:
        findings = []
        SAFETY.verify_audited_runtime(
            SCRIPT,
            SAFETY.AUDITED_FETCHER,
            SCRIPT.read_text(encoding="utf-8") + "\n# drift\n",
            findings,
        )
        self.assertTrue(any("digest changed" in item for item in findings))

    def test_parse_pr_url_uses_base_repository_identity(self) -> None:
        self.assertEqual(
            MODULE.parse_pr_url("https://github.com/base-owner/base-repo/pull/42"),
            ("base-owner", "base-repo", 42),
        )

    def test_rejects_partial_explicit_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "together"):
            MODULE.resolve_pr("owner/repo", None)

    def test_resolve_pr_uses_canonical_url_not_head_repository(self) -> None:
        with mock.patch.object(
            MODULE,
            "run_json",
            return_value={
                "number": 42,
                "url": "https://github.com/base-owner/base-repo/pull/42",
                "headRepositoryOwner": {"login": "fork-owner"},
                "headRepository": {"name": "fork-repo"},
            },
        ):
            self.assertEqual(
                MODULE.resolve_pr(None, None),
                ("base-owner", "base-repo", 42),
            )

    def test_actionable_filter_excludes_resolved_and_outdated(self) -> None:
        threads = [
            {"id": "open", "isResolved": False, "isOutdated": False},
            {"id": "resolved", "isResolved": True, "isOutdated": False},
            {"id": "outdated", "isResolved": False, "isOutdated": True},
        ]
        self.assertEqual(
            [thread["id"] for thread in MODULE.actionable_threads(threads)],
            ["open"],
        )

    def test_paginates_threads_and_thread_comments(self) -> None:
        calls = []

        def fake_runner(query, variables):
            calls.append((query, variables.copy()))
            if "reviewThreads" in query and variables.get("cursor") is None:
                return {
                    "data": {"repository": {"pullRequest": {
                        "title": "Cross-fork change",
                        "url": "https://github.com/base/repo/pull/7",
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "t1"},
                            "nodes": [{
                                "id": "thread-1",
                                "isResolved": False,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                                    "nodes": [{"id": "comment-1", "body": "claim"}],
                                },
                            }],
                        },
                    }}}
                }
            if "reviewThreads" in query:
                return {
                    "data": {"repository": {"pullRequest": {
                        "title": "Cross-fork change",
                        "url": "https://github.com/base/repo/pull/7",
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{
                                "id": "thread-2",
                                "isResolved": True,
                                "isOutdated": False,
                                "comments": {
                                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                                    "nodes": [{"id": "comment-3", "body": "done"}],
                                },
                            }],
                        },
                    }}}
                }
            return {
                "data": {"node": {"comments": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [
                        {"id": "comment-1", "body": "duplicate"},
                        {"id": "comment-2", "body": "follow-up"},
                    ],
                }}}
            }

        result = MODULE.fetch_all_threads(fake_runner, "base", "repo", 7)

        self.assertEqual(result["repository"], "base/repo")
        self.assertEqual([t["id"] for t in result["threads"]], ["thread-1", "thread-2"])
        self.assertEqual(
            [c["id"] for c in result["threads"][0]["comments"]],
            ["comment-1", "comment-2"],
        )
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1][1]["cursor"], "t1")
        self.assertEqual(calls[2][1], {"id": "thread-1", "cursor": "c1"})


if __name__ == "__main__":
    unittest.main()
