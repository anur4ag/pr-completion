#!/usr/bin/env python3
"""Validate release-source package identity and dual-harness version consistency.

Checks:
  - VERSION equals Claude, Codex, and marketplace plugin versions
  - release source versions are plain SemVer (no +codex.<timestamp>)
  - marketplace sources stay relative and inside the repository
  - required package files and skill tree exist
  - sibling skill references stay namespaced and resolvable without skill forks
  - no absolute personal home paths, common secret markers, or Python caches
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PLUGIN_NAME = "pr-completion"
EXPECTED_SKILLS = (
    "take-pr-to-completion",
    "commit-workspace-changes",
    "gh-review-comment-triage",
    "merge-conflict-resolution",
)

SEMVER_PLAIN_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?$"
)

CACHEBUSTER_RE = re.compile(r"\+codex\.", re.IGNORECASE)
PERSONAL_PATH_RE = re.compile(
    r"(?:/Users/|/home/|[A-Za-z]:\\Users\\|~/(?:Desktop|Documents|Downloads)/)"
)
SECRET_MARKERS = (
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
    "AWS_SECRET_ACCESS_KEY",
    "ghp_",
    "github_pat_",
)
SIBLING_REF_RE = re.compile(
    r"\$(?P<plugin>[A-Za-z0-9_-]+):(?P<skill>[A-Za-z0-9_-]+)"
)
TEXT_SUFFIXES = {
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".txt",
    ".sh",
}


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (
            (path / ".claude-plugin").is_dir() or (path / ".codex-plugin").is_dir()
        ):
            return path
    raise SystemExit(f"could not locate plugin root from {start}")


def load_json(path: Path, findings: list[str] | None = None) -> dict | None:
    """Load a JSON object.

    When ``findings`` is provided, read/parse errors are appended and ``None``
    is returned instead of aborting the whole validation run. Callers that
    already reported a missing required file can continue collecting findings.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        message = f"could not read {path}: {error}"
        if findings is not None:
            findings.append(message)
            return None
        raise SystemExit(message) from error
    except json.JSONDecodeError as error:
        message = f"invalid JSON in {path}: {error}"
        if findings is not None:
            findings.append(message)
            return None
        raise SystemExit(message) from error
    if not isinstance(payload, dict):
        message = f"{path} must contain a JSON object"
        if findings is not None:
            findings.append(message)
            return None
        raise SystemExit(message)
    return payload


def rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# Required public/release surfaces from tickets 1–4. Grouped so deletion tests
# can drop one group at a time and assert validate-release fails.
REQUIRED_FILE_GROUPS: dict[str, tuple[str, ...]] = {
    "package-identity": (
        "VERSION",
        "LICENSE",
        "CHANGELOG.md",
        ".gitignore",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        "assets/traycer-icon.png",
    ),
    "skills": (
        "skills/take-pr-to-completion/SKILL.md",
        "skills/commit-workspace-changes/SKILL.md",
        "skills/gh-review-comment-triage/SKILL.md",
        "skills/merge-conflict-resolution/SKILL.md",
        "skills/take-pr-to-completion/scripts/pr_watch.py",
    ),
    "batch-a-tooling": (
        "scripts/set-version.py",
        "scripts/validate-release.py",
        "scripts/stage-codex-dev-install.py",
        "scripts/run-package-tests.py",
        "scripts/check-merge-ready-safety.py",
    ),
    "ticket-3-ci-runners": (
        "scripts/install-smoke.py",
        "scripts/run-watcher-tests.py",
        "scripts/run-ci-validation.py",
        ".github/workflows/ci.yml",
    ),
    "ticket-4-public-docs": (
        "README.md",
        "SECURITY.md",
        ".github/workflows/pages.yml",
        "scripts/build-docs.py",
        "scripts/check-docs-links.py",
        "docs/site.json",
        "docs/index.md",
        "docs/installation.md",
        "docs/skills.md",
        "docs/support.md",
        "docs/privacy.md",
        "docs/terms.md",
    ),
    "ticket-5-release": (
        ".github/workflows/release.yml",
        "scripts/package-release.py",
    ),
    "ticket-6-openai-submission": (
        "scripts/package-openai-submission.py",
        "submission/openai/README.md",
        "submission/openai/assets/logo.png",
        "submission/openai/availability.md",
        "submission/openai/fixtures/ci-failure.json",
        "submission/openai/listing.json",
        "submission/openai/portal-checklist.md",
        "submission/openai/release-notes.md",
        "submission/openai/starter-prompts.json",
        "submission/openai/test-cases.json",
    ),
    # Verification assets: tests, offline fixtures, and public CSS must ship.
    "verification-test-modules": (
        "skills/take-pr-to-completion/tests/test_pr_watch.py",
        "scripts/tests/test_package_tooling.py",
        "scripts/tests/test_contamination.py",
        "scripts/tests/test_install_smoke.py",
        "scripts/tests/test_discovery_guards.py",
        "scripts/tests/test_openai_submission.py",
    ),
    "watcher-install-fixtures": (
        "skills/take-pr-to-completion/tests/fixtures/ready-to-merge.json",
        "skills/take-pr-to-completion/tests/fixtures/review-comment.json",
        "skills/take-pr-to-completion/tests/fixtures/pending-ci.json",
        "skills/take-pr-to-completion/tests/fixtures/blocked.json",
        "skills/take-pr-to-completion/tests/fixtures/external-auto-merge.json",
        "skills/take-pr-to-completion/tests/fixtures/conflict.json",
        "skills/take-pr-to-completion/tests/fixtures/repository-layout.json",
        "skills/take-pr-to-completion/tests/fixtures/merged.json",
        "skills/take-pr-to-completion/tests/fixtures/external-auto-merge-pending-ci.json",
        "skills/take-pr-to-completion/tests/fixtures/external-auto-merge-failing-ci.json",
    ),
    "docs-assets": (
        "docs/assets/site.css",
    ),
}


def required_files() -> tuple[str, ...]:
    files: list[str] = []
    for group in REQUIRED_FILE_GROUPS.values():
        files.extend(group)
    return tuple(files)


def check_required_files(root: Path, findings: list[str]) -> None:
    for relative in required_files():
        if not (root / relative).is_file():
            findings.append(f"missing required file: {relative}")


def check_versions(root: Path, findings: list[str]) -> str | None:
    version_path = root / "VERSION"
    if not version_path.is_file():
        findings.append("missing VERSION")
        return None
    version = version_path.read_text(encoding="utf-8").strip()
    if not SEMVER_PLAIN_RE.fullmatch(version):
        findings.append(
            f"VERSION must be plain SemVer without build metadata (got {version!r})"
        )
    if CACHEBUSTER_RE.search(version):
        findings.append(f"VERSION must not contain Codex cachebuster metadata: {version!r}")

    claude = load_json(root / ".claude-plugin" / "plugin.json", findings)
    codex = load_json(root / ".codex-plugin" / "plugin.json", findings)
    marketplace = load_json(root / ".claude-plugin" / "marketplace.json", findings)
    if claude is None or codex is None or marketplace is None:
        return version

    for label, payload in (
        (".claude-plugin/plugin.json", claude),
        (".codex-plugin/plugin.json", codex),
    ):
        value = payload.get("version")
        if value != version:
            findings.append(
                f"{label} version {value!r} does not match VERSION {version!r}"
            )
        if isinstance(value, str) and CACHEBUSTER_RE.search(value):
            findings.append(f"{label} retains cachebuster metadata: {value!r}")
        if payload.get("name") != PLUGIN_NAME:
            findings.append(f"{label} name must be {PLUGIN_NAME!r}")
        skills = payload.get("skills")
        if skills not in {"./skills/", "skills/", "./skills"}:
            findings.append(f"{label} skills path must point at ./skills/ (got {skills!r})")

    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        findings.append(".claude-plugin/marketplace.json missing plugins array")
        return version

    entry = None
    for item in plugins:
        if isinstance(item, dict) and item.get("name") == PLUGIN_NAME:
            entry = item
            break
    if entry is None:
        findings.append(
            f".claude-plugin/marketplace.json missing plugin entry {PLUGIN_NAME!r}"
        )
        return version

    market_version = entry.get("version")
    if market_version != version:
        findings.append(
            "marketplace plugin version "
            f"{market_version!r} does not match VERSION {version!r}"
        )
    if isinstance(market_version, str) and CACHEBUSTER_RE.search(market_version):
        findings.append(
            f"marketplace plugin version retains cachebuster metadata: {market_version!r}"
        )

    source = entry.get("source")
    if not isinstance(source, str) or not source.startswith("./"):
        findings.append(
            "marketplace plugin source must be a repository-relative path "
            f"starting with ./ (got {source!r})"
        )
    elif ".." in Path(source).parts:
        findings.append(f"marketplace plugin source escapes repository: {source!r}")
    else:
        resolved = (root / source).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            findings.append(f"marketplace plugin source leaves repository: {source!r}")
        if not resolved.exists():
            findings.append(f"marketplace plugin source does not exist: {source!r}")

    return version


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return None
    import struct

    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def check_publisher_metadata(root: Path, findings: list[str]) -> None:
    for relative in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
        payload = load_json(root / relative, findings)
        if payload is None:
            continue
        author = payload.get("author")
        if not isinstance(author, dict) or not str(author.get("name") or "").strip():
            findings.append(f"{relative}: author.name is required")
        elif str(author.get("name") or "").strip() != "Traycer":
            findings.append(
                f"{relative}: author.name must be 'Traycer' for the verified "
                "Business \u2014 Traycer portal identity"
            )
        repository = payload.get("repository")
        if not isinstance(repository, str) or "github.com/anur4ag/pr-completion" not in repository:
            findings.append(
                f"{relative}: repository metadata should point at "
                "https://github.com/anur4ag/pr-completion"
            )
        license_name = payload.get("license")
        if license_name != "MIT":
            findings.append(f"{relative}: license must be MIT (got {license_name!r})")

    marketplace = load_json(root / ".claude-plugin" / "marketplace.json", findings)
    if marketplace is None:
        return
    owner = marketplace.get("owner")
    if not isinstance(owner, dict) or not str(owner.get("name") or "").strip():
        findings.append(".claude-plugin/marketplace.json: owner.name is required")
    elif str(owner.get("name") or "").strip() != "Traycer":
        findings.append(
            ".claude-plugin/marketplace.json: owner.name must be 'Traycer'"
        )


def check_codex_portal_visuals(root: Path, findings: list[str]) -> None:
    """Portal requires square composerIcon and logo under the plugin root."""
    payload = load_json(root / ".codex-plugin" / "plugin.json", findings)
    if payload is None:
        return
    interface = payload.get("interface")
    if not isinstance(interface, dict):
        findings.append(".codex-plugin/plugin.json: interface object is required")
        return
    if payload.get("skills") != "./skills/":
        findings.append(
            ".codex-plugin/plugin.json: skills must be the plugin-root-relative "
            "./skills/ path"
        )
    developer = interface.get("developerName")
    if developer != "Traycer":
        findings.append(
            ".codex-plugin/plugin.json: interface.developerName must be 'Traycer'"
        )
    default_prompt = interface.get("defaultPrompt")
    if (
        not isinstance(default_prompt, list)
        or not default_prompt
        or any(not isinstance(item, str) or not item.strip() for item in default_prompt)
    ):
        findings.append(
            ".codex-plugin/plugin.json: interface.defaultPrompt must be a "
            "non-empty array of non-empty strings"
        )
    for field in ("composerIcon", "logo"):
        value = interface.get(field)
        if not isinstance(value, str) or not value.startswith("./"):
            findings.append(
                f".codex-plugin/plugin.json: interface.{field} must be a "
                "plugin-root-relative path starting with ./"
            )
            continue
        if ".." in Path(value).parts:
            findings.append(
                f".codex-plugin/plugin.json: interface.{field} escapes plugin root"
            )
            continue
        rel = value[2:] if value.startswith("./") else value
        path = root / rel
        if not path.is_file():
            findings.append(
                f".codex-plugin/plugin.json: interface.{field} missing file {value}"
            )
            continue
        dims = _png_dimensions(path)
        if dims is None:
            findings.append(
                f".codex-plugin/plugin.json: interface.{field} must be a PNG ({value})"
            )
            continue
        width, height = dims
        if width != height or width < 512 or width > 2048:
            findings.append(
                f".codex-plugin/plugin.json: interface.{field} must be square "
                f"512-2048px (got {width}x{height} for {value})"
            )


def check_skills(root: Path, findings: list[str]) -> None:
    skills_root = root / "skills"
    for skill in EXPECTED_SKILLS:
        skill_md = skills_root / skill / "SKILL.md"
        if not skill_md.is_file():
            findings.append(f"missing skill: skills/{skill}/SKILL.md")
            continue
        # Guard against accidental harness forks of the same skill.
        duplicates = list(skills_root.glob(f"**/{skill}/SKILL.md"))
        if len(duplicates) != 1:
            findings.append(
                f"skill {skill!r} must exist exactly once under skills/ "
                f"(found {len(duplicates)})"
            )

    # Cross-harness sibling references: both Claude and Codex resolve
    # $pr-completion:<skill> against the shared skills/ tree.
    skill_names = set(EXPECTED_SKILLS)
    for path in sorted(skills_root.rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8")
        for match in SIBLING_REF_RE.finditer(text):
            plugin = match.group("plugin")
            skill = match.group("skill")
            if plugin != PLUGIN_NAME:
                findings.append(
                    f"{rel(root, path)}: skill reference uses unexpected plugin "
                    f"namespace ${plugin}:{skill} (expected ${PLUGIN_NAME}:...)"
                )
            if skill not in skill_names:
                findings.append(
                    f"{rel(root, path)}: unresolved sibling skill reference "
                    f"${plugin}:{skill}"
                )


def iter_text_files(root: Path) -> list[Path]:
    skip_dirs = {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".codex-staging",
        ".cachebust",
        # Generated Pages output (gitignored); source of truth is docs/*.md.
        "_site",
    }
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES or path.name in {
            "VERSION",
            "LICENSE",
            "CHANGELOG.md",
            ".gitignore",
        }:
            files.append(path)
    return files


def check_package_hygiene(root: Path, findings: list[str]) -> None:
    for path in root.rglob("__pycache__"):
        if ".git" in path.parts:
            continue
        findings.append(f"generated Python cache present: {rel(root, path)}")
    for path in root.rglob("*.pyc"):
        if ".git" in path.parts:
            continue
        findings.append(f"compiled Python bytecode present: {rel(root, path)}")

    for path in iter_text_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if CACHEBUSTER_RE.search(text) and path.name.endswith(".json"):
            # Only manifests/catalogs are hard failures; scripts may mention the policy.
            if path.suffix == ".json":
                findings.append(
                    f"{rel(root, path)}: release JSON must not embed +codex. cachebusters"
                )
        for match in PERSONAL_PATH_RE.finditer(text):
            # Allow this validator and staging script to mention path patterns.
            if path.name in {
                "validate-release.py",
                "stage-codex-dev-install.py",
                "check-merge-ready-safety.py",
            }:
                continue
            findings.append(
                f"{rel(root, path)}: absolute personal path marker {match.group(0)!r}"
            )
        # Skip the validator's own marker table (literal patterns, not secrets).
        if path.name == "validate-release.py":
            continue
        for marker in SECRET_MARKERS:
            if marker in text:
                findings.append(f"{rel(root, path)}: possible secret marker {marker!r}")


def validate(root: Path) -> list[str]:
    findings: list[str] = []
    check_required_files(root, findings)
    check_versions(root, findings)
    check_publisher_metadata(root, findings)
    check_codex_portal_visuals(root, findings)
    check_skills(root, findings)
    check_package_hygiene(root, findings)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate dual-harness package identity, plain SemVer alignment, "
            "and release-source hygiene."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (defaults to repository containing this script)",
    )
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else plugin_root_from(Path(__file__))
    findings = validate(root)
    if findings:
        print("validate-release failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    print(f"validate-release passed for {root} at version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
