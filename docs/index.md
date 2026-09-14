# PR Completion

A local skills plugin for Claude Code and Codex that owns in-scope GitHub PRs through **verified merge** under the invoking task's authorization.

It prepares changes, reuses one managed watcher, repairs CI and review findings, observes automatic incremental review, obtains effective approval, and lands through GitHub protections. Head changes trigger fresh readiness evaluation without another user prompt or routine manual review request. The commit helper returns local commits to its caller.

<div class="callout">

**Release status.** Version 0.4.0 is prepared locally; marketplace users receive these changes after publication. Invocation authorizes completion, while explicit local-only/stop-at-ready requests remain narrower. No admin/protection bypass, force-push, or blocking-review dismissal is permitted.

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
| `take-pr-to-completion` | You want the in-scope PRs merged after checks, reviews, and repairs. |
| `commit-workspace-changes` | Local changes need validation and commits; return evidence to the calling workflow. |
| `gh-review-comment-triage` | Review threads need to be checked against current code, fixed when real, and resolved with evidence. |
| `merge-conflict-resolution` | A merge, rebase, cherry-pick, or revert is conflicted and both sides' intent must be preserved. |

All four ship from one shared `skills/` tree. Claude Code and Codex load the same implementations.

## Deterministic watcher

The watcher combines paginated review threads, review bodies, PR comments and reactions with structured GitHub check and merge metadata. One managed monitor runs it through repair, readiness and enrollment. It emits changes, keeps an atomic latest snapshot, and replays unfinished work after restart.

```text
prepare -> watch -> repair/triage -> push -> automatic incremental review
             |                                   |
             +-------------- ready <-------------+
                               |
                      protected landing -> watch -> merged
```

A pending dependent PR cannot hide a ready upstream PR. A failed gate remains actionable even after auto-merge enrollment. The agent judges review claims; durable acknowledgements track handled feedback by content so edited/new findings reappear. See [Skills and safety](skills.md) for the runtime interface and migration from 0.3.0.

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
- [Release v0.4.0 (pending)](https://github.com/anur4ag/pr-completion/releases/tag/v0.4.0)
- [Skills and safety](skills.md)
- [Support](support.md)
- [Privacy](privacy.md)
- [Terms](terms.md)
- [MIT License](../LICENSE)
- [Security policy](../SECURITY.md)

Publisher identity: **Traycer**. Repository owner: [anur4ag](https://github.com/anur4ag).
