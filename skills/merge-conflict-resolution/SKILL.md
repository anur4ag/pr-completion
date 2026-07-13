---
name: merge-conflict-resolution
description: Safely resolve Git merge, rebase, cherry-pick, or revert conflicts by understanding both sides and validating the result. Use for conflicted branches, upstream updates, or stuck continuation operations.
---

# Merge Conflict Resolution

## Workflow

1. Snapshot `git status`, the active operation, and unmerged paths with `git diff --name-only --diff-filter=U`. Read repository instructions.
2. For each conflict, inspect the working tree plus base, ours, and theirs when available: `git show :1:path`, `:2:path`, and `:3:path`.
3. Reconstruct both intents from nearby code, call sites, types, tests, and recent commits. Do not choose a side merely because it compiles.
4. Preserve the correct combined behavior, local style, and unrelated user changes. Remove every conflict marker.
5. Ask only when code and history cannot resolve a product or architecture choice.
6. Run `git diff --check`, focused validation for touched behavior, and broader checks when shared contracts changed.
7. If continuation is authorized, stage only resolved files and use the appropriate non-interactive merge/rebase/cherry-pick/revert continuation. Do not continue while required validation fails.

Never use destructive recovery commands, discard unrelated changes, or hide semantic uncertainty. Report files resolved, key decisions, validation, continuation state, and remaining blockers.
