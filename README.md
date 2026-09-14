# PR Completion

Autonomous pull request completion for [Claude Code](https://code.claude.com/) and [Codex](https://chatgpt.com/codex).

Invoke PR Completion to delegate the in-scope PR through **verified merge**. It prepares changes, uses one managed watcher, repairs CI/review findings, observes automatic incremental reviews, obtains effective approval, and uses GitHub's protected landing path. Routine head changes do not require another user approval or manual review request.

> **Status.** Source `VERSION` `0.4.0` is prepared for release. The last published release is [v0.3.0](https://github.com/anur4ag/pr-completion/releases/tag/v0.3.0), which retains the previous confirmation-based contract. The new behavior is available from this working tree and must be published before marketplace users receive it.
> Docs: [https://anur4ag.github.io/pr-completion/](https://anur4ag.github.io/pr-completion/).
> Publisher: **Business — Traycer**. No directory submission or release publication is claimed by this change.

## Skills

| Skill | Responsibility |
| --- | --- |
| `take-pr-to-completion` | Own task-scoped preparation, review, repair, protected landing and verified merge. |
| `commit-workspace-changes` | Validate and commit local task changes; return to its caller. |
| `gh-review-comment-triage` | Verify inline and top-level findings, repair a complete round, reply and resolve with evidence. |
| `merge-conflict-resolution` | Preserve both sides' intent and validate the combined result. |

Invoke skills by namespaced id, for example `$pr-completion:take-pr-to-completion`. Explicit local-only or stop-at-ready requests retain their narrower scope.

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

After v0.4.0 is published, pin the marketplace to that release tag, then install:

```bash
claude plugin marketplace add anur4ag/pr-completion@v0.4.0
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

After v0.4.0 is published, pin the marketplace to that release tag:

```bash
codex plugin marketplace add anur4ag/pr-completion@v0.4.0
# or: codex plugin marketplace add anur4ag/pr-completion --ref v0.4.0
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
5. At `ready`, the helper revalidates the observed head and lands under the original task authorization.
6. Expect verified **merged**, or a concrete blocker and observation status. A submitted landing request alone is not completion.

## Safety boundary

Invocation authorizes the task's PR lifecycle. Ordinary pushes and repairs preserve that authorization while invalidating stale readiness. The `pr_land.py` helper is the only merge-state mutation surface: it rechecks policy, reviews, checks, head identity, queue requirement and the allowed merge method before using GitHub's protected path. `--dry-run` is optional inspection.

No admin/protection bypass, force-push, published-history rewrite, direct merge API or blocking-review dismissal is permitted. Required eligible approvers remain required. Auto-merge enrollment never suppresses repairs; the watcher stays responsible until GitHub confirms merge. A plain commit request stays local rather than launching a completion workflow.

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
