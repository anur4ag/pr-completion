# PR Completion 0.4.0 — prepared, unpublished

Invoking completion authorizes the workflow through verified merge, including normal repair rounds and head changes. Narrower requests remain narrower.

- One observation workflow uses the bundled watcher inside the runtime monitor, choosing finite or persistent execution to match notification support.
- CodeRabbit and Codex participation is distinct from effective approval. Automatic incremental review is observed after pushes; a new head alone does not request another pass.
- Review threads, review summaries, and PR comments are triaged together. Durable feedback receipts survive restarts and expose edited findings.
- After findings are handled, request `@coderabbitai approve` if effective approval is missing. GitHub's eligible-approver and branch protections still apply.
- The landing helper rechecks readiness and uses GitHub CLI's head race guard. A stale observation returns to watching under the same task authorization.
- Independent PRs progress independently; enrollment never hides failing checks or new feedback. Only an observed GitHub merge is completion.
- A standalone commit request returns after local validated commits.

Breaking CLI changes: `--confirm` and `--policy-digest` are removed; `--dry-run` is optional. Required reviewers now mean completed participation, not an approval on every head.

Build `pr-completion-0.4.0-portal-plugin.zip` from the working tree for validation. Publish and pin the final release before distribution. The immutable 0.3.0 pins remain historical verification inputs until then.
