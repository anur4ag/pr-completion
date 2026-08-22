# Skills and safety

`pr-completion` ships four independently invokable skills from one shared implementation tree.

- `$pr-completion:take-pr-to-completion`
- `$pr-completion:commit-workspace-changes`
- `$pr-completion:gh-review-comment-triage`
- `$pr-completion:merge-conflict-resolution`

## `take-pr-to-completion`

**Role.** Own the PR lifecycle from local work through a merged PR or an evidenced blocker.

It can:

- validate/commit task work through the commit skill's phase-only mode;
- push normal feature branches and create missing PRs when base/title/body are derivable;
- run `pr_watch.py`, repair CI, triage reviews, and resolve conflicts;
- infer an allowed landing method or required merge queue from repository policy;
- present an explicit per-PR confirmation for the exact current head;
- invoke the guarded `pr_land.py` helper after approval; and
- observe `awaiting_merge` until GitHub reports `merged` or a blocker.

It cannot:

- treat silence, another PR's answer, or an older head's answer as approval;
- use `--admin`, bypass branch protections, force-push, or rewrite published history;
- issue merge REST/GraphQL mutations or construct an alternative merge command; or
- claim that a requested landing is merged before the watcher observes it.

## `commit-workspace-changes`

**Role.** Discover owning repositories/submodules, derive validation, fix task-caused failures, and create compliant commits.

Invocation modes:

| Mode | Trigger | Result |
| --- | --- | --- |
| Direct lifecycle (default) | User invokes the skill or asks for the commit loop. | Commit, then hand off once to `take-pr-to-completion`. |
| Phase-only child | The parent orchestrator invokes its commit phase. | Return commit evidence to the parent; no recursive handoff. |
| Explicit local-only | User says commit-only, local-only, no push, or no PR. | Stop after validated local commits. |

It preserves unrelated work, honors hooks/DCO, and processes nested repositories deepest first.

## `gh-review-comment-triage`

**Role.** Verify review threads against current code before changing or resolving them.

It classifies real, already-fixed, stale, false-positive, and decision-dependent findings; patches real issues; and replies/resolves with evidence. It does not resolve a thread without fix or non-actionability evidence.

## `merge-conflict-resolution`

**Role.** Resolve merge, rebase, cherry-pick, or revert conflicts by reconstructing both sides' intent and validating the combined result.

It does not discard unrelated work, choose a side merely because it compiles, or hide product uncertainty that code cannot settle.

## Autonomous landing contract

```text
local work
  -> validate + commit
  -> push + find/create PR
  -> watch pending/actionable gates
  -> ready (exact current head)
  -> infer repository policy
  -> show per-PR plan + immediate-merge warning
  -> explicit approval?
       no  -> report ready and stop
       yes -> fresh readiness/head recheck -> guarded landing request
              -> awaiting_merge -> merged | blocked
```

Enforced invariants:

1. Landing confirmation is separate for each PR and exact head SHA.
2. A push or changed head invalidates readiness and approval.
3. `pr_land.py` is the only merge-state mutation surface and always rechecks the resolved readiness policy—including an explicit `--config` or `--no-config` source—plus watcher readiness or a configured repository attestation, exact head, queue requirement, and merge-method allowance before acting.
4. Auto mode requires a repository-allowed `merge`, `squash`, or `rebase` method; required queue mode accepts no strategy override.
5. No admin flag, protection bypass, force-push, history rewrite, or direct merge API is authorized.
6. The watcher remains read-only. `--await-merge`, `--await-merge-mode auto|queue`, and `--await-merge-since requestedAt` exist only on the CLI, support one PR, and cannot persist approval in `.pr-completion.json`.
7. Background watcher output must be consumed before the agent yields or ends its turn.
8. A landing request is not terminal success; only observed `merged` is.

## Watcher states

| State | Meaning | Agent response |
| --- | --- | --- |
| `pending` | A current-head gate is still running or GitHub state has not stabilized. | Keep polling. |
| `actionable` | CI, reviews, conflicts, or a base update need work. | Repair, commit phase-only, push, restart. |
| `ready` | Current-head checks, approvals, threads, and mergeability satisfy the fail-closed predicate. | Build the landing plan and ask for per-PR confirmation. |
| `auto_merge` | Another actor already configured auto-merge. | Report provenance and observe external state; do not reconfigure it. |
| `awaiting_merge` | An approved landing request was accepted for the exact authorized head. | Keep polling until merged or blocked. |
| `merged` | GitHub reports the PR merged. | Report terminal success. |
| `blocked` | Credentials, authority, policy, stale authorization, or an unreported gate prevents safe progress. | Stop with evidence and a precise unblock. |

Successful observations use process exit `0`; `blocked` uses `20`; timeout uses `30`. JSON `state` remains authoritative.

## Watcher configuration

CLI flags override `.pr-completion.json` keys.

| JSON key | CLI flag | Default | Meaning |
| --- | --- | --- | --- |
| `cursorPath` | `--cursor PATH` | Git-dir/platform state path | Stores the last emitted fingerprint per PR. |
| `observationsPath` | `--observations-file PATH` | `null` | Appends emitted observations as NDJSON. |
| `strictChangesRequested` | `--strict-changes-requested` | `false` | Always treats `CHANGES_REQUESTED` as actionable. |
| `repositoryReadiness` | none | `null` | Explicitly configures one base-protected repository readiness provider. |

### Repository-owned readiness

The watcher-ready path remains the default. An alternate source-qualification path exists only when the repository-root `.pr-completion.json` contains all of these fields:

```json
{
  "version": 1,
  "repositoryReadiness": {
    "provider": "example-validator",
    "providerSchema": "example.readiness.v1",
    "verifier": "scripts/verify-readiness.py",
    "maxAgeSeconds": 3600,
    "blockOnExecutedCheckFailure": true
  }
}
```

The config and Python verifier must be tracked, clean, and identical on the current PR base and candidate; a candidate cannot introduce or alter the provider that qualifies it. The helper invokes the verifier with a fixed argv-only interface, `python -B <verifier> --artifact <path>`, and rejects verifier-caused repository changes. No command string, shell mode, or auto-discovery is supported.

The verifier emits the generic `pr_completion.repository_readiness.v1` shape documented by `skills/take-pr-to-completion/schemas/repository-readiness-v1.schema.json`. It binds repository, PR number, candidate head and tree, base, provider/schema, source-validation result, evidence digest, evaluation time, optional review result, and an attestation digest. Its authority fields must explicitly state that it grants neither operator approval nor merge authority.

Invoke `pr_land.py --repository-readiness <artifact>` to produce a plan. The helper independently refreshes GitHub PR state, rejects a changed identity/head/base, unknown merge policy, review blocker, stale artifact, provider failure, or contradictory/ambiguous executed check state. Absent GitHub checks are allowed in this explicitly configured mode. Confirmation repeats the same artifact plus `--repository-readiness-digest <digest-from-plan>`, the normal `--policy-digest`, and `--confirm`; any change fails closed. Repository readiness establishes source qualification only and never bypasses explicit per-PR confirmation.

`--await-merge HEAD_SHA --await-merge-mode auto|queue --await-merge-since TIMESTAMP` is intentionally CLI-only. It cannot be placed in repository config and rejects multi-target use. `TIMESTAMP` is the helper's `requestedAt` value and must survive process restarts. The watcher allows at most 60 seconds from that timestamp for GitHub's accepted enrollment evidence to appear, then blocks if the auto-merge request or merge-queue entry is absent, rejected, or bound to another head.

## Standalone use

- Commit through full lifecycle: `$pr-completion:commit-workspace-changes`
- Commit locally only: “Use `$pr-completion:commit-workspace-changes`, commit-only; do not push or create a PR.”
- One review round: `$pr-completion:gh-review-comment-triage`
- Stuck conflict: `$pr-completion:merge-conflict-resolution`
- Full lifecycle: `$pr-completion:take-pr-to-completion`

Sibling calls always use the namespaced `$pr-completion:<skill>` form so Claude Code and Codex resolve the same skill tree.
