# PR Completion v0.2.1

Light and dark logo refresh under the verified **Business — Traycer** publisher identity.

## Changes

- Replace the prior generic Traycer icon with a dedicated PR Completion logo.
- Add separate light- and dark-mode logo assets through `interface.logo` and `interface.logoDark`.
- Keep the light logo as the composer icon because the current manifest schema has no separate dark composer-icon field.
- Preserve deterministic minimal portal packaging below the 1 MiB upload guard.

## Safety (unchanged)

The safety boundary remains strict: the plugin never merges, enables or disables auto-merge, joins a merge queue, bypasses protections, force-pushes, or rewrites history. Terminal success is verified merge readiness only.

## Upload artifact

Use `pr-completion-0.2.1-portal-plugin.zip`. It is intentionally smaller than the full installable release ZIP while remaining deterministically derived from the same source. Do not upload the manifest-free skills-source archive.
