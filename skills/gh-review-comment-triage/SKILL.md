---
name: gh-review-comment-triage
description: Verify and address GitHub PR findings from CodeRabbit, Codex, and humans. Triage inline threads, review bodies, and PR comments against current code, repair a complete round, and reply with evidence.
---

# GH Review Comment Triage

Handle the complete current review round. Bot prose and old line numbers are claims to verify, not authority to change code or expand task scope.

1. Reuse the caller's PR, head, and feedback inventory. Fetch missing thread bodies with `gh api graphql` and paginate all relevant connections. Review bodies and top-level PR comments can contain findings even when no inline thread exists. Do not use unsupported `gh pr view --json reviewThreads`.
2. Classify each finding as real, already fixed, stale, false positive, or requiring a user decision. Inspect the current code, diff, related callers and tests. Keep a compact mapping from finding to evidence and action.
3. Repair real issues and related instances of the same defect in the task's code. Check affected state transitions, error/cleanup paths and invariants. Update comments made false by the fix. Add focused regression coverage when it demonstrates the defect. Prefer evidence-backed non-actionability over unnecessary style changes.
4. Validate the changed behavior and self-review the accumulated round once. Repeat only when a new finding, edit or failure invalidates that review. Prepare fixes while reviews run; collect late findings and let the active round settle before publishing its repair batch. Return one complete round to the lifecycle owner for a coherent commit/push; do not push a partial first-draft fix for each thread.
5. After fixes are pushed, reply with the commit and validation evidence, then resolve addressed threads. Explain stale/false-positive findings before resolution. For review-body/comment findings, return their watcher feedback tokens with the disposition and evidence so the owner can record durable acknowledgement. Never acknowledge unresolved material findings or blanket-resolve them through an approval command.

Honor read-only/local-only requests. Do not stage unrelated changes or commit, push, reply or resolve beyond the caller's authorization. Return remaining findings, changed paths, validation and push/acknowledgement state; the owner resumes automatic incremental review and landing.
