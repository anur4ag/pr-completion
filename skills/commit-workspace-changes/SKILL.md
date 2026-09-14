---
name: commit-workspace-changes
description: Validate and commit task-related changes across Git repositories and submodules. Use for local commits or as the commit phase of PR Completion; the calling workflow owns pushing and landing.
---

# Commit Workspace Changes

Create validated, task-scoped commits and return to the caller. A commit request does not independently authorize publishing or merging. When called by PR Completion, continue there without recursively invoking another lifecycle.

1. Reuse the caller's repository/branch inventory, or discover ownership from Git metadata. Snapshot staged, unstaged, untracked changes and active Git operations. Read applicable instructions; preserve unrelated work and intentional staging.
2. Resolve in-scope conflicts with `$pr-completion:merge-conflict-resolution`. Process changed submodules before parents. Derive the intended branch rather than committing blindly from detached HEAD.
3. Derive required validation from repository instructions, hooks and CI. Reuse successful evidence for the same tree and scope. Run focused checks and every mandated check not already covered. Do not duplicate a broad check solely because a helper handoff occurred.
4. Fix task-caused failures and inspect formatter output. Rerun failed/invalidated checks. Diagnose pre-existing, environment or unrelated failures with evidence; do not loop unchanged commands, weaken tests, bypass hooks, or claim unsupported flakiness.
5. Re-read status and staged diff. Stage explicit intended paths; exclude secrets, conflict markers, artifacts and unrelated edits. Split commits only where independent concerns or repository convention warrant it. Infer message style and use DCO sign-off when required.
6. Commit normally. If a hook changes files, inspect and restage only intended changes. Verify the new commit and remaining worktree; do not make empty commits. Commit a parent gitlink only when it belongs to the task.

Return repository, commit, validation evidence and remaining changes/blockers concisely. The lifecycle owner handles pushing, PRs, reviews and landing.
