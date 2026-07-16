# PR Completion

A local skills plugin for Claude Code and Codex that prepares a GitHub pull request, watches its gates, repairs actionable failures, and—only with explicit per-PR approval—requests a protected landing and observes it to completion.

It validates and commits workspace changes, creates or finds PRs, monitors CI and reviews, triages feedback, and resolves conflicts. Direct use of the commit skill continues into this lifecycle unless you say local-only or commit-only.

<div class="callout">

**Safety boundary.** Routine autonomy reaches a verified-ready exact head. Landing requires a separate confirmation for each PR, warns that approval may merge immediately, and is revalidated before the guarded request. PR Completion never uses admin or protection bypass, force-push, history rewrite, or direct merge APIs.

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
| `take-pr-to-completion` | You want preparation, watcher repairs, per-PR landing confirmation, and observation until merged or blocked. |
| `commit-workspace-changes` | Local changes need checks and commits; direct use normally hands off into PR creation and monitoring unless explicitly local-only. |
| `gh-review-comment-triage` | Review threads need to be checked against current code, fixed when real, and resolved with evidence. |
| `merge-conflict-resolution` | A merge, rebase, cherry-pick, or revert is conflicted and both sides' intent must be preserved. |

All four ship from one shared `skills/` tree. Claude Code and Codex load the same implementations.

## Deterministic watcher

The orchestrator repeatedly observes the PR and emits one machine-readable state. Its default durable cursor suppresses an identical actionable observation across relaunches:

```text
pending -> actionable -> repair -> new head -> re-observe
                         |
                         +-> ready -> confirm exact PR/head -> awaiting_merge -> merged
                                  \-> stop at ready                 \-> blocked
```

Every push invalidates the previous observation and any landing confirmation. `ready` is recalculated from checks, approvals, unresolved threads, and mergeability. `auto_merge` means another actor already configured it. `awaiting_merge` is emitted only after an approved request bound to the exact head.

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
- [Release v0.3.0](https://github.com/anur4ag/pr-completion/releases/tag/v0.3.0) (pending until publication)
- [Skills and safety](skills.md)
- [Support](support.md)
- [Privacy](privacy.md)
- [Terms](terms.md)
- [MIT License](../LICENSE)
- [Security policy](../SECURITY.md)

Publisher identity: **Traycer**. Repository owner: [anur4ag](https://github.com/anur4ag).
