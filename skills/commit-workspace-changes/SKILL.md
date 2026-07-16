---
name: commit-workspace-changes
description: Autonomously discover task-related changes across Git repositories and submodules, run required checks, fix failures, and create compliant commits. A direct invocation normally hands off to PR creation and the full PR Completion watcher; use explicit local-only or commit-only wording to stop after commits.
---

# Commit Workspace Changes

Turn intended work into validated commits in every repository that owns it. When this skill was invoked directly, continue into `$pr-completion:take-pr-to-completion` after the commit phase unless the user explicitly asked for local-only, commit-only, no-push, or no-PR work.

## Invocation mode

Choose the mode from the immediate caller:

- **Direct lifecycle mode (default):** the user invoked this skill or asked for the commit loop without a local-only boundary. Complete the commit phase, then hand off once to `$pr-completion:take-pr-to-completion`, passing the repositories, branches, commits, validation evidence, and remaining worktree state.
- **Phase-only child mode:** `$pr-completion:take-pr-to-completion` explicitly invoked this skill as its commit phase. Return results to the parent; never invoke the parent again.
- **Explicit local-only mode:** the user said commit-only, local-only, no push, no PR, or equivalent. Stop after validated local commits.

This mode boundary prevents recursive skill dispatch while making a direct commit-loop request continue through PR creation and monitoring by default.

## Operating contract

Invocation authorizes repository discovery, ordinary checks, in-scope fixes, formatter output, explicit staging, hooks, and commits. Infer routine choices from instructions, manifests, CI, history, diffs, and task context. Ask only for a non-derivable semantic decision, unavailable required tooling, overlapping unrelated edits, or destructive action.

## Discover ownership

1. Snapshot the current directory, repository, branch, HEAD, staged/unstaged/untracked changes, and any active merge, rebase, cherry-pick, or revert.
2. Discover nested repositories and registered submodules from Git metadata. Map each changed path to its owner and process deepest children before parents.
3. Read applicable instructions for every changed repository.
4. Separate task work from unrelated user changes. Preserve unrelated work and meaningful existing staging.
5. Confirm each repository is on a safe branch. Escalate if a detached repository has no derivable intended branch.

If an in-scope operation has unresolved conflicts, load `$pr-completion:merge-conflict-resolution` before the normal commit loop.

## Derive and run checks

Build each repository's validation contract from its instructions, hooks, manifests, package scripts, task-runner configuration, CI workflows, and contributor documentation. Run focused formatting, lint, compile/type checks, tests, and generated-file checks first, then every mandated broad check.

For each failure:

1. Classify it as task-caused, pre-existing, environment/tooling, or unrelated-state interference.
2. Fix task-caused failures narrowly; add regression coverage when warranted.
3. Inspect formatter or generator output before accepting it.
4. Rerun the failed check and everything invalidated by the fix.
5. Repeat until the full validation contract passes.

Do not bypass hooks, weaken tests, suppress diagnostics, invent alternate commands when canonical ones exist, or claim flakiness without evidence.

## Commit safely

For each repository with intended changes:

1. Re-read status and diffs. Reject conflict markers, secrets, debug output, accidental artifacts, and unrelated files.
2. Stage explicit intended paths and inspect the staged diff.
3. Split independent concerns when repository convention or reviewability requires it.
4. Infer message style and scope from instructions and recent history.
5. Use `git commit -s` when DCO is required.
6. If a hook fails or edits files, return to validation and restage intentionally.
7. Verify the commit and remaining worktree. Never create an empty commit.

Commit child content in its owning repository. A child commit may alter the parent gitlink; commit that pointer only when parent policy makes the pin update part of this workflow.

## Complete or hand off

First report repositories, checks, fixes, commits, deliberately uncommitted changes, and blockers. Then:

- In direct lifecycle mode, invoke `$pr-completion:take-pr-to-completion` exactly once and identify this work as an already-completed commit phase.
- In phase-only child mode, return to the parent.
- In explicit local-only mode, stop without pushing or creating a PR.
