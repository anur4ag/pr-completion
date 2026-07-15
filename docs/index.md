# PR Completion

A local skills plugin for Claude Code and Codex that prepares a GitHub pull request, watches its gates, repairs actionable failures, and stops at verified merge readiness.

It can validate and commit workspace changes, push in-scope commits, monitor CI and reviews, triage feedback, and resolve conflicts. Its terminal report describes the current head; it does not perform the merge.

<div class="callout">

**Safety boundary.** Success means the current PR head is merge-ready. PR Completion never merges, enables or disables auto-merge, enters a merge queue, force-pushes, or bypasses branch protection. Auto-merge enabled by another actor is observed and reported only.

</div>

## Install

Claude Code:

```bash
claude plugin marketplace add anur4ag/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Codex:

```bash
codex plugin marketplace add anur4ag/pr-completion
codex plugin add pr-completion@pr-completion
```

See [Installation](installation.md) for version pinning, updates, local development installs, and troubleshooting.

## Four sibling skills

| Skill | Run it when |
| --- | --- |
| `take-pr-to-completion` | You want the full commit, watch, review, and conflict loop to continue until the PR is ready or blocked. |
| `commit-workspace-changes` | Local task changes need to be discovered, checked, and committed across repositories or submodules. |
| `gh-review-comment-triage` | Review threads need to be checked against current code, fixed when real, and resolved with evidence. |
| `merge-conflict-resolution` | A merge, rebase, cherry-pick, or revert is conflicted and both sides' intent must be preserved. |

All four ship from one shared `skills/` tree. Claude Code and Codex load the same implementations.

## Deterministic watcher

The orchestrator repeatedly observes the PR and emits one machine-readable state. Its default durable cursor suppresses an identical actionable observation across relaunches:

```text
pending -> actionable -> repair -> new head -> re-observe
                         |
                         +-> ready | auto_merge | merged | blocked
```

Every push invalidates the previous observation. `ready` is calculated again for the new head from required checks, current approvals, unresolved review threads, and mergeability. `auto_merge` means another actor already enabled it; the plugin does not change that setting.

The autonomous loop launches the watcher in the background, consumes its single new observation on exit, dispatches repairs, and relaunches with the same cursor. An optional NDJSON observations file preserves emitted output across harness session recycling. A bot's standing `CHANGES_REQUESTED` decision is treated as a wait state while the current head has no unresolved review threads and checks are still pending.

## Requirements

| Requirement | Supported baseline |
| --- | --- |
| Python | 3.10+ |
| Claude Code | 2.1.207+ |
| Codex CLI | 0.144.3+ |
| Local tools | Git and authenticated GitHub CLI (`gh`) |
| Platforms | macOS, Linux, and Windows; hosted validation covers Python 3.10 and 3.14 plus floor/latest harness install smoke |

Target repositories must also have their own build, lint, test, and hook dependencies installed.

## Project reference

- [Repository](https://github.com/anur4ag/pr-completion)
- [Release v0.2.0](https://github.com/anur4ag/pr-completion/releases/tag/v0.2.0)
- [Skills and safety](skills.md)
- [Support](support.md)
- [Privacy](privacy.md)
- [Terms](terms.md)
- [MIT License](../LICENSE)
- [Security policy](../SECURITY.md)

Publisher identity: **Traycer**. Repository owner: [anur4ag](https://github.com/anur4ag).
