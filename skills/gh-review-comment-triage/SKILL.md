---
name: gh-review-comment-triage
description: Verify and resolve GitHub PR review comments from Codex, CodeRabbit, or humans using GH CLI. Use to fetch review threads, distinguish real issues from stale or false-positive findings, fix actionable issues, reply with evidence, and resolve addressed threads.
---

# GH Review Comment Triage

Ground every review claim in current code before changing anything.

## Workflow

1. Identify repository, branch, base, PR number, URL, and current head SHA with `gh pr view` and Git.
2. Fetch review threads with `gh api graphql`; do not try unsupported `gh pr view --json reviewThreads`. Include thread id, resolution/outdated state, path and line, author, body, timestamp, and URL. Paginate beyond 100 threads.
3. Build a compact triage table: thread, claim, current-code evidence, verdict, and action. Use `real`, `already fixed`, `stale`, `false positive`, or `needs user decision`.
4. Inspect the exact file, nearby symbols, related call sites, tests, and current diff. Old line numbers and plausible bot prose are not evidence.
5. Patch only real issues and add focused regression tests when useful. Keep each change mapped to its thread.
6. Validate the touched behavior. When a parent workflow owns broader validation and commits, return the changed work to it.
7. Reply and resolve only when the task authorizes GitHub mutation. For fixes, cite code and validation; for stale or false-positive findings, give the concrete reason before resolving.

Do not resolve before fixing or documenting non-actionability. Do not batch unrelated findings into a vague change, stage unrelated work, or commit/push unless the invoking workflow authorizes it.

Report fixed, stale, false-positive, and unresolved threads; validation performed; branch/push state; and whether actionable threads remain.
