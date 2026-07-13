# OpenAI skills-only submission materials

This directory is the reproducible source for the PR Completion `v0.1.0` OpenAI plugin-directory draft. It follows the current official [Submit plugins](https://learn.chatgpt.com/docs/submit-plugins) workflow.

## Build and validate

From a clone that contains the public `v0.1.0` tag:

```bash
python3 -B scripts/package-openai-submission.py --check-urls
```

The command refuses any tag/commit drift, any change to the exact tagged skill allowlist, any mismatch from the published skills-source checksum, any extra local submission file, and common credential, personal-path, cache, local-only, or cachebuster contamination. It extracts blobs directly from the tagged Git object database; working-tree skill edits cannot enter the upload.

Outputs under ignored `submission-out/`:

- `pr-completion-0.1.0-openai-skills.zip` — upload this on the portal **Skills** tab. Its bytes and checksum match the published `v0.1.0` skills-source archive.
- `pr-completion-0.1.0-openai-materials.zip` — auditable copy of the form inputs and test fixture; do not upload this as the skill bundle.
- `SHA256SUMS.txt` and `validation-report.json` — integrity and validation evidence.

## Portal mapping

| Portal tab | Local source |
|---|---|
| Create plugin | Choose **Skills only** |
| Info | `listing.json`, `assets/logo.png` |
| Skills | Generated `pr-completion-0.1.0-openai-skills.zip` |
| Prompts | `starter-prompts.json` |
| Testing | `test-cases.json` (five positive, three negative) |
| Global | `availability.md` |
| Submit | `release-notes.md`, then complete accurate attestations |

`portal-checklist.md` separates locally proven facts from organization/account facts that must be observed in an already-authenticated portal session.
