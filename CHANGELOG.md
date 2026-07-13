# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Reproducible OpenAI skills-only directory submission packaging pinned to the exact `v0.1.0` tag and published skills-source checksum.
- Complete public listing, logo, starter prompts, release notes, availability guidance, and five positive plus three negative reviewer cases.

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

[Unreleased]: https://github.com/anur4ag/pr-completion/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/anur4ag/pr-completion/releases/tag/v0.1.0
