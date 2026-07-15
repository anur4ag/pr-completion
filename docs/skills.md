# Skills and safety

`pr-completion` ships four independently invokable skills from one shared implementation tree.

Namespaced ids:

- `$pr-completion:take-pr-to-completion`
- `$pr-completion:commit-workspace-changes`
- `$pr-completion:gh-review-comment-triage`
- `$pr-completion:merge-conflict-resolution`

## Authority of each skill

### `take-pr-to-completion`

**Role.** Orchestrate a PR to verified merge readiness with a deterministic watcher.

**In scope.**

- Preflight of repository, branch, PR, head SHA, and auth
- Delegating local commit preparation to `commit-workspace-changes`
- Pushing in-scope commits to the PR branch
- Running `scripts/pr_watch.py` and dispatching actionable states
- CI diagnosis and branch-caused repairs
- Review-round triage via `gh-review-comment-triage`
- Conflict and required base-update handling via `merge-conflict-resolution`
- Check reruns when flakiness is evidenced
- Thread replies and resolution when authorized by the workflow

**Out of scope (hard boundary).**

- `gh pr merge` and GraphQL/REST merge mutations
- Enabling, disabling, or reconfiguring auto-merge
- Enqueueing merge-queue entries
- Force-push and history rewrite used as recovery
- Branch-protection or admin bypasses

**Terminal success.** The current head is **verified merge-ready** (required checks pass, required approvals current for that head, unresolved review threads are zero, mergeability is non-conflicting), or the skill reports an already-merged PR / externally enabled auto-merge / blocked state with evidence.

### `commit-workspace-changes`

**Role.** Turn intended local work into validated commits across repositories and submodules.

**In scope.**

- Discovering owning repositories and nested modules
- Deriving and running repository validation (lint, tests, hooks, generators)
- Fixing task-caused failures narrowly
- Staging intended paths and creating commits (including DCO sign-off when required)

**Out of scope.**

- Pushing or opening PRs unless a parent workflow performs those steps
- Bypassing hooks or weakening tests
- Committing unrelated user changes

### `gh-review-comment-triage`

**Role.** Verify review threads against **current** code before changing anything.

**In scope.**

- Fetching threads with GitHub GraphQL via `gh`
- Classifying findings as real, already fixed, stale, false positive, or needs user decision
- Patching real issues and adding focused regression tests when useful
- Replying and resolving with evidence when mutation is authorized

**Out of scope.**

- Resolving threads without fix or non-actionability evidence
- Batching unrelated findings into vague changes
- Committing or pushing unless the invoking workflow authorizes it

### `merge-conflict-resolution`

**Role.** Resolve conflicted merge, rebase, cherry-pick, or revert operations safely.

**In scope.**

- Inspecting base/ours/theirs and reconstructing both intents
- Producing a combined resolution without leaving conflict markers
- Validating touched behavior before continuation

**Out of scope.**

- Destructive recovery that discards unrelated user work
- Choosing a side solely because it compiles
- Hiding product or architecture uncertainty that code cannot decide

## Safety contract

```text
Observe GitHub state
  -> repair CI / triage reviews / resolve conflicts (as needed)
  -> re-observe on new head after every push
  -> stop at ready | auto_merge (external) | merged | blocked
  -> never mutate merge state
```

Enforced invariants:

1. No merge-state mutation commands or API equivalents.
2. “Ready” is evaluated against the **current** head SHA and current gates.
3. A push invalidates prior observation; the watcher restarts.
4. Successful watcher observations exit process status `0`; JSON `state` is the machine contract.
5. Background watcher execution is not completion until the agent consumes final JSON.
6. The same durable cursor is reused across relaunches so an identical actionable observation is not re-reported.
7. Lost harness completion metadata is recovered from the durable output or observations file, never interpreted as an empty result.
8. Unrelated dirty changes, missing credentials, and non-derivable product decisions escalate rather than invent authority.

### Watcher configuration

CLI flags override the corresponding `.pr-completion.json` keys.

| JSON key | CLI flag | Default | Meaning |
| --- | --- | --- | --- |
| `cursorPath` | `--cursor PATH` | `$GIT_DIR/pr-completion/pr-watch-cursors.json` (platform state fallback) | Atomically stores the last emitted fingerprint per PR target. `null` disables it. |
| `observationsPath` | `--observations-file PATH` | `null` | Appends each emitted observation as one NDJSON line. |
| `strictChangesRequested` | `--strict-changes-requested` | `false` | Always reports `CHANGES_REQUESTED` as actionable. |

With the default non-strict behavior, `CHANGES_REQUESTED` plus zero unresolved threads plus pending current-head checks is reported as pending `review_rerun`. Unresolved threads, or a standing decision after pending checks finish, remain actionable.

### Watcher states

| State | Meaning | Agent response |
| --- | --- | --- |
| `pending` | A required gate, including a thread-free bot re-review, is still running or GitHub state has not stabilized. | Wait, then observe again. |
| `actionable` | CI, reviews, conflicts, or a required base update need work. | Dispatch the matching sibling skill, then re-observe. |
| `ready` | Current-head checks, approvals, threads, and mergeability satisfy the readiness contract. | Stop and report evidence; do not merge. |
| `auto_merge` | Another actor already enabled auto-merge. | Stop and report the external setting; do not change it. |
| `merged` | GitHub reports the PR already merged. | Stop and report the terminal state. |
| `blocked` | Progress needs authority, credentials, or a decision the workflow cannot derive. | Stop with evidence and the specific unblock requirement. |

`pending`, `actionable`, `ready`, `auto_merge`, and `merged` are successful observations at the process level; the emitted JSON `state` remains the machine contract. `blocked` uses a non-zero exit status.

## Why the boundary is strict

GitHub does not expose an atomic precondition that guarantees enabling auto-merge cannot race with a final required check and merge immediately.
`expectedHeadOid` protects head identity, not full gate status.
Therefore the public workflow never enables auto-merge as part of “completion.”

## Using the skills alone

Each skill can be invoked independently.
Typical standalone uses:

- Commit loop only: `$pr-completion:commit-workspace-changes`
- One review round: `$pr-completion:gh-review-comment-triage`
- Stuck conflict: `$pr-completion:merge-conflict-resolution`
- Full babysit: `$pr-completion:take-pr-to-completion`

When skills call siblings, they use the namespaced `$pr-completion:<skill>` form so both harnesses resolve the shared tree.
