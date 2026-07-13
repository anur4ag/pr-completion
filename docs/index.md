# PR Completion

Local skills plugin that takes a GitHub pull request to **verified merge readiness** for Claude Code and Codex.

It validates and commits workspace changes, watches CI and reviews, triages feedback, and resolves conflicts. It stops when the current head is merge-ready. It never merges, enables auto-merge, joins a merge queue, force-pushes, or bypasses branch protections.

<div class="callout">

**Publication status.** These pages are built from repository source so install, safety, and legal copy can be reviewed before hosting is enabled.
Planned public origin: `https://anur4ag.github.io/pr-completion/`.
Do not treat that origin as live until ticket 5 verifies Pages and anonymous install paths.

</div>

## What you get

| Skill | Role |
| --- | --- |
| `take-pr-to-completion` | Orchestrator. Drives the PR lifecycle and stops at merge-ready. |
| `commit-workspace-changes` | Discovers task-related changes, runs checks, creates local commits. |
| `gh-review-comment-triage` | Verifies review threads against current code and resolves with evidence. |
| `merge-conflict-resolution` | Resolves Git conflicts by preserving combined intent. |

All four skills ship from one shared `skills/` tree. Claude and Codex manifests point at that tree; skill implementations are not forked per harness.

## Quick install

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

Full pin, update, uninstall, and troubleshooting steps: [Installation](installation.md).

## Prerequisites

- Python **3.10+**
- Git
- Authenticated GitHub CLI (`gh`)
- Project-specific build/lint/test tools for the repositories you touch
- Claude Code **2.1.207+** or Codex CLI **0.144.3+** (local compatibility floors; not yet multi-OS publication-verified)

### Platform support status

| Platform | Status |
| --- | --- |
| macOS | Locally verified during package development |
| Linux | Target support pending green public hosted CI |
| Windows | Target support pending green public hosted CI |

Three-OS and floor-version claims become publication-verified only when ticket 5 records successful hosted jobs.

## Safety in one line

**Terminal success = verified merge-ready head, not a merged PR.**

See [Skills and safety](skills.md) for skill authority and the full mutation boundary.

## Privacy, terms, support

- [Privacy](privacy.md) - local plugin code; harness/tool provider transmission; no publisher telemetry or backend
- [Terms](terms.md) - MIT license and usage terms
- [Support](support.md) - GitHub Issues and security reporting path
- Repository security policy: [`SECURITY.md`](../SECURITY.md) in source

## Publisher

- Publisher name: **Anurag Sharma** (individual)
- GitHub: [anur4ag](https://github.com/anur4ag)
- Canonical repository: [anur4ag/pr-completion](https://github.com/anur4ag/pr-completion)
- License: MIT

Individual publisher verification for directory/marketplace surfaces is an external step and is not claimed complete on these pages.
