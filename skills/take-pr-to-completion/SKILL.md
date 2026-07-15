---
name: take-pr-to-completion
description: Autonomously shepherd a GitHub pull request to verified merge readiness with a deterministic state-machine watcher, local commit preparation, CI fixes, review triage, and conflict resolution. Stops without merging, enabling auto-merge, joining a merge queue, bypassing protections, or force-pushing. Use when asked to babysit, watch, finish, land, or take a PR to completion with minimal interaction.
---

# Take PR to Completion

Own the PR until its current head is verified merge-ready, or until an already-configured external auto-merge or merged state is observed and reported. Pending CI or review is a wait state, not completion. Terminal success is **verified merge readiness**, not a merged PR.

## Operating contract

Invocation authorizes routine in-scope edits, tests, commits, pushes, check reruns, thread replies and resolution, and base updates required for merge readiness. Infer operational details from repository instructions, PR metadata, history, tests, and code. Keep updates informational; do not ask for approval between cycles.

This skill never mutates merge state. Do not run `gh pr merge`, enable or disable auto-merge, enqueue a merge queue entry, bypass branch protections, force-push, or issue GraphQL/REST equivalents such as `enablePullRequestAutoMerge`, `disablePullRequestAutoMerge`, or `enqueuePullRequest`. Auto-merge that another actor enabled before this run may be observed and reported only.

Escalate only for a non-derivable product or architecture decision, unavailable credentials or permissions, overlapping unrelated changes, or contradictory reviewer requirements. Never rewrite history, bypass protections, merge, or otherwise exceed this authority. Exhaust safe diagnostics and non-destructive alternatives first.

## Preflight

1. Read repository and workspace instructions. Identify the owning repository, worktree, branch, PR, base branch, head SHA, required checks, merge policy, and authentication.
2. Inspect local Git state without disturbing unrelated changes.
3. If task-related local changes exist in any owning repository, load `$pr-completion:commit-workspace-changes` to validate and commit them.
4. Push in-scope commits to their corresponding branches. Respect repository policy for deferred submodule gitlinks; do not invent a parent commit because a child repository advanced.

## Run the deterministic watcher

Resolve `scripts/pr_watch.py` relative to this `SKILL.md`, but run it with the PR repository as the working directory. Use its default `until-actionable` mode:

```bash
python3 <skill-directory>/scripts/pr_watch.py
```

The script owns repository discovery, GitHub queries, GraphQL pagination, polling, backoff, head-SHA freshness, and JSON output. CLI arguments override `.pr-completion.json`, which overrides defaults. Use `--help` for flags and `--print-config --pretty` for the resolved schema and defaults. For multiple independent repositories or submodules, pass repeated `--target PATH[=PR]`, configure `targets`, or use `--discover open-pr`. Pass required bot or human logins with repeated `--reviewer` or `requiredReviewers`.

The durable cursor defaults to `$GIT_DIR/pr-completion/pr-watch-cursors.json`, including the resolved worktree git dir when `.git` is a file. If no git metadata is available, it falls back to `$XDG_STATE_HOME/pr-completion/pr-watch-cursors.json`, then `~/.local/state/pr-completion/pr-watch-cursors.json` (or `%LOCALAPPDATA%/pr-completion/pr-watch-cursors.json` on Windows). It stores the last emitted fingerprint per PR target outside the working tree. Keep that path stable across every relaunch. Set `cursorPath` to `null` only for an intentional stateless run, or override it with `--cursor PATH`.

Configure a stable `observationsPath` or pass `--observations-file PATH` for the recycling-proof NDJSON trail. A typical repository configuration is:

```json
{
  "version": 1,
  "cursorPath": "~/.local/state/pr-completion/pr-watch-cursors.json",
  "observationsPath": "~/.local/state/pr-completion/pr-watch-observations.ndjson",
  "strictChangesRequested": false
}
```

`strictChangesRequested` defaults to `false`. With that default, a standing `CHANGES_REQUESTED` decision is a `review_rerun` pending state when the current head has zero unresolved threads and checks are still pending. It becomes actionable again when a thread is unresolved or pending checks finish without clearing the decision. Use `--strict-changes-requested` (or set `strictChangesRequested` to `true`) to restore always-actionable classification.

Do not recreate manual polling while the watcher works. In `until-actionable`, the process emits exactly one new actionable or terminal observation when it exits; a previously emitted identical actionable fingerprint remains a wait state across process relaunches. The JSON `state`, not the process exit code, is the state-machine signal:

- `actionable`: dispatch every reported action below.
- `pending`: possible on normal output only in `once` or `watch` mode; `until-actionable` keeps polling without emitting it.
- `ready`: current head is verified merge-ready under the watcher's fail-closed predicate; report and stop. Issue no merge-state mutation.
- `auto_merge`: auto-merge was already enabled by another actor. This is terminal and read-only even if CI or reviews still look pending or failing: report structured provenance from the observation and stop without dispatching repairs or changing auto-merge.
- `merged`: the PR is already merged (externally); report and finish.
- Exit `20` with `blocked`: diagnose configuration, authentication, or an unreported gate; escalate only if unrecoverable.
- Exit `30`: watcher timeout; inspect its last JSON state and resume or escalate with evidence.

The canonical autonomous pattern is a durable relaunch loop:

1. Launch `until-actionable` in the background with the stable cursor, observations file, and a durable stdout output file.
2. When the process exits, read the output file, parse the final JSON object, and dispatch every action it reports.
3. After repairs and any push, relaunch with the **same cursor and observations paths** against the current head.
4. Repeat until `ready`, `auto_merge`, `merged`, or `blocked` is emitted.

Always consume the emitted JSON before yielding, sending a status update, or ending the turn. Claude Code background output reaches the agent when the process exits, not through continuous stdout. If a harness completion record is lost after session recycling, recover the observation from the durable stdout output file or the last applicable line in the observations NDJSON file; never assume missing completion metadata means no observation was emitted. Never leave a completed watcher's output unread.

After any push, the prior observation is stale. Stop an old watcher if needed and start a fresh one against the new head SHA. Repeat until the watcher reports a terminal state.

## Dispatch actionable states

### Base update or conflict

For `base_behind`, update only when repository policy or merge readiness requires it. For `conflict`, load `$pr-completion:merge-conflict-resolution`. After resolution, use `$pr-completion:commit-workspace-changes` if uncommitted work remains, push, and restart the watcher.

Without a documented strategy, merge the base into the published PR branch instead of rewriting history. Never force-push.

### CI failure

For `ci_failure`, read failed job and step logs. Classify each failure as branch-caused, deterministic repository failure, flaky/transient, infrastructure/external, or obsolete-head noise.

- Fix branch-caused and deterministic failures within scope; add focused regression coverage when useful.
- Rerun a flaky job only with evidence of flakiness.
- Ignore superseded runs only after verifying the current head.
- After edits, invoke `$pr-completion:commit-workspace-changes`, push, and restart the watcher.

Escalate external blockers only after safe diagnosis and justified retries cannot progress them.

### Review feedback

For `review_threads` or actionable `changes_requested`, load `$pr-completion:gh-review-comment-triage` for the current round. A `review_rerun` pending entry means the standing decision has no unresolved thread to dispatch while checks are pending; keep waiting. If triage changes code, invoke `$pr-completion:commit-workspace-changes`, push, and restart the watcher.

Do not treat approval on an older SHA as current when repository policy or the reviewer requires a fresh pass. Continue until no actionable threads remain and required approvals or bot passes are current for the **current** head SHA.

## Terminal states (no merge mutation)

On `ready`, treat the current head as verified merge-ready when required checks pass, required approvals are current for that head, unresolved review threads are zero, and mergeability is non-conflicting. Report readiness and stop. Do not merge, enable auto-merge, or join a merge queue.

On `auto_merge`, report that auto-merge was already configured externally. Include the structured provenance payload from the observation (`enabledAt`, `enabledBy`, `mergeMethod`, and any other returned fields). Do not disable, reconfigure, or replace that setting, and do not open a repair loop for remaining gates.

On `merged`, report the already-merged PR and finish. Do not attempt further merge actions.

Report the PR URL, final state and head SHA, CI and review outcomes, conflicts handled, commits pushed, validation performed, and whether the result is merge-ready, externally auto-merge-enabled, already merged, or blocked with evidence.
