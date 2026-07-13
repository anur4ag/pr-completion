---
name: commit-workspace-changes
description: Autonomously discover task-related changes across Git repositories and submodules, derive and run required checks, fix failures, and create compliant local commits in each owning repository. Use for the commit loop, run-checks-and-commit requests, or local PR preparation without predefined repository boundaries or commands.
---

# Commit Workspace Changes

Turn intended local work into validated commits in the repositories that own it. Do not push or create PRs; a parent workflow may do that.

## Operating contract

Invocation authorizes repository discovery, ordinary checks, in-scope fixes, formatter output, explicit staging, hooks, and commits. Infer routine choices from instructions, manifests, CI, history, diffs, and task context. Ask only for a non-derivable semantic decision, unavailable required tooling, overlapping unrelated edits, or destructive action.

## Discover ownership

1. Snapshot the current directory, repository, branch, HEAD, staged/unstaged/untracked changes, and active merge, rebase, cherry-pick, or revert state.
2. Use Git metadata to discover nested repositories and registered submodules. Map each changed path to its owning repository and process deepest children before parents.
3. Read applicable repository and subtree instructions for every changed repository.
4. Separate task work from unrelated user changes using task context, diffs, and existing staging. Preserve unrelated work.
5. Confirm each repository is on a safe branch. Infer a detached repository's intended branch from worktree metadata; escalate only if no safe branch is derivable.

If an in-scope merge or rebase has unresolved conflicts, load `$pr-completion:merge-conflict-resolution` before the normal commit loop.

## Derive and run checks

Build each repository's validation contract from its instructions, pre-commit and hook configuration, package scripts, workspace or task-runner configuration, language manifests, CI workflows, and contributor documentation.

Run focused formatting, lint, compile or type checks, tests, and generated-file checks first for useful feedback. Then run every repository-mandated broader check, including pre-commit when required.

For each failure:

1. Classify it as task-caused, pre-existing, environment/tooling, or caused by unrelated working-tree state.
2. Fix task-caused failures narrowly and add regression coverage when warranted.
3. Inspect formatter or generator output before accepting it.
4. Rerun the failed check and everything invalidated by the fix.
5. Repeat until the full validation contract passes and check-generated changes are validated.

Do not bypass hooks, weaken tests, suppress diagnostics, invent alternate commands when canonical ones exist, or claim flakiness without evidence. Escalate a pre-existing or external failure only after confirming the task did not cause it.

## Commit safely

For each repository with intended changes:

1. Re-read status and diffs. Reject conflict markers, secrets, debug output, accidental artifacts, and unrelated files.
2. Preserve meaningful existing staging. Stage explicit intended paths; use broad staging only when every change is in scope.
3. Inspect the staged diff. Split independent concerns when repository convention or reviewability requires it.
4. Infer message style and scope from instructions and recent history.
5. Use `git commit -s` when DCO is required by instructions, CI, convention, or the user. Preserve other signing and hooks.
6. If a hook fails or edits files, return to validation and restage intentionally.
7. Verify the commit and remaining worktree. Never create an empty commit.

### Nested repositories and submodules

Commit content in its owning repository. A child commit may change the gitlink seen by its parent; do not stage that pointer as a parent source change. Commit it only when parent policy makes the pin update part of this workflow. Otherwise leave and report it. Validate intentional child and parent changes separately.

## Completion

Finish when required checks and hooks pass, intended changes are committed with required sign-off, unrelated work is preserved, and no task-related changes remain except explicitly deferred policy artifacts such as a parent gitlink.

Report repositories found, checks and fixes, commits created, deliberately uncommitted changes, and blockers.
