# PR Completion v0.1.2

Portal-ready patch under the verified **Business — Traycer** publisher identity.

## Changes

- Ship the canonical 1024×1024 Traycer square icon as a public plugin asset.
- Add required Codex portal visual fields `interface.composerIcon` and `interface.logo` with plugin-root-relative `./assets/traycer-icon.png` paths.
- Align public manifests and publisher-facing listing materials with Traycer while keeping GitHub repository ownership (`anur4ag/pr-completion`) and MIT copyright attribution accurate.
- Change `interface.defaultPrompt` to the current documented array shape.
- Replace the full-repository portal upload with a deterministic minimal ZIP containing only the Codex manifest, runtime skill files, and referenced square assets.
- Exclude tests, CI workflows, docs, release scripts, submission materials, and alternate-harness manifests from the portal upload; enforce a conservative 1 MiB size guard.

## Safety (unchanged)

The safety boundary remains strict: the plugin never merges, enables or disables auto-merge, joins a merge queue, bypasses protections, force-pushes, or rewrites history. Terminal success is verified merge readiness only.

## Upload artifact

Use `pr-completion-0.1.2-portal-plugin.zip`. It is intentionally smaller than the full installable release ZIP while remaining deterministically derived from the same source. Do not upload the manifest-free skills-source archive.
