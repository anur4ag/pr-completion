# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- OpenAI packager integrity is two independent gates (not an either/or pin):
  - Immutable-tag reconstruction enforces the portable member/content fingerprint (`RELEASE_PLUGIN_CONTENT_SHA256`) so local and multi-OS CI reconstruction does not depend on Ubuntu ZIP container bytes.
  - Hosted `release-integrity` downloads the immutable published `v0.1.1` plugin ZIP and `SHA256SUMS.txt` and requires exact published ZIP bytes (`RELEASE_PLUGIN_SHA256`). Content fingerprint alone cannot satisfy that job.
  - `--from-working-tree` compares only against contemporaneous `package-release` output (no published ZIP pin).
- Listing `source.commit` must equal pinned `RELEASE_COMMIT` exactly when that pin is set (empty and wrong values fail).
- Hosted CI fetches full history and tags so immutable `v0.1.1` reconstruction runs deterministically (no silent skip).

### Documentation

- Recorded a one-time DCO exception for exactly two immutable ancestors (`a93a5d7`, `4af89ae`) on the `v0.1.1` history.
- Future commits require `Signed-off-by` matching author or committer. While the active `main` ruleset remains configured, the GitHub Actions-produced `signed-off-by` check is required for every actor (no bypass). Land changes via signed pull requests that preserve signed commits. No retag or history rewrite.

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

[Unreleased]: https://github.com/anur4ag/pr-completion/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.1
[0.1.0]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.0
