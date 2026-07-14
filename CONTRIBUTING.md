# Contributing

## Developer Certificate of Origin (DCO)

All **new** commits on this repository must include a DCO sign-off trailer that matches the commit **author** or **committer** identity:

```text
Signed-off-by: Your Name <you@example.com>
```

Use `git commit -s` (or an equivalent that adds a matching trailer). An unrelated signatory does not satisfy the check.

### Enforcement (absolute for future work)

- Hosted workflow **dco** / check name **`signed-off-by`** validates the trailer and identity.
- The active `main` branch ruleset requires that status check for changes to land on `main`.
- There is **no** administrator bypass for this requirement. Direct pushes to `main` that lack a green `signed-off-by` check are not an intended workflow.
- Land future work through a signed commit on a branch, open a pull request, wait for `signed-off-by` (and other required checks) to pass, then merge.

### One-time historical exception (immutable)

Two commits on the public `v0.1.1` ancestry lack DCO trailers:

| Commit | Subject |
| --- | --- |
| `a93a5d7` | Fix release workflow env var case for VERSION and TAG |
| `4af89ae` | Prepare OpenAI skills-only directory submission |

The maintainer accepted a **one-time exception** for exactly those two SHAs so the published `v0.1.1` tag remains immutable (no history rewrite, no retag, no replacement release to disguise unsigned ancestors).

This project does **not** claim that every historical commit on the release line is DCO-signed. Only future commits are required to carry matching sign-off.

## Merge-ready-only plugin contract

Do not add instructions or helpers that merge PRs, enable auto-merge, join merge queues, force-push, or bypass branch protections as part of the public completion workflow.
