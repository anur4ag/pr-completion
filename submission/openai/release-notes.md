# PR Completion v0.2.0

Autonomous watcher relaunch release under the verified **Business — Traycer** publisher identity.

## Changes

- Add a durable per-PR cursor that suppresses identical actionable observations across watcher relaunches.
- Add an append-only NDJSON observation trail so agents can recover emitted state after harness session recycling.
- Treat a thread-free stale `CHANGES_REQUESTED` decision as a pending bot re-review while current-head checks are still running.
- Add `--strict-changes-requested` for callers that need the previous always-actionable review classification.
- Document the canonical autonomous background relaunch loop and stable cursor/output paths.

## Safety (unchanged)

The safety boundary remains strict: the plugin never merges, enables or disables auto-merge, joins a merge queue, bypasses protections, force-pushes, or rewrites history. Terminal success is verified merge readiness only.

## Upload artifact

Use `pr-completion-0.2.0-portal-plugin.zip`. It is intentionally smaller than the full installable release ZIP while remaining deterministically derived from the same source. Do not upload the manifest-free skills-source archive.
