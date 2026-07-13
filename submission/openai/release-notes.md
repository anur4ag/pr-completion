# PR Completion v0.1.0

Initial public skills-only submission.

PR Completion provides four coordinated local workflows for repository-aware commits, deterministic pull-request observation, review-thread triage, and conflict resolution. The orchestrator may make ordinary in-scope edits, commits, pushes, CI repairs, and authorized thread replies while it drives a pull request to verified merge readiness.

The safety boundary is strict: the plugin never merges, enables or disables auto-merge, joins a merge queue, bypasses protections, force-pushes, or rewrites history. An externally enabled auto-merge or externally merged pull request is observed and reported only.

Reviewers can run all eight supplied cases without private repositories. The portal skill upload is reconstructed from public tag `v0.1.0` at commit `e56ef4e79f44e295cb17dc66b3b03f622c780f09` and must retain SHA-256 `1cc653d0b5b9879109c31105c98a3d211f484ad409f6b23c6336f255e525536e`.

No demo account, MCP server, app ID, domain challenge, hosted credential service, or plugin-operated telemetry is required. Live GitHub workflows use the reviewer's existing authenticated `gh` session and repository permissions.
