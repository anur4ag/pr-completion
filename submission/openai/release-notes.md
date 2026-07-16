# PR Completion v0.3.0

Autonomous landing contract under the verified **Business — Traycer** publisher identity.

## Changes

- Direct commit-skill use now continues through push, PR creation, and monitoring unless the user explicitly asks for local-only work.
- Every ready PR receives a separate exact-head landing prompt with an immediate-merge warning.
- A guarded helper rechecks readiness and head identity before requesting normal protected auto-merge or a required merge-queue entry.
- The watcher adds `awaiting_merge`, verifies that the accepted auto-merge or queue enrollment remains active for the authorized head, and keeps observing until exact-head merge or a blocker.

## Safety

Landing approval is never implicit, shared across PRs, or reused after a head change. The plugin never uses admin/protection bypass, force-push, history rewrite, direct merge APIs, or alternate merge commands. A submitted request is not reported as merged until the read-only watcher observes it.

## Upload artifact

Use `pr-completion-0.3.0-portal-plugin.zip` from the published v0.3.0 release. Do not upload the manifest-free skills-source archive.
