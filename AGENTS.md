# PR readiness regression evidence

Use the repository's canonical validation entrypoint:
`python -B scripts/run-ci-validation.py --skip-install-smoke`.
It runs the watcher test suite locally and in `.github/workflows/ci.yml`.

| Mistake class | Enforcement | Past evidence |
|---|---|---|
| Treating an optional billing failure as a required merge gate, or treating a successful empty required-check selection as missing evidence | `test_spi_pr97_required_none_does_not_inherit_optional_billing_failure` verifies required-only readiness and retains failure, conflict, review, head and all-check-policy guards | SPI PR #97 head `c19eae416aa842d35a9b66d31881ca9dfba70ded`, Actions `41873633121`; installed 0.3.0 returned pending for empty required checks and UNSTABLE |

The installed managed plugin cache is read-only. Keep source fixes and their
tests in this checkout; do not overwrite cached plugin files. Runtime rollout
must preserve the existing native GitHub protections and task authorization.
