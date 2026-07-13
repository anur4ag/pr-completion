# Security Policy

## Supported versions

Security fixes are applied to the latest published SemVer release of `pr-completion` and to the current `main` branch used for the next release.

Older tags receive fixes only when a release note explicitly backports them.

## What this project is

`pr-completion` is a **local skills plugin**. It:

- ships skill instructions and helpers that execute on the user's machine under Claude Code or Codex;
- uses tools the user already configures (Git, `gh`, language toolchains);
- does **not** operate a backend service, telemetry collector, or credential proxy.

Harnesses and third-party tools may still send data to their own providers; see [docs/privacy.md](docs/privacy.md).

## Reporting a vulnerability

**Do not** open a public GitHub Issue for security-sensitive reports.

### Private channel (publication-time)

The only private reporting channel is **GitHub private vulnerability reporting** on the public repository:

`https://github.com/anur4ag/pr-completion/security/advisories/new`

**Availability gate.** That URL is not usable until:

1. the public repository `anur4ag/pr-completion` exists; and
2. private vulnerability reporting is enabled on that repository.

Both steps are part of **ticket 5** (public v0 release verification). Until then, treat private reporting as **planned, not live**. There is no alternate publisher profile-email fallback.

When the channel is live, please include:

- a description of the issue and impact;
- reproduction steps or a proof of concept;
- affected version or commit when known;
- whether a public issue or discussion already exists.

## Scope examples

In scope:

- instruction or script paths that could cause unintended destructive Git operations beyond the documented skill authority;
- leakage of secrets from the working tree into commits, logs, or reports due to plugin guidance or scripts;
- install or packaging flaws that cause untrusted code execution outside the installed plugin tree.

Out of scope:

- vulnerabilities in GitHub, Claude Code, Codex, Python, or other third-party tools the user installs independently;
- misuse after a user deliberately grants broader tool permissions than the skill contracts describe;
- social-engineering the user into pasting secrets into chat.

## Response expectations

After private reporting is enabled and a report is received, you should get an acknowledgement.
Fix timelines depend on severity and complexity.
Public disclosure is coordinated after a fix is available when possible.

## Non-security support

For ordinary bugs, install problems, and feature requests, use the public issue tracker once the repository is published.
See [docs/support.md](docs/support.md).
