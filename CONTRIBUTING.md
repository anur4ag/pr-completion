# Contributing

## Developer Certificate of Origin (DCO)

All **new** commits on this repository must include a DCO sign-off trailer that matches the commit **author** or **committer** identity:

```text
Signed-off-by: Your Name <you@example.com>
```

Use `git commit -s` (or an equivalent that adds a matching trailer). An unrelated signatory does not satisfy the check.

### Enforcement (active repository governance)

- Hosted GitHub Actions workflow **dco** produces the check name **`signed-off-by`**, which validates the trailer and that at least one sign-off matches the commit author or committer.
- While the active `main` ruleset configuration remains in force, that **GitHub Actions-produced** `signed-off-by` check is required for every actor (no bypass actors). Direct pushes to `main` are not an intended workflow.
- Land future work through a signed commit on a branch, open a pull request, wait for `signed-off-by` (and other required checks) to pass, then merge with a method that preserves the signed commit (for example rebase merge). Do not squash into an unsigned generated merge commit.
- This is repository-native governance, not an external DCO app. Repository owners can intentionally change settings or workflow source through a future governed change; enforcement is absolute only for the current active ruleset and Actions producer binding.

### One-time historical exception (immutable)

Two commits on the public `v0.1.1` ancestry lack DCO trailers:

| Commit | Subject |
| --- | --- |
| `a93a5d7` | Fix release workflow env var case for VERSION and TAG |
| `4af89ae` | Prepare OpenAI skills-only directory submission |

The maintainer accepted a **one-time exception** for exactly those two SHAs so the published `v0.1.1` tag remains immutable (no history rewrite, no retag, no replacement release to disguise unsigned ancestors).

This project does **not** claim that every historical commit on the release line is DCO-signed. Only future commits are required to carry matching sign-off.

## Guarded landing plugin contract

`skills/take-pr-to-completion/scripts/pr_land.py` is the sole authorized
merge-state mutation surface. It may request normal protected auto-merge or
merge-queue enrollment only after a fresh readiness check and explicit per-PR,
exact-head confirmation. Do not add alternate CLI, REST, GraphQL, alias, or
wrapper mutation paths. Admin bypasses, force-pushes, history rewrites, and
protection bypasses remain prohibited.
