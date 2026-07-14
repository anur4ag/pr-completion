# Contributing

## Developer Certificate of Origin (DCO)

All **new** commits on this repository must include a DCO sign-off trailer:

```text
Signed-off-by: Your Name <you@example.com>
```

Use `git commit -s` (or an equivalent that adds the trailer).

Hosted CI enforces this for pull requests and for new commits on `main`.

### One-time historical exception (immutable)

Two commits on the public `v0.1.1` ancestry lack DCO trailers:

| Commit | Subject |
| --- | --- |
| `a93a5d7` | Fix release workflow env var case for VERSION and TAG |
| `4af89ae` | Prepare OpenAI skills-only directory submission |

The maintainer accepted a **one-time exception** for exactly those two SHAs so the published `v0.1.1` tag remains immutable (no history rewrite, no retag, no replacement release to disguise unsigned ancestors).

This project does **not** claim that every historical commit on the release line is DCO-signed. Only future commits are required to carry sign-off.

## Merge-ready-only plugin contract

Do not add instructions or helpers that merge PRs, enable auto-merge, join merge queues, force-push, or bypass branch protections as part of the public completion workflow.
