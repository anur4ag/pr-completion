# Skills and safety

Four skills share one implementation tree across Claude Code and Codex. Use `$pr-completion:take-pr-to-completion` for the full lifecycle, `commit-workspace-changes` for local commits, `gh-review-comment-triage` for a review round, and `merge-conflict-resolution` for conflicted Git operations.

## Completion contract

Invocation authorizes in-scope preparation, reviews, repairs and protected merge. Authorization persists through routine head changes. An explicit local-only or stop-at-ready request narrows that scope. The lifecycle owner retains responsibility when delegated workers have narrower runtime permissions.

The commit helper returns validated commits without recursively creating another completion workflow. Triage verifies current code, repairs related instances, self-reviews a complete round, and returns one coherent batch. Required checks, hooks, DCO, semantic conflict resolution and unrelated user work remain protected.

## Monitor integration

Discover and reuse the runtime monitor for the PR set. Run the shipped watcher as its foreground command. Completion-only monitors and background tasks use `until-actionable`: consume the result, handle it, and rearm. Use persistent `watch` only when the monitor delivers changed output while running. Reuse monitor identity when supported and the same durable paths in either mode; keep one active observer. Never wait for persistent `watch` to exit before handling readiness.

```bash
python3 <skill-directory>/scripts/pr_watch.py --target <repo>=<pr-url> --mode until-actionable --cursor <state-directory>/cursor.json --observations-file <state-directory>/events.ndjson --output <state-directory>/latest.json
```

`watch` emits NDJSON on meaningful changes and remains alive through actionable, ready and awaiting-merge states. It finishes when all PRs merge or the remaining targets are blocked. `latest.json` holds the last emitted snapshot, written atomically; observations append. `until-actionable` emits one result and replays unfinished actions on restart. `once` is read-only inspection of GitHub state. Local state writes never imply that findings are resolved on GitHub.

Each PR retains its own state. Read ready targets even when other targets fail. Land upstream dependencies first, then repin dependents to the actual merged commit and resume their checks/reviews.

## Reviews and approval

The default participants are `coderabbitai` and `chatgpt-codex-connector`. `requiredReviewers` / `--reviewer` specify required review participation, not a requirement for each participant to vote APPROVED on every SHA. Completed reviews, Codex positive reactions and active bot checks are distinct signals. GitHub's effective approval gate must also be satisfied.

Observe CodeRabbit's automatic incremental review after each push. Do not routinely issue `@coderabbitai review`, a full review, or a Codex request because the head changed. Investigate missing coverage or stalled activity before a justified recovery request. Existing approvals can remain effective under repository policy.

Review bodies and top-level comments may contain material findings. The watcher emits unacknowledged bodies as `review_feedback`. The agent verifies their substance, then records the exact returned token using the same target/cursor:

```bash
python3 <skill-directory>/scripts/pr_watch.py --target <repo>=<pr-url> --cursor <state-directory>/cursor.json --ack-feedback <id:digest> --verdict addressed --evidence <pushed-fix-and-validation>
```

Use `non-actionable` with a concrete explanation for clean summaries, stale findings or false positives. Acknowledgements live beside the cursor in `cursor.feedback.json`; keep both across restarts and pass the same cursor to the lander. They survive head changes but do not cover new/edited bodies. Never acknowledge a material issue before it is addressed.

When `approval_needed` is emitted, all material feedback must already be handled and automatic review settled. Post `@coderabbitai approve` if its approval is still needed, then verify the result. That command can also resolve CodeRabbit threads; it must not replace triage. The watcher observes an outstanding request instead of immediately suggesting another. If bot approval is disabled or cannot satisfy an eligible human/CODEOWNER rule, obtain that required review. Do not dismiss a blocking review to manufacture readiness.

[CodeRabbit commands](https://docs.coderabbit.ai/reference/review-commands) and [automatic review](https://docs.coderabbit.ai/configuration/auto-review) describe the provider behavior; repository configuration determines which automatic triggers and approvals are available.

## Protected landing

```bash
python3 <skill-directory>/scripts/pr_land.py --repo <repo> --pr <pr-url> --head <observed-head> --mode auto --method <allowed-method> --cursor <state-directory>/cursor.json
```

Use `--mode queue` without a method for a required merge queue. Preserve watcher config/CLI policy overrides. `--dry-run` is optional and does not request user confirmation. Offline fixtures require `--dry-run` and can never authorize mutation.

The helper collects fresh evidence and validates readiness, exact head, queue policy and merge-method allowance immediately before GitHub's protected command. It retains `--match-head-commit`. A mismatch returns to observation under the same authorization. No admin bypass, force-push, history rewrite or alternate merge API is allowed. Keep monitoring after enrollment; only GitHub's merged state is success, and report the actual merge commit.

[GitHub CLI merge](https://cli.github.com/manual/gh_pr_merge), [checks](https://cli.github.com/manual/gh_pr_checks), and [API](https://cli.github.com/manual/gh_api) provide the native operations. Repository metadata is cached within a watcher process and recollected by the fresh landing check. Review connections use independent GraphQL cursors in a combined query; malformed/incomplete pages and head races fail closed.

## States and recovery

| State/action | Response |
| --- | --- |
| `pending` | Observe automatic reviews, checks and required approvers. |
| `actionable` | Dispatch reported repairs and feedback; do not duplicate an in-flight repair. |
| `ready` | Land that target under task authorization. |
| `awaiting_merge` | Observe enrollment and keep repairing any newly failed gates. |
| `landing_enrollment_missing` / `landing_enrollment_rejected` | Reconcile current GitHub state and gates before retrying. |
| `diagnose_wait` | Inspect a gate unchanged for 15 minutes; respect provider cooldowns and do not blindly retrigger reviews. |
| `timeout` | Consume the retained last state and resume the same monitor if progress remains possible. |
| `blocked` | Recover or report a concrete external dependency after bounded attempts. |
| `merged` | Record the actual merged commit and stop obsolete observation. |

Process exit codes: successful observation `0`, blocked `20`, timeout `30`. The JSON state remains authoritative. A persistent permission/runtime denial is not transient; stop identical retries and retain the evidence needed to recover.

## Configuration and migration from 0.3.0

CLI values override `.pr-completion.json`. Run `--print-config` to inspect resolved values.

| Key | CLI | Default |
| --- | --- | --- |
| `requiredReviewers` | repeated `--reviewer` | CodeRabbit and Codex review participants |
| `checkPolicy` | `--check-policy all\|required` | `all`; use `required` when repository policy permits |
| `intervalSeconds` | `--interval` | 30 seconds; unchanged observations back off |
| `maxIntervalSeconds` | `--max-interval` | 120 seconds |
| `timeoutSeconds` | `--timeout` | 3600 seconds; `0` explicitly disables |
| `cursorPath` | `--cursor` | Git-dir/platform state path |
| `observationsPath` | `--observations-file` | None |
| — | `--output` | Optional atomic latest snapshot |
| `strictChangesRequested` | `--strict-changes-requested` | `false`; true exposes every standing changes-requested vote as actionable |

The legacy single-PR `--await-merge`, `--await-merge-mode`, and `--await-merge-since` flags remain for enrollment reconciliation. Their head is an observation guard, not a user authorization boundary. They never revoke task authorization after a push. Continuous monitoring normally needs no phase switch.

With `checkPolicy: required`, GitHub CLI selects required checks; a valid empty list means no required checks. Non-required failures do not block this policy. `HAS_HOOKS` remains subject to server hooks, and merge-queue admission remains subject to GitHub protections. Unknown or malformed observations cannot establish readiness.

Version 0.4.0 removes `--confirm` and `--policy-digest`; callers invoke the helper directly or use `--dry-run`. The `auto_merge` terminal state is replaced by ongoing `awaiting_merge` observation with repairs preserved. Existing cursor files remain readable, but no longer suppress unfinished actions. Do not use old approval-only semantics for `requiredReviewers`. Upgrade both harness installations to the same release; never overwrite a published version's content in place.

A newly pushed head waits for its automatic CodeRabbit review or completed status to appear, closing the registration gap without posting a manual review request. Retained approvals remain usable when GitHub permits them. The cursor preserves the age of unchanged waits across restarts.
