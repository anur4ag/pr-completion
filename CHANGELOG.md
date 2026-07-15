# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.1] - 2026-07-15

### Added

- Added a dedicated dark-mode PR Completion logo through the current Codex `interface.logoDark` manifest field.

### Changed

- Replaced the generic Traycer icon with dedicated, optimized 1024×1024 PR Completion light and dark artwork.
- Kept the light artwork as `interface.composerIcon` and `interface.logo`; Codex selects the dark artwork through `interface.logoDark` where supported.
- Extended release validation and minimal portal packaging to require, validate, and ship both logo variants while preserving the 1 MiB upload guard.

### Release metadata

- Pinned the immutable v0.2.0 tag commit, installable ZIP, portal ZIP, and portable content fingerprint after public release publication.
- Marked the v0.2.0 Pages release link public and advanced hosted immutable-tag validation to v0.2.0.

### Safety

- Merge-ready-only authority is unchanged.

## [0.2.0] - 2026-07-15

### Added

- Added a durable per-target watcher cursor (`cursorPath` / `--cursor`) with an outside-worktree git-dir default, platform-state fallback, atomic writes, and cross-invocation actionable-observation deduplication.
- Added an append-only NDJSON observations trail (`observationsPath` / `--observations-file`) for recovery after harness session recycling.
- Added `strictChangesRequested` / `--strict-changes-requested` to opt back into always-actionable review decisions.

### Changed

- `until-actionable` now emits exactly one new actionable or terminal observation on exit and keeps polling when the durable cursor already records an identical actionable observation.
- A standing `CHANGES_REQUESTED` decision with no unresolved review threads is treated as a pending review rerun while current-head checks are still pending.

### Documentation

- Documented the canonical background relaunch loop, stable cursor reuse, durable output recovery, and stale bot review behavior.

### Safety

- Merge-ready-only authority is unchanged.

## [0.1.2] - 2026-07-14

Portal upload compatibility patch.

### Fixed

- Changed Codex `interface.defaultPrompt` from a string to the current documented non-empty array shape.
- Replaced the 93-member full-release portal upload with a deterministic minimal archive containing only `.codex-plugin/plugin.json`, seven runtime skill files, and the referenced square icon.
- Excluded tests, fixtures, workflows, docs, release scripts, submission materials, and alternate-harness manifests from the portal upload.
- Added a conservative 1 MiB upload-size guard and schema/allowlist regressions for the exact rejected package shape.
- OpenAI packager integrity is two independent gates (not an either/or pin):
  - Immutable-tag reconstruction enforces the portable member/content fingerprint (`RELEASE_PLUGIN_CONTENT_SHA256`) so local and multi-OS CI reconstruction does not depend on Ubuntu ZIP container bytes.
  - Hosted `release-integrity` downloads immutable published assets and independently checks the full installable ZIP (`RELEASE_INSTALLABLE_SHA256`) and the portal-upload ZIP (`RELEASE_PORTAL_SHA256`). Content fingerprint alone cannot satisfy either byte gate.
  - `--from-working-tree` compares only against contemporaneous `package-release` output (no published ZIP pin).
- Listing `source.commit` must equal pinned `RELEASE_COMMIT` exactly when that pin is set (empty and wrong values fail).
- Hosted CI fetches full history and tags so immutable current-release reconstruction runs deterministically (no silent skip).

### Documentation

- Recorded a one-time DCO exception for exactly two immutable ancestors (`a93a5d7`, `4af89ae`) on the `v0.1.1` history.
- Future commits require `Signed-off-by` matching author or committer. While the active `main` ruleset remains configured, the GitHub Actions-produced `signed-off-by` check is required for every actor (no bypass). Land changes via signed pull requests that preserve signed commits. No retag or history rewrite.

### Safety

- Merge-ready-only authority is unchanged.

## [0.1.1] - 2026-07-14

Portal-ready patch for the verified **Business — Traycer** publisher identity.

### Added

- Canonical 1024×1024 Traycer square icon at `assets/traycer-icon.png`.
- Codex portal visual fields `interface.composerIcon` and `interface.logo` (plugin-root-relative `./assets/traycer-icon.png`).
- Portal-compliant OpenAI upload packaging: public plugin ZIP with manifest, skills, and square assets under one top-level directory (not skills-only; not a private overlay).
- Regression coverage for missing manifest, missing image refs, non-square/out-of-root assets, and deterministic reconstruction.

### Changed

- Publisher-facing manifests, listing materials, and docs use Traycer / Business — Traycer while preserving GitHub repository ownership and MIT copyright facts.
- OpenAI submission materials and pins target `v0.1.1`.

### Safety

- Merge-ready-only authority is unchanged.

## [0.1.0] - 2026-07-14

Initial public dual-harness release.

### Added

- Shared Claude Code and Codex manifests with one canonical `VERSION` (`0.1.0`).
- Four skills: `take-pr-to-completion`, `commit-workspace-changes`, `gh-review-comment-triage`, `merge-conflict-resolution`.
- Cross-platform CI on macOS, Linux, and Windows (package suite + isolated install smoke).
- GitHub Pages docs (install, skills/safety, support, privacy, terms).
- Deterministic release packaging (`plugin` ZIP, skills-source ZIP, SHA-256 checksums) via tag-only `release.yml`.
- MIT license, security policy, and private vulnerability reporting on the public repository.

### Safety

- Terminal success is verified merge readiness only.
- The public workflow never merges, enables auto-merge, joins a merge queue, force-pushes, or bypasses branch protections.

[Unreleased]: https://github.com/anur4ag/pr-completion/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/anur4ag/pr-completion/releases/tag/v0.2.1
[0.2.0]: https://github.com/anur4ag/pr-completion/releases/tag/v0.2.0
[0.1.2]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.2
[0.1.1]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.1
[0.1.0]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.0
