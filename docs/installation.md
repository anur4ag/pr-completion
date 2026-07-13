# Installation

Install `pr-completion` from the GitHub repository marketplace `anur4ag/pr-completion`.
The plugin id and marketplace name are both `pr-completion`, so the install selector is `pr-completion@pr-completion`.

<div class="callout">

Marketplace installs require the public repository and tagged releases to exist.
Until ticket 5 verifies publication, treat commands below as the **intended** install path and validate against a local clone when testing early.

</div>

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Used by the PR watcher and packaging scripts (`python3`) |
| Git | Required for marketplace clones and repository work |
| GitHub CLI (`gh`) | Must be authenticated for the host that owns the PR |
| Harness | Claude Code **2.1.207+** or Codex CLI **0.144.3+** (local compatibility floors) |
| Project toolchains | Install whatever the target repositories need to build, lint, and test |

### Platform support status

| Platform | Status |
| --- | --- |
| **macOS** | Locally verified during package development |
| **Linux** | Target support; planned until public hosted CI is green |
| **Windows** | Target support; planned until public hosted CI is green |

This is not yet a publication-verified three-OS matrix. Ticket 5 must record green hosted jobs before Linux/Windows or multi-OS floor claims are promoted.

Check harness versions:

```bash
claude --version
codex --version
gh auth status
python3 --version
```

## Claude Code

### Install (latest marketplace snapshot)

```bash
claude plugin marketplace add anur4ag/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Scopes: `user` (default in examples), `project`, or `local`. Prefer `user` for personal installs.

### Pin to a release tag

Pin the marketplace source to a git ref, then install:

```bash
claude plugin marketplace add anur4ag/pr-completion@v0.1.0
claude plugin install pr-completion@pr-completion --scope user
```

Equivalent git-URL form:

```bash
claude plugin marketplace add https://github.com/anur4ag/pr-completion.git#v0.1.0
claude plugin install pr-completion@pr-completion --scope user
```

### Update

Refreshing the marketplace catalog and updating the installed plugin are separate steps:

```bash
claude plugin marketplace update pr-completion
claude plugin update pr-completion --scope user
```

Restart Claude Code after update when the CLI reports that a restart is required to apply the new version.

### Uninstall

```bash
claude plugin uninstall pr-completion --scope user
claude plugin marketplace remove pr-completion
```

Removing the marketplace also removes plugins installed from it when it is the last remaining declaration for that marketplace.

### Inventory check

```bash
claude plugin list
claude plugin details pr-completion@pr-completion
```

You should see the four skills:

- `take-pr-to-completion`
- `commit-workspace-changes`
- `gh-review-comment-triage`
- `merge-conflict-resolution`

## Codex

### Install (latest marketplace snapshot)

```bash
codex plugin marketplace add anur4ag/pr-completion
codex plugin add pr-completion@pr-completion
```

### Pin to a release tag

```bash
codex plugin marketplace add anur4ag/pr-completion@v0.1.0
codex plugin add pr-completion@pr-completion
```

Or pass the ref explicitly:

```bash
codex plugin marketplace add anur4ag/pr-completion --ref v0.1.0
codex plugin add pr-completion@pr-completion
```

### Update

Codex refreshes marketplace snapshots with `marketplace upgrade`. Reinstall the plugin to pick up a newer snapshot version:

```bash
codex plugin marketplace upgrade pr-completion
codex plugin remove pr-completion@pr-completion
codex plugin add pr-completion@pr-completion
```

Upgrade all configured marketplaces:

```bash
codex plugin marketplace upgrade
```

### Uninstall

```bash
codex plugin remove pr-completion@pr-completion
codex plugin marketplace remove pr-completion
```

### Inventory check

```bash
codex plugin list
codex plugin list --available --json
```

Confirm the four expected skills are present for `pr-completion`.

## Local clone install (development)

For development against an unchecked-in tree:

Claude:

```bash
claude plugin marketplace add /absolute/path/to/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Codex (local path marketplace):

```bash
codex plugin marketplace add /absolute/path/to/pr-completion
codex plugin add pr-completion@pr-completion
```

Release source uses plain SemVer in manifests.
Codex local iteration may stage a temporary cachebusted copy via `scripts/stage-codex-dev-install.py` so the committed manifests stay clean.

## First use

1. Open a repository with either an open PR or task-related local changes.
2. Ensure `gh auth status` succeeds for that GitHub host.
3. Ask the agent to run `$pr-completion:take-pr-to-completion`.
4. Read the terminal report: merge-ready, externally auto-merge-enabled, already merged, or blocked.
5. Merge with your own process if desired. This plugin does not merge.

## Troubleshooting

### Marketplace add fails

- Confirm the repository is public (or your credentials can read a private fork).
- For Claude, prefer `owner/repo` or a full `https://...git` URL with scheme.
- For Codex, `owner/repo[@ref]`, HTTPS, SSH, or a local path are valid sources.

### Plugin installs but skills are missing

- Re-check the selector: `pr-completion@pr-completion` (plugin@marketplace).
- List plugins and details after a clean reinstall.
- Ensure you did not install a differently named marketplace that shadows `pr-completion`.

### `gh` authentication errors during a run

- Run `gh auth login` for the correct host.
- Confirm scopes include repository access needed to read checks, reviews, and push to the PR branch.
- The plugin does not store or broker GitHub credentials.

### Watcher or Python errors

- Require Python 3.10+: `python3 --version`.
- Run from the PR repository working tree so Git and `gh` resolve the correct remote.

### CI or install matrix claims

Today:

- **macOS** work during package development is the only locally verified platform claim.
- **Linux** and **Windows** are target platforms for the public CI matrix; they are not publication-verified until hosted jobs are green (ticket 5).
- Claude Code **2.1.207** and Codex CLI **0.144.3** are local compatibility floors, not multi-OS hosted verification results.

Do not treat marketing copy or early drafts as a completed three-OS support matrix.

## Related pages

- [Skills and safety](skills.md)
- [Support](support.md)
- [Privacy](privacy.md)
