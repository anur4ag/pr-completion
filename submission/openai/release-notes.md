# PR Completion v0.1.1

Portal-ready patch under the verified **Business — Traycer** publisher identity.

## Changes

- Ship the canonical 1024×1024 Traycer square icon as a public plugin asset.
- Add required Codex portal visual fields `interface.composerIcon` and `interface.logo` with plugin-root-relative `./assets/traycer-icon.png` paths.
- Align public manifests and publisher-facing listing materials with Traycer while keeping GitHub repository ownership (`anur4ag/pr-completion`) and MIT copyright attribution accurate.
- Fix the OpenAI portal upload package so the Skills-tab ZIP contains the public release manifest, skills, and referenced square assets under one top-level plugin directory (not a skills-tree-only archive and not a private overlay).

## Safety (unchanged)

The safety boundary remains strict: the plugin never merges, enables or disables auto-merge, joins a merge queue, bypasses protections, force-pushes, or rewrites history. Terminal success is verified merge readiness only.

## Upload artifact

Use the generated portal plugin ZIP (byte-identical to the public `pr-completion-0.1.1-plugin.zip` release asset). Do not upload a skills-only archive; the authenticated portal rejects packages missing a supported plugin manifest.
