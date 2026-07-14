# OpenAI directory submission materials (v0.1.2)

This directory holds form inputs and validation fixtures for the PR Completion OpenAI plugin-directory draft under the verified **Business — Traycer** identity. It follows the official [Submit plugins](https://learn.chatgpt.com/docs/submit-plugins) workflow.

## Portal rejection lessons (v0.1.0)

1. A skills-tree-only ZIP was rejected because it lacked a supported plugin manifest (`.codex-plugin/plugin.json`, `.agent-plugin/plugin.json`, or `.claude-plugin/plugin.json`) at the ZIP root or inside the sole top-level directory.
2. A full plugin ZIP without `interface.composerIcon` and `interface.logo` was rejected; both must reference a square image inside the package.
3. The corrected full-release ZIP passed local manifest/icon checks but failed
   during portal upload with a generic server-side error. It contained 93 files,
   duplicated the 452 KB logo, and was slightly larger than 1 MiB.
4. The portal identity dropdown for this account exposes **Business — Traycer** only.

## Build and validate

From a clean checkout before the `v0.1.2` tag:

```bash
# Pre-tag / local portal ZIP from the current tree
python3 -B scripts/package-openai-submission.py --from-working-tree
```

Outputs under ignored `submission-out/`:

- `pr-completion-0.1.2-portal-plugin.zip` — **upload this** on the portal Skills tab. It contains only `.codex-plugin/plugin.json`, runtime skill files, and referenced square visual assets under one top-level directory. Tests, fixtures, CI, docs, release scripts, submission materials, and alternate-harness manifests are excluded.
- `pr-completion-0.1.2-openai-materials.zip` — auditable copy of form inputs and fixtures; do not upload this as the skill bundle.
- `SHA256SUMS.txt` and `validation-report.json` — integrity and validation evidence.

## Portal mapping

| Portal tab | Local source |
|---|---|
| Create plugin | Choose **Skills only** |
| Developer identity | **Business — Traycer** |
| Info | `listing.json`, `assets/logo.png` |
| Skills | Generated `pr-completion-0.1.2-portal-plugin.zip` |
| Prompts | `starter-prompts.json` |
| Testing | `test-cases.json` (five positive, three negative) |
| Global | `availability.md` |
| Submit | `release-notes.md`, then complete accurate attestations |

`portal-checklist.md` separates locally proven facts from organization/account steps that remain user-controlled in the authenticated portal.
