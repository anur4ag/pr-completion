# Installation

Install `pr-completion` from the public GitHub marketplace repository `anur4ag/pr-completion`; both the plugin id and marketplace name are `pr-completion`.

## Requirements

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Runs the deterministic PR watcher and local helpers. |
| Git | Required for marketplace clones and repository work. |
| GitHub CLI (`gh`) | Authenticate for the host that owns the PR. |
| Harness | Claude Code 2.1.207+ or Codex CLI 0.144.3+. |
| Project toolchains | Install the build, lint, test, and hook dependencies used by the target repositories. |

The hosted matrix validates macOS, Linux, and Windows with Python 3.10 and 3.14. Separate install-smoke jobs exercise the floor and current stable Claude Code and Codex CLI channels on all three platforms.

Check the local environment:

```bash
python3 --version
git --version
gh auth status
claude --version
codex --version
```

## Claude Code

### Install

```bash
claude plugin marketplace add anur4ag/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Use `--scope user` for a personal install. Claude Code also supports `project` and `local` scopes.

### Pin v0.3.0

```bash
claude plugin marketplace add anur4ag/pr-completion@v0.3.0
claude plugin install pr-completion@pr-completion --scope user
```

Equivalent Git URL form:

```bash
claude plugin marketplace add https://github.com/anur4ag/pr-completion.git#v0.3.0
claude plugin install pr-completion@pr-completion --scope user
```

### Update

```bash
claude plugin marketplace update pr-completion
claude plugin update pr-completion --scope user
```

Restart Claude Code if the CLI reports that a restart is required.

### Inspect or remove

```bash
claude plugin list
claude plugin details pr-completion@pr-completion
claude plugin uninstall pr-completion --scope user
claude plugin marketplace remove pr-completion
```

## Codex

### Install

```bash
codex plugin marketplace add anur4ag/pr-completion
codex plugin add pr-completion@pr-completion
```

### Pin v0.3.0

```bash
codex plugin marketplace add anur4ag/pr-completion@v0.3.0
codex plugin add pr-completion@pr-completion
```

Or pass the ref separately:

```bash
codex plugin marketplace add anur4ag/pr-completion --ref v0.3.0
codex plugin add pr-completion@pr-completion
```

### Update

```bash
codex plugin marketplace upgrade pr-completion
codex plugin remove pr-completion@pr-completion
codex plugin add pr-completion@pr-completion
```

Use `codex plugin marketplace upgrade` without a name to refresh every configured marketplace.

### Inspect or remove

```bash
codex plugin list
codex plugin list --available --json
codex plugin remove pr-completion@pr-completion
codex plugin marketplace remove pr-completion
```

## Local development install

Use an absolute path to an unchecked-in clone.

Claude Code:

```bash
claude plugin marketplace add /absolute/path/to/pr-completion
claude plugin install pr-completion@pr-completion --scope user
```

Codex:

```bash
codex plugin marketplace add /absolute/path/to/pr-completion
codex plugin add pr-completion@pr-completion
```

Codex development may use `scripts/stage-codex-dev-install.py` to create a temporary cache-busted copy. Committed manifests keep plain SemVer.

## First run

1. Open a repository with an open PR or task-related local changes.
2. Confirm `gh auth status` succeeds for the PR host.
3. Ask the agent to run `$pr-completion:take-pr-to-completion`.
4. Let it commit, push, create/find the PR, and handle normal watcher cycles.
5. At `ready`, approve or decline the prompt for that PR and exact head SHA. The prompt warns that approval may merge immediately.
6. After approval, read the terminal state: `merged` or `blocked`. If you decline, the result remains `ready`.

## Troubleshooting

### Marketplace add fails

- Confirm your network and credentials can read `anur4ag/pr-completion`.
- For Claude Code, use `owner/repo` or a complete HTTPS Git URL.
- For Codex, use `owner/repo[@ref]`, HTTPS, SSH, or a local path.

### Plugin installs but skills are absent

- Confirm the selector is `pr-completion@pr-completion` (`plugin@marketplace`).
- Inspect installed and available plugin inventories after a clean reinstall.
- Remove a differently named marketplace if it shadows `pr-completion`.

### GitHub authentication fails

- Run `gh auth login` for the correct host.
- Ensure the authenticated account can read checks and reviews and push to the PR branch.
- Do not paste long-lived tokens into agent chat; the plugin uses your existing CLI authentication.

### Watcher or Python fails

- Confirm `python3 --version` is 3.10 or newer.
- Run from the PR repository so Git and `gh` resolve the correct remote.
- Include the plugin version, OS, skill name, and redacted logs in a [support issue](support.md).

## Related reference

- [Skills and safety](skills.md)
- [Support](support.md)
- [Release v0.3.0](https://github.com/anur4ag/pr-completion/releases/tag/v0.3.0)
