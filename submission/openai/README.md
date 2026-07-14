# OpenAI directory submission materials (v0.1.1)

This directory holds form inputs and validation fixtures for the PR Completion OpenAI plugin-directory draft under the verified **Business — Traycer** identity. It follows the official [Submit plugins](https://learn.chatgpt.com/docs/submit-plugins) workflow.

## Portal rejection lessons (v0.1.0)

1. A skills-tree-only ZIP was rejected because it lacked a supported plugin manifest (`.codex-plugin/plugin.json`, `.agent-plugin/plugin.json`, or `.claude-plugin/plugin.json`) at the ZIP root or inside the sole top-level directory.
2. A full plugin ZIP without `interface.composerIcon` and `interface.logo` was rejected; both must reference a square image inside the package.
3. The portal identity dropdown for this account exposes **Business — Traycer** only.

## Build and validate

From a clean checkout (working tree before tag, or after `v0.1.1` exists):

```bash
# Pre-tag / local portal ZIP from the current tree (must match package-release bytes)
python3 -B scripts/package-openai-submission.py --from-working-tree

# After the public tag exists
python3 -B scripts/package-openai-submission.py --check-urls
```

Outputs under ignored `submission-out/`:

- `pr-completion-0.1.1-portal-plugin.zip` — **upload this** on the portal Skills tab. It contains the public release manifest, skills, and square visual assets under one top-level plugin directory. When pins are set it matches the published `pr-completion-0.1.1-plugin.zip` checksum.
- `pr-completion-0.1.1-openai-materials.zip` — auditable copy of form inputs and fixtures; do not upload this as the skill bundle.
- `SHA256SUMS.txt` and `validation-report.json` — integrity and validation evidence.

## Portal mapping

| Portal tab | Local source |
|---|---|
| Create plugin | Choose **Skills only** |
| Developer identity | **Business — Traycer** |
| Info | `listing.json`, `assets/logo.png` |
| Skills | Generated `pr-completion-0.1.1-portal-plugin.zip` |
| Prompts | `starter-prompts.json` |
| Testing | `test-cases.json` (five positive, three negative) |
| Global | `availability.md` |
| Submit | `release-notes.md`, then complete accurate attestations |

`portal-checklist.md` separates locally proven facts from organization/account steps that remain user-controlled in the authenticated portal.
