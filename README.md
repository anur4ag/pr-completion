# PR Completion

Autonomous pull request preparation and explicitly approved landing for [Claude Code](https://code.claude.com/) and [Codex](https://chatgpt.com/codex).

Validate and commit local work, create or find its GitHub PR, then drive it through CI, review triage, and conflicts. At **verified readiness**, PR Completion asks for explicit confirmation for that PR and exact head before requesting auto-merge or a required merge-queue entry, then watches until merged or blocked.

> **Status.** Source package `VERSION` `0.3.0` is the release candidate. Its [`v0.3.0`](https://github.com/anur4ag/pr-completion/releases/tag/v0.3.0) link and pinned install become live only after publication.
> Docs: [https://anur4ag.github.io/pr-completion/](https://anur4ag.github.io/pr-completion/).
> Directory publisher identity: **Business — Traycer**. OpenAI portal upload remains a user-controlled step and is not claimed submitted or approved here.

## Skills

| Skill | Authority |
| --- | --- |
| `take-pr-to-completion` | Orchestrates commit, push, PR creation, watcher repairs, per-PR landing confirmation, and post-request observation. Only the guarded helper may request a normal protected landing. |
| `commit-workspace-changes` | Discovers changes, runs checks, and commits. Direct invocation normally hands off to the full lifecycle; explicit local-only/commit-only wording stops after commits. |
| `gh-review-comment-triage` | Fetches review threads, verifies claims against current code, patches real issues, and replies/resolves with evidence. |
| `merge-conflict-resolution` | Resolves merge/rebase/cherry-pick/revert conflicts by reconstructing both intents and validating the result. |

Invoke skills by namespaced id, for example `$pr-completion:take-pr-to-completion`.

## Prerequisites

- **Python** 3.10 or newer (`python3`)
- **Git**
- **GitHub CLI** (`gh`) authenticated to the account that can read and write the target PR
- Repository-specific build, lint, and test tools required by the projects you work in
- One target harness floor:
  - Claude Code **2.1.207+**, or
  - Codex CLI **0.144.3+**

### Platform support status

| Platform | Status |
| --- | --- |
| **macOS** | Supported (hosted CI + local) |
| **Linux** | Supported (hosted CI) |
| **Windows** | Supported (hosted CI) |

Hosted validation runs on `ubuntu-latest`, `macos-latest`, and `windows-latest` for the package suite and isolated install smoke.

## Install

Marketplace name and plugin id are both `pr-completion`. Install as `pr-completion@pr-completion`.

### Claude Code

```bash
claude plugin marketplace add anur4ag/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Pin the marketplace to a release tag, then install:

```bash
claude plugin marketplace add anur4ag/pr-completion@v0.3.0
claude plugin install pr-completion@pr-completion --scope user
```

Refresh marketplace catalog, then update the plugin:

```bash
claude plugin marketplace update pr-completion
claude plugin update pr-completion --scope user
```

Uninstall:

```bash
claude plugin uninstall pr-completion --scope user
# Optional: also drop the marketplace source
claude plugin marketplace remove pr-completion
```

### Codex

```bash
codex plugin marketplace add anur4ag/pr-completion
codex plugin add pr-completion@pr-completion
```

Pin the marketplace to a release tag:

```bash
codex plugin marketplace add anur4ag/pr-completion@v0.3.0
# or: codex plugin marketplace add anur4ag/pr-completion --ref v0.3.0
codex plugin add pr-completion@pr-completion
```

Refresh marketplace snapshot, then reinstall to pick up a new version:

```bash
codex plugin marketplace upgrade pr-completion
codex plugin remove pr-completion@pr-completion
codex plugin add pr-completion@pr-completion
```

Uninstall:

```bash
codex plugin remove pr-completion@pr-completion
# Optional: also drop the marketplace source
codex plugin marketplace remove pr-completion
```

## First use

1. Open a repository with an open pull request or local task changes ready to commit.
2. Authenticate GitHub CLI: `gh auth status` should succeed for that host.
3. Ask the agent to drive the PR with `$pr-completion:take-pr-to-completion`.
4. The agent prepares or creates the PR and autonomously handles normal CI, review, and conflict cycles.
5. At `ready`, review the per-PR prompt. It names the repository, PR URL, exact head SHA, action/method, and warns that approval may merge immediately.
6. Approve that PR's landing or stop at readiness. Approval is never reused for another PR or a changed head.
7. After approval, expect a terminal report of **merged** or **blocked**; without approval, expect **ready** with evidence.

## Safety boundary

Routine work stops at verified readiness until you explicitly approve one PR and one current head SHA. The audited `pr_land.py` helper is the only merge-state mutation surface. It rechecks the resolved watcher policy, readiness, head identity, queue requirement, and allowed merge method immediately before using GitHub's normal protected auto-merge or merge-queue path.

The workflow never uses admin bypass, force-push, protection bypass, history rewrite, direct REST/GraphQL merge mutations, or implicit/bulk approval. A changed head invalidates approval. After an approved request, the read-only watcher remains active in `awaiting_merge` only while the exact-head auto-merge request or merge-queue entry remains observable; vanished or rejected enrollment becomes a blocker, and only an exact-head `merged` observation is success.

## Privacy and license

- Plugin code and local helpers execute on your machine inside Claude Code or Codex.
- Those harnesses, plus tools you invoke (especially Git and `gh`), may transmit data to **their** configured providers under **their** policies.
- The publisher operates no backend, analytics service, telemetry collector, or credential proxy for this plugin.
- GitHub access uses your existing `gh` authentication; GitHub remains operated by GitHub, not by this publisher.
- License: [MIT](LICENSE)

Public legal and support pages:

| Page | URL |
| --- | --- |
| Site home | `https://anur4ag.github.io/pr-completion/` |
| Support | `https://anur4ag.github.io/pr-completion/support/` |
| Privacy | `https://anur4ag.github.io/pr-completion/privacy/` |
| Terms | `https://anur4ag.github.io/pr-completion/terms/` |

## Documentation

Local source of truth for durable docs:

- [docs/index.md](docs/index.md) - overview
- [docs/installation.md](docs/installation.md) - install, pin, update, uninstall, troubleshooting
- [docs/skills.md](docs/skills.md) - skill authority and safety contract
- [docs/support.md](docs/support.md) - support and issue routing
- [docs/privacy.md](docs/privacy.md) - privacy statement
- [docs/terms.md](docs/terms.md) - terms of use
- [SECURITY.md](SECURITY.md) - security reporting

Build and link-check the GitHub Pages site locally:

```bash
python3 scripts/build-docs.py
python3 scripts/check-docs-links.py
```

## Publisher

- Marketplace / directory publisher identity: **Traycer** (portal label **Business — Traycer**)
- Canonical GitHub repository owner: [anur4ag](https://github.com/anur4ag)
- Repository: `https://github.com/anur4ag/pr-completion`
- Copyright remains as stated in [`LICENSE`](LICENSE). Repository ownership and copyright attribution are separate from the verified business portal identity used for directory submission.

## Version

Canonical version file: [`VERSION`](VERSION). Claude, Codex, and marketplace manifests must match it.
