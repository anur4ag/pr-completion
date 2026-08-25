---
name: gh-review-comment-triage
description: Verify and resolve GitHub PR review comments from Codex, CodeRabbit, or humans using GH CLI. Use to fetch review threads, distinguish real issues from stale or false-positive findings, fix actionable issues convergently (class sweep, invariant pass, self-reviewed single push per round), reply with evidence, and resolve addressed threads.
---

# GH Review Comment Triage

Ground every review claim in current code before changing anything.

Reviewers re-review every push, and each push of unreviewed fixes is fresh surface for new findings. The goal of a round is therefore convergence, not thread count: one batched, self-reviewed push that gives the next review pass nothing new to find. A point-fix that patches only the flagged line reliably spawns the next round.

## Workflow

1. Identify repository, branch, base, PR number, URL, and current head SHA with `gh pr view` and Git.
2. Fetch review threads with `gh api graphql`; do not try unsupported `gh pr view --json reviewThreads`. Include thread id, resolution/outdated state, path and line, author, body, timestamp, and URL. Paginate beyond 100 threads.
3. Build a compact triage table over **all** open threads before fixing any: thread, claim, current-code evidence, verdict, and action. Use `real`, `already fixed`, `stale`, `false positive`, or `needs user decision`. A round addresses the whole table at once, never one thread at a time.
4. Inspect the exact file, nearby symbols, related call sites, tests, and current diff. Old line numbers and plausible bot prose are not evidence.
5. Fix each real issue **convergently**, not point-wise:
   - **Sweep the class.** The flagged line is one member of a pattern. Search the changed code (and its neighbors) for every other site with the same defect and fix them all in this round; a reviewer that found one member will find the siblings on the next pass.
   - **Run the invariant pass.** After drafting the fix, re-derive what the touched code participates in — callers, state transitions, error and cleanup paths, concurrency assumptions, and the comments/docs that describe the old behavior — and confirm the fix preserves each. Update any comment the fix falsifies. A fix that satisfies the thread but breaks a neighboring invariant is the primary cause of review loops.
   - Add focused regression tests when useful. Keep each change traceable to its thread(s) in the report, but shared-cause threads share one coherent fix.
6. **Self-review the round before it leaves the machine.** Once all fixes are drafted, adversarially review the accumulated round diff with the same scrutiny the PR reviewers apply (correctness, invariants, edge cases, consistency with surrounding code) and fix what it finds locally. Repeat until the pass finds nothing material. Never hand reviewers a first-draft fix.
7. Validate the touched behavior. When a parent workflow owns broader validation and commits, return the changed work to it as **one complete round** — all threads triaged, all fixes self-reviewed — so it results in a single push. Never return or push a partial round.
8. Reply and resolve only when the task authorizes GitHub mutation. For fixes, cite code and validation; for stale or false-positive findings, give the concrete reason before resolving. Prefer a reasoned reply-and-resolve over a code change for style-level nits when the current code is defensible — every avoidable change is avoidable new review surface.

Do not resolve before fixing or documenting non-actionability. Do not collapse unrelated findings into one vague change (batching the round's push is required; obscuring per-thread traceability is not allowed), stage unrelated work, or commit/push unless the invoking workflow authorizes it.

Report fixed, stale, false-positive, and unresolved threads; the class sweeps performed and sites they caught beyond the flagged lines; invariant-pass and self-review outcomes; validation performed; branch/push state; and whether actionable threads remain.
