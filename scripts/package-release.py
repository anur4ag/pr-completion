#!/usr/bin/env python3
"""Build deterministic release ZIPs and SHA-256 checksums from clean source.

Produces:

  - pr-completion-<version>-plugin.zip
      Full installable plugin tree (manifests, skills, scripts, docs source,
      workflows, license, README, VERSION).
  - pr-completion-<version>-skills-source.zip
      Clean tagged skills/ tree only. This is the skills source archive used
      later for OpenAI packaging (ticket 6 builds the portal submission
      package from this class of allowlisted source, not a hand-edited tree).
  - SHA256SUMS.txt

Determinism guarantees (same source -> same bytes):

  - file list from git ls-files when available, else a fixed ignore walk
  - sorted member order
  - fixed ZIP timestamps (1980-01-01)
  - fixed external_attr (mode bits only)
  - DEFLATE compresslevel=9

Never packages cachebusted staging trees, generated docs/_site, Python
caches, or dirty untracked release-out artifacts.

Usage:

  python3 -B scripts/package-release.py
  python3 -B scripts/package-release.py --out-dir release-out
  python3 -B scripts/package-release.py --root /path/to/checkout
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path


PLUGIN_NAME = "pr-completion"
SEMVER_PLAIN_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?$"
)
CACHEBUSTER_RE = re.compile(r"\+codex\.", re.IGNORECASE)

# Fixed timestamp for reproducible ZIP members (ZIP epoch-compatible).
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

SKIP_DIR_NAMES = frozenset(
    {
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
        "release-out",
        "submission-out",
        "dist",
        "build",
        "_site",
    }
)

SKIP_FILE_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".zip",
    ".tmp",
    ".log",
    ".DS_Store",
)

# Paths never shipped even if present and tracked by mistake.
SKIP_RELATIVE_PREFIXES = (
    "docs/_site/",
    "release-out/",
    "submission-out/",
    ".codex-staging/",
    ".cachebust/",
)


class PackageError(Exception):
    """Release packaging failure."""


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (
            (path / ".claude-plugin").is_dir() or (path / ".codex-plugin").is_dir()
        ):
            return path
    raise PackageError(f"could not locate plugin root from {start}")


def read_version(root: Path) -> str:
    version_path = root / "VERSION"
    if not version_path.is_file():
        raise PackageError("missing VERSION")
    version = version_path.read_text(encoding="utf-8").strip()
    if not SEMVER_PLAIN_RE.fullmatch(version):
        raise PackageError(f"VERSION must be plain SemVer (got {version!r})")
    if CACHEBUSTER_RE.search(version):
        raise PackageError(f"VERSION must not contain cachebuster metadata: {version!r}")
    return version


def _should_skip_relative(relative: str) -> bool:
    if relative.startswith(SKIP_RELATIVE_PREFIXES):
        return True
    name = Path(relative).name
    if name.endswith(SKIP_FILE_SUFFIXES) or name in {".DS_Store", "Thumbs.db"}:
        return True
    parts = Path(relative).parts
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    return False


def list_via_git(root: Path) -> list[str] | None:
    """Return tracked files via git ls-files, or None if not a usable checkout."""
    if not (root / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.split(b"\0")
    files: list[str] = []
    for item in raw:
        if not item:
            continue
        relative = item.decode("utf-8", errors="surrogateescape")
        if _should_skip_relative(relative):
            continue
        path = root / relative
        if not path.is_file():
            continue
        files.append(relative.replace("\\", "/"))
    return sorted(set(files))


def list_via_walk(root: Path) -> list[str]:
    files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        path = Path(dirpath)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIP_DIR_NAMES and not name.startswith(".claude-home-")
            and not name.startswith(".codex-home-")
        )
        for name in sorted(filenames):
            full = path / name
            if not full.is_file():
                continue
            relative = full.relative_to(root).as_posix()
            if _should_skip_relative(relative):
                continue
            files.append(relative)
    return files


def list_plugin_files(root: Path) -> list[str]:
    # Prefer git-tracked files when the index is populated. An empty ls-files
    # result (fresh repo with no commits yet) falls back to a filesystem walk
    # so local packaging still works before the initial push.
    git_files = list_via_git(root)
    if git_files:
        files = git_files
    else:
        files = list_via_walk(root)
    if not files:
        raise PackageError("no package files discovered for plugin ZIP")
    # Hard minimum surfaces for a usable installable plugin.
    required = (
        "VERSION",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
        "assets/traycer-icon.png",
        "skills/take-pr-to-completion/SKILL.md",
        "skills/commit-workspace-changes/SKILL.md",
        "skills/gh-review-comment-triage/SKILL.md",
        "skills/merge-conflict-resolution/SKILL.md",
    )
    missing = [item for item in required if item not in files]
    if missing:
        raise PackageError(f"plugin allowlist missing required files: {missing}")
    return files


def list_skills_files(plugin_files: list[str]) -> list[str]:
    skills = [path for path in plugin_files if path.startswith("skills/")]
    if not skills:
        raise PackageError("skills source archive would be empty")
    for skill in (
        "take-pr-to-completion",
        "commit-workspace-changes",
        "gh-review-comment-triage",
        "merge-conflict-resolution",
    ):
        expected = f"skills/{skill}/SKILL.md"
        if expected not in skills:
            raise PackageError(f"skills archive missing {expected}")
    return skills


def file_mode(path: Path) -> int:
    mode = path.stat().st_mode
    # Normalize to a stable permission set: executable stays executable for owner/group/other.
    if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        return 0o755
    return 0o644


def write_deterministic_zip(
    zip_path: Path,
    *,
    root: Path,
    members: list[str],
    archive_root: str,
) -> None:
    if zip_path.exists():
        zip_path.unlink()
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        zip_path,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for relative in members:
            source = root / relative
            if not source.is_file():
                raise PackageError(f"missing member while packaging: {relative}")
            data = source.read_bytes()
            # Reject accidental cachebuster contamination in JSON manifests.
            if relative.endswith(".json") and CACHEBUSTER_RE.search(
                data.decode("utf-8", errors="ignore")
            ):
                raise PackageError(
                    f"refusing to package cachebuster metadata in {relative}"
                )
            arcname = f"{archive_root}/{relative}"
            info = zipfile.ZipInfo(filename=arcname, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = file_mode(source) << 16
            info.create_system = 3  # Unix
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(out_dir: Path, artifacts: list[Path]) -> Path:
    lines = []
    for path in artifacts:
        digest = sha256_file(path)
        lines.append(f"{digest}  {path.name}")
    checksums = out_dir / "SHA256SUMS.txt"
    # Trailing newline, LF only, sorted by filename already via artifacts order.
    checksums.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return checksums


def package_release(root: Path, out_dir: Path) -> dict:
    version = read_version(root)
    plugin_files = list_plugin_files(root)
    skills_files = list_skills_files(plugin_files)

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    plugin_zip = out_dir / f"{PLUGIN_NAME}-{version}-plugin.zip"
    skills_zip = out_dir / f"{PLUGIN_NAME}-{version}-skills-source.zip"

    write_deterministic_zip(
        plugin_zip,
        root=root,
        members=plugin_files,
        archive_root=f"{PLUGIN_NAME}-{version}",
    )
    write_deterministic_zip(
        skills_zip,
        root=root,
        members=skills_files,
        archive_root=f"{PLUGIN_NAME}-{version}-skills",
    )

    checksums = write_checksums(out_dir, [plugin_zip, skills_zip])

    return {
        "version": version,
        "plugin_zip": plugin_zip,
        "skills_zip": skills_zip,
        "checksums": checksums,
        "plugin_file_count": len(plugin_files),
        "skills_file_count": len(skills_files),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build deterministic plugin and skills-source ZIPs with SHA-256 checksums."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (default: repository containing this script)",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="output directory (default: <root>/release-out)",
    )
    args = parser.parse_args(argv)

    try:
        root = (
            args.root.resolve()
            if args.root is not None
            else plugin_root_from(Path(__file__))
        )
        out_dir = (
            args.out_dir.resolve()
            if args.out_dir is not None
            else (root / "release-out")
        )
        result = package_release(root, out_dir)
    except PackageError as error:
        print(f"package-release failed: {error}", file=sys.stderr)
        return 1

    print(f"package-release ok version={result['version']}")
    print(f"  plugin:   {result['plugin_zip']} ({result['plugin_file_count']} files)")
    print(f"  skills:   {result['skills_zip']} ({result['skills_file_count']} files)")
    print(f"  checksums:{result['checksums']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
