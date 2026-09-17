# PR Completion 0.4.0 - published

Invoking completion authorizes the workflow through verified merge, including normal repair rounds and head changes. Narrower requests remain narrower.

- One observation workflow uses the bundled watcher inside the runtime monitor, choosing finite or persistent execution to match notification support.
- CodeRabbit and Codex participation is distinct from effective approval. Automatic incremental review is observed after pushes; a new head alone does not request another pass.
- Review threads, review summaries, and PR comments are triaged together. Durable feedback receipts survive restarts and expose edited findings.
- After findings are handled, request `@coderabbitai approve` if effective approval is missing. GitHub's eligible-approver and branch protections still apply.
- The landing helper rechecks readiness and uses GitHub CLI's head race guard. A stale observation returns to watching under the same task authorization.
- A behind base waits while checks or automatic reviews are pending, then becomes actionable; failures, conflicts, review findings, and stalled waits still surface.
- Independent PRs progress independently; enrollment never hides failing checks or new feedback. Only an observed GitHub merge is completion.
- A standalone commit request returns after local validated commits.

Breaking CLI changes: `--confirm` and `--policy-digest` are removed; `--dry-run` is optional. Required reviewers now mean completed participation, not an approval on every head.

Published as `v0.4.0` at commit `3c73522adc9d48667055278d350c0e76e9b45058`.
Use the published `pr-completion-0.4.0-portal-plugin.zip` for directory submission.
Release checksums and the portable content fingerprint are pinned in the packaging script.
