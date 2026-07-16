#!/usr/bin/env python3
"""Read GitHub PR review threads with base-repository-safe pagination."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Any, Callable


PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")

THREADS_QUERY = r"""
query($owner:String!, $repo:String!, $pr:Int!, $cursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$pr) {
      title
      url
      reviewThreads(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          originalLine
          originalStartLine
          diffSide
          startDiffSide
          comments(first:100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              id databaseId body url createdAt lastEditedAt
              author { login }
            }
          }
        }
      }
    }
  }
}
"""

COMMENTS_QUERY = r"""
query($id:ID!, $cursor:String) {
  node(id:$id) {
    ... on PullRequestReviewThread {
      comments(first:100, after:$cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id databaseId body url createdAt lastEditedAt
          author { login }
        }
      }
    }
  }
}
"""

Runner = Callable[[str, dict[str, Any]], dict[str, Any]]


def run_json(command: list[str]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise RuntimeError(message) from exc
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Command returned invalid JSON: {' '.join(command)}") from exc


def gh_graphql(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    command = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, value in variables.items():
        if value is None or value == "":
            continue
        command.extend(["-F", f"{key}={value}"])
    return run_json(command)


def parse_pr_url(url: str) -> tuple[str, str, int]:
    match = PR_URL_RE.match(url)
    if not match:
        raise ValueError(f"Unsupported GitHub PR URL: {url}")
    owner, repo, number = match.groups()
    return owner, repo, int(number)


def resolve_pr(repo: str | None, pr: int | None) -> tuple[str, str, int]:
    if (repo is None) != (pr is None):
        raise ValueError("Pass --repo OWNER/REPO and --pr NUMBER together")
    if repo is not None and pr is not None:
        parts = repo.split("/", 1)
        if len(parts) != 2 or not all(parts):
            raise ValueError("--repo must be OWNER/REPO")
        return parts[0], parts[1], pr

    data = run_json(["gh", "pr", "view", "--json", "number,url"])
    owner, base_repo, url_number = parse_pr_url(str(data["url"]))
    if int(data["number"]) != url_number:
        raise RuntimeError("PR number does not match canonical PR URL")
    return owner, base_repo, url_number


def merge_comments(existing: list[dict[str, Any]], extra: list[dict[str, Any]]) -> None:
    seen = {comment.get("id") for comment in existing}
    for comment in extra:
        if comment.get("id") not in seen:
            existing.append(comment)
            seen.add(comment.get("id"))


def actionable_threads(threads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        thread
        for thread in threads
        if not thread.get("isResolved") and not thread.get("isOutdated")
    ]


def fetch_all_threads(
    runner: Runner,
    owner: str,
    repo: str,
    pr: int,
) -> dict[str, Any]:
    threads: list[dict[str, Any]] = []
    cursor: str | None = None
    title = ""
    url = ""

    while True:
        payload = runner(
            THREADS_QUERY,
            {"owner": owner, "repo": repo, "pr": pr, "cursor": cursor},
        )
        pull_request = payload["data"]["repository"]["pullRequest"]
        if pull_request is None:
            raise RuntimeError(f"PR not found in base repository {owner}/{repo}#{pr}")
        title = pull_request.get("title", title)
        url = pull_request.get("url", url)
        connection = pull_request["reviewThreads"]
        threads.extend(connection.get("nodes") or [])
        page_info = connection["pageInfo"]
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            raise RuntimeError("Thread pagination reported another page without a cursor")

    for thread in threads:
        comments = thread.get("comments") or {"nodes": [], "pageInfo": {}}
        nodes = comments.get("nodes") or []
        page_info = comments.get("pageInfo") or {}
        comment_cursor = page_info.get("endCursor")
        while page_info.get("hasNextPage"):
            if not comment_cursor:
                raise RuntimeError("Comment pagination reported another page without a cursor")
            payload = runner(
                COMMENTS_QUERY,
                {"id": thread["id"], "cursor": comment_cursor},
            )
            connection = payload["data"]["node"]["comments"]
            merge_comments(nodes, connection.get("nodes") or [])
            page_info = connection["pageInfo"]
            comment_cursor = page_info.get("endCursor")
        thread["comments"] = nodes

    return {
        "repository": f"{owner}/{repo}",
        "pr_number": pr,
        "title": title,
        "url": url,
        "threads": threads,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="Base repository as OWNER/REPO")
    parser.add_argument("--pr", type=int, help="Pull request number")
    parser.add_argument(
        "--only-unresolved-current",
        action="store_true",
        help="Output only threads that are unresolved and not outdated",
    )
    args = parser.parse_args()

    try:
        owner, repo, pr = resolve_pr(args.repo, args.pr)
        result = fetch_all_threads(gh_graphql, owner, repo, pr)
        if args.only_unresolved_current:
            result["threads"] = actionable_threads(result["threads"])
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
