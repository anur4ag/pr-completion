---
name: take-pr-to-completion
description: Autonomously prepare and shepherd GitHub pull requests through commits, push, PR creation, CI, reviews, and conflicts. At verified readiness, request explicit per-PR landing confirmation, safely request auto-merge or merge-queue entry for the exact head, and continue until merged or blocked. Never bypass protections, use admin privileges, or force-push.
---

# Take PR to Completion

Own each in-scope repository from local work through a merged PR or an evidenced blocker. Routine preparation, repair, and observation are autonomous. A landing mutation always requires explicit per-PR confirmation for the current head SHA.

## Operating contract

Invocation authorizes in-scope edits, checks, commits, normal pushes, PR creation, check reruns, review replies and resolution, and conflict/base updates. Infer routine details from repository instructions, GitHub metadata, history, and tests. Do not ask between ordinary cycles.

Never use `--admin`, bypass protections, force-push, rewrite published history, disable an externally configured landing action, or invoke merge REST/GraphQL mutations. The shipped `scripts/pr_land.py` helper is the sole authorized merge-state mutation surface. It is fail-closed, binds approval to the current head SHA, and is used only after explicit per-PR confirmation.

Escalate only for a non-derivable product decision, unavailable credentials or permissions, unrelated overlapping changes, contradictory reviewer requirements, or an ambiguous repository landing policy.

## Prepare repositories and pull requests

1. Read repository/workspace instructions. Discover all owning repositories and submodules, deepest first.
2. If task-related changes remain, invoke `$pr-completion:commit-workspace-changes` in **phase-only child mode** so it returns here without recursion.
3. For each repository, require a safe non-default feature branch and a configured writable remote. Preserve unrelated work.
4. Push ordinary commits without force. If authentication or upstream ownership is unavailable, block with evidence.
5. Find an open PR for the branch. If none exists, infer the base branch, title, and body from policy, history, and commits; create the PR with GitHub CLI. Never create a PR from a default branch or guess a materially ambiguous base.
6. Record repository, PR URL, base, branch, and current head SHA. Treat repositories independently; one PR's landing confirmation never authorizes another.

## Run the deterministic watcher

Resolve `scripts/pr_watch.py` relative to this file and run it from the PR repository. Use default `until-actionable` mode with a stable cursor, observations NDJSON path, and durable stdout file:

```bash
python3 <skill-directory>/scripts/pr_watch.py
```

The watcher owns discovery, GitHub queries, pagination, polling, backoff, head freshness, and JSON output. Repeated `--target PATH[=PR]` supports multiple PRs before landing. CLI values override `.pr-completion.json`. Use `--help` and `--print-config --pretty` for the interface.

The JSON `state`, not the process exit code, is authoritative:

- `actionable`: dispatch every reported repair.
- `pending`: keep polling.
- `ready`: the exact current head satisfies the fail-closed readiness predicate; begin the landing-decision phase.
- `auto_merge`: another actor already configured auto-merge; report provenance and observe it as external state unless the user asks to change course.
- `awaiting_merge`: an approved landing request was accepted for the exact authorized head; keep polling.
- `merged`: terminal success.
- `blocked` (exit `20`): diagnose and escalate only when safe recovery is exhausted.
- `timeout` (exit `30`): consume the final JSON, then resume or report with evidence.

Launch `until-actionable` in the background. When it exits, always read and parse the durable output before yielding or ending the turn. Dispatch actions, repair, commit in phase-only child mode, push, and relaunch against the new head with the same cursor and observations paths. A push invalidates every prior observation and landing decision.

## Dispatch actionable states

- `conflict`: load `$pr-completion:merge-conflict-resolution`, validate, commit phase-only, push, restart.
- `base_behind`: update only when policy/readiness requires it; prefer a base merge over history rewriting when policy is silent.
- `ci_failure`: inspect logs, fix branch-caused/deterministic failures, rerun justified flaky jobs, validate, commit phase-only, push, restart.
- `review_threads` or actionable `changes_requested`: load `$pr-completion:gh-review-comment-triage`; after edits validate, commit phase-only, push, restart.
- `review_rerun`: wait for the reviewer/check state; do not invent work.

Approvals and comments on an older SHA are not current when policy or the reviewer requires a fresh pass.

## Landing-decision phase

Handle one `ready` PR at a time.

1. Determine whether the repository requires a merge queue. Otherwise infer the allowed merge method from repository settings, instructions, and established history. Ask a method question only when the policy is genuinely ambiguous.
2. Run `scripts/pr_land.py` **without** `--confirm` for the exact ready head to obtain the canonical plan. Preserve every readiness-policy input from the watcher: pass the same mutually exclusive `--config` or `--no-config` source, repeated `--reviewer`, `--check-policy`, and `--strict-changes-requested` overrides when they were used. Use `--mode queue` for a required queue, or `--mode auto --method merge|squash|rebase` for auto-merge. The plan records the resolved policy source and values, then emits `readinessPolicyDigest`.
3. Ask for explicit per-PR confirmation using structured input when available. Show repository, PR URL, current head SHA, chosen action/method, and the exact warning from the plan that the request may merge immediately. Offer approve and stop-at-ready choices. Silence, a prior PR's answer, or a previous head's answer is not approval.
4. If declined, report verified readiness and stop for that PR without mutation.
5. If approved, immediately invoke the same helper plan with the same readiness-policy flags, `--policy-digest <readinessPolicyDigest>`, and `--confirm`. The helper re-runs the read-only watcher, requires the same resolved policy, requires `ready`, requires the exact authorized head SHA, revalidates queue and merge-method policy, and uses GitHub's normal protected path. If policy, head, or gates changed, return to the watcher; do not reuse approval.

Example shapes (fill values from the fresh plan):

```bash
python3 <skill-directory>/scripts/pr_land.py --repo <repo> --pr <url> --head <sha> --mode auto --method squash
python3 <skill-directory>/scripts/pr_land.py --repo <repo> --pr <url> --head <sha> --mode auto --method squash --policy-digest <digest-from-plan> --confirm
```

For a required merge queue, omit `--method` and use `--mode queue`. Never construct an alternative merge command yourself.

## Observe landing to completion

After `landing_requested`, start a fresh single-PR watcher bound to the authorized head:

```bash
python3 <skill-directory>/scripts/pr_watch.py --target <repo>=<url> --await-merge <sha> --await-merge-mode <auto|queue> --await-merge-since <requestedAt>
```

`requestedAt` comes from the successful `landing_requested` payload and must be reused across watcher restarts. `awaiting_merge` is a wait state. The watcher allows a bounded maximum 60-second window from that durable timestamp for GitHub to expose the accepted auto-merge request or merge-queue entry, then requires that enrollment to remain observable. Missing or rejected enrollment becomes `blocked`; it never waits forever on an unproven action. `merged` is success only when GitHub's merged head matches the authorized head. A different head emits `blocked` with `authorization_stale`; return to normal watching and require a new readiness result and new confirmation. Closed/draft, rejected queue requests, permissions, or protection failures are blocked with evidence. Do not claim completion merely because auto-merge was enabled or a queue request was submitted.

## Report

For every PR, report URL, final/current head, commits pushed, checks and reviews, conflicts handled, landing decision and method, confirmation provenance, and final state: merged, ready-without-approval, externally configured auto-merge, or blocked. Never describe a submitted landing request as merged until the watcher observes `merged`.
