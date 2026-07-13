# Privacy

**Last updated:** 2026-07-14

This privacy statement describes how the **pr-completion** plugin handles data.

## Summary

- **Plugin code and helpers** (skills, local scripts such as the PR watcher) execute on your machine inside Claude Code or Codex.
- **Claude Code, Codex, Git, `gh`, and other tools you invoke** may transmit prompts, repository metadata, or network requests to **their configured providers** under those products' own policies.
- The publisher of pr-completion operates **no backend**, **no analytics service**, and **no telemetry collector** for this plugin.
- The plugin does **not** proxy, store, or broker your credentials.

## What the plugin is

`pr-completion` is a package of skill instructions and local helper scripts.
It does not introduce a hosted application, MCP authentication service, remote control plane, or publisher-operated data collection endpoint.

## What “local” means (and does not mean)

| Component | Where it runs | Who may receive data |
| --- | --- | --- |
| Skill instructions and plugin helpers | Your machine | Not sent to a pr-completion publisher service (none exists) |
| Claude Code / Codex harness | Your machine, talking to the harness provider | Anthropic, OpenAI, or your configured model/API endpoints per harness settings |
| Git remotes you push to | Your machine + remote host | Your Git host |
| GitHub CLI (`gh`) | Your machine + GitHub API | GitHub, Inc., using your credentials |
| Project build/test tools | Your machine (and any services those tools call) | Those tools' operators |

Saying the plugin runs locally does **not** mean an entire agent session is offline or that no third party ever sees repository or chat context.

## Data the plugin may touch

Depending on how you invoke the skills, local processes may read or write:

| Category | Examples | Typical destination |
| --- | --- | --- |
| Local workspace | Source files, diffs, test output, Git state | Your machine; Git remotes you push to |
| GitHub metadata | PR title, checks, review threads, mergeability | Requested through your `gh` session from GitHub |
| Tool configuration | Paths, CLI flags, optional `.pr-completion.json` | Your machine |
| Chat / harness context | Prompts and tool results inside Claude Code or Codex | Governed by those products' policies |

The plugin authors do not receive these materials through a plugin-operated channel.

## GitHub and other third parties

When skills invoke `gh`, Git, or project toolchains, those tools communicate with services you choose (for example `github.com`) using **your** credentials and configuration.

- **GitHub** is operated by GitHub, Inc., not by the pr-completion publisher.
- Claude Code and Codex are operated by their respective providers.
- This privacy statement does not replace those providers' policies.

## Telemetry

The plugin does not:

- phone home to a publisher-controlled endpoint;
- embed product analytics SDKs;
- upload crash dumps to the publisher;
- register a plugin-specific tracking identifier.

If your harness, OS, CI system, or Git host logs activity, that logging is outside this plugin's control.

## Credentials

Skills expect you to authenticate external tools yourself (for example `gh auth login`).
The plugin must not ask you to paste long-lived tokens into chat when a standard CLI auth flow exists.
Do not commit secrets; skill guidance treats credentials and private keys as out-of-scope for commits.

## Children

The plugin is a developer tool and is not directed at children.

## Changes

Material changes to this statement will be reflected in the repository documentation for the affected version.
The planned public URL for this page is `https://anur4ag.github.io/pr-completion/privacy/` once GitHub Pages hosting is enabled and verified.

## Contact

Privacy questions can use the same channels as [Support](support.md).
Security-sensitive issues should follow [SECURITY.md](../SECURITY.md).
