#!/usr/bin/env python3
"""Stage a temporary Codex cachebuster copy and optionally reinstall from it.

Release source must keep plain SemVer in .codex-plugin/plugin.json.
Local Codex iteration copies the plugin to a temporary directory, applies
`+codex.<timestamp>` only there, then reinstalls from that copy.

The staged marketplace uses the Codex-supported layout:

  <marketplace-root>/.agents/plugins/marketplace.json
  <marketplace-root>/plugins/pr-completion/

By default this script only stages and prints paths. Pass --install to run:

  codex plugin marketplace add <staging-marketplace-root>
  codex plugin add pr-completion@pr-completion-dev

Use --codex-home DIR for an isolated Codex home (required for safe tests).
Without --codex-home, --install uses the caller's real Codex configuration.

The working tree under --root is never modified by the cachebuster.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PLUGIN_NAME = "pr-completion"
MARKETPLACE_NAME = "pr-completion-dev"
CACHEBUSTER_PREFIX = "codex"
STAGING_MARKER_NAME = ".pr-completion-codex-staging"
STAGING_MARKER_CONTENTS = "owned-by: scripts/stage-codex-dev-install.py\n"
STAGING_MARKER_BYTES = STAGING_MARKER_CONTENTS.encode("utf-8")

IGNORE_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    ".codex-staging",
    ".cachebust",
    "node_modules",
    ".DS_Store",
    STAGING_MARKER_NAME,
}


class StagingError(Exception):
    """Safe staging failure."""


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (path / ".codex-plugin").is_dir():
            return path
    raise StagingError(f"could not locate plugin root from {start}")


def sanitize_cachebuster(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    sanitized = re.sub(r"-{2,}", "-", sanitized).strip("-")
    if not sanitized:
        raise StagingError("Cachebuster must contain at least one letter or digit.")
    return sanitized


def default_cachebuster() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def with_cachebuster(version: str, cachebuster: str) -> str:
    prefix = version.split("+", 1)[0]
    return f"{prefix}+{CACHEBUSTER_PREFIX}.{cachebuster}"


def ignore_copy(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in IGNORE_NAMES:
            ignored.add(name)
            continue
        if name.endswith((".pyc", ".pyo", ".log")):
            ignored.add(name)
    return ignored


def load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise StagingError(f"{path} must contain a JSON object")
    return payload


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_same_or_relative(path: Path, other: Path) -> bool:
    """True if path == other, path is inside other, or other is inside path."""
    left = path.resolve()
    right = other.resolve()
    if left == right:
        return True
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        pass
    return False


def _lstat_or_none(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def is_authentic_staging_marker(marker: Path) -> bool:
    """True only for a regular, non-symlink marker with exact owned contents.

    Uses lstat + O_NOFOLLOW so a symlink to a plausible marker file never
    authenticates ownership.
    """
    st = _lstat_or_none(marker)
    if st is None:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(marker), flags)
    except OSError:
        return False
    try:
        data = os.read(fd, max(st.st_size, 0) + 1)
    finally:
        os.close(fd)
    return data == STAGING_MARKER_BYTES


def write_staging_marker(staging_root: Path) -> None:
    """Atomically write the ownership marker without following a destination symlink.

    Writes a temp file in the same directory, then uses os.replace so a symlink
    at the marker path is replaced as a directory entry rather than followed.
    Callers must only invoke this on paths that are empty/missing or already
    authenticated; spoofed markers are rejected earlier without mutation.
    """
    staging_root.mkdir(parents=True, exist_ok=True)
    marker = staging_root / STAGING_MARKER_NAME
    existing = _lstat_or_none(marker)
    if existing is not None and stat.S_ISLNK(existing.st_mode):
        # Never follow or overwrite through a symlink marker.
        raise StagingError(
            f"refusing to write ownership marker over symlink: {marker}"
        )
    if existing is not None and not stat.S_ISREG(existing.st_mode):
        raise StagingError(
            f"refusing to write ownership marker over non-regular path: {marker}"
        )

    fd, tmp_name = tempfile.mkstemp(
        prefix=".pr-completion-marker.",
        suffix=".tmp",
        dir=str(staging_root),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(STAGING_MARKER_BYTES)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic directory-entry replace; does not follow a symlink at `marker`.
        os.replace(tmp_path, marker)
    except Exception:
        if tmp_path.exists() or tmp_path.is_symlink():
            tmp_path.unlink(missing_ok=True)
        raise

    if not is_authentic_staging_marker(marker):
        raise StagingError(f"failed to establish authentic staging marker at {marker}")


def clear_script_owned_staging(staging_root: Path) -> None:
    """Remove prior staged contents after an authentic marker was verified.

    Leaves the marker entry alone; the caller rewrites it atomically afterward.
    Uses lstat so symlinks are unlinked as entries, not followed into targets.
    """
    for child in list(staging_root.iterdir()):
        if child.name == STAGING_MARKER_NAME:
            continue
        st = _lstat_or_none(child)
        if st is None:
            continue
        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)


def prepare_staging_root(source_root: Path, keep_dir: Path | None) -> Path:
    """Return a safe marketplace root that never overlaps the source tree.

    --keep-dir rules:
      - reject paths equal to, inside, or ancestors of the source root
      - create when missing
      - allow empty existing directories
      - allow reuse only when an authentic (lstat-regular, exact-content) marker
        is present
      - never delete entries when the marker is missing, wrong, or a symlink
    """
    source = source_root.resolve()

    if keep_dir is None:
        staging_root = Path(tempfile.mkdtemp(prefix="pr-completion-codex-dev-")).resolve()
        if is_same_or_relative(staging_root, source):
            shutil.rmtree(staging_root, ignore_errors=True)
            raise StagingError(
                f"refusing temporary staging path that overlaps source: {staging_root}"
            )
        write_staging_marker(staging_root)
        return staging_root

    staging_root = keep_dir.expanduser().resolve()
    if is_same_or_relative(staging_root, source):
        raise StagingError(
            "staging path must not equal, contain, or live inside the source tree: "
            f"staging={staging_root} source={source}"
        )

    if not staging_root.exists():
        staging_root.mkdir(parents=True, exist_ok=False)
        write_staging_marker(staging_root)
        return staging_root

    staging_st = _lstat_or_none(staging_root)
    if staging_st is None or not stat.S_ISDIR(staging_st.st_mode) or stat.S_ISLNK(
        staging_st.st_mode
    ):
        raise StagingError(
            f"staging path exists and is not a real directory: {staging_root}"
        )

    marker = staging_root / STAGING_MARKER_NAME
    children = list(staging_root.iterdir())
    if not children:
        write_staging_marker(staging_root)
        return staging_root

    if not is_authentic_staging_marker(marker):
        raise StagingError(
            "staging path exists without an authentic ownership marker; "
            f"refuse to delete: {staging_root}"
        )

    # Authentic marker only: safe to replace prior staged contents.
    clear_script_owned_staging(staging_root)
    write_staging_marker(staging_root)
    return staging_root


def apply_cachebuster(plugin_root: Path, cachebuster: str) -> str:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    if not manifest_path.is_file():
        raise StagingError(f"missing Codex manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        raise StagingError(f"{manifest_path} must contain a non-empty string version")
    next_version = with_cachebuster(version, cachebuster)
    manifest["version"] = next_version
    write_json(manifest_path, manifest)
    return next_version


def write_marketplace_manifest(marketplace_root: Path) -> Path:
    """Write Codex-supported marketplace at .agents/plugins/marketplace.json."""
    marketplace_path = (
        marketplace_root / ".agents" / "plugins" / "marketplace.json"
    )
    marketplace = {
        "name": MARKETPLACE_NAME,
        "interface": {
            "displayName": "PR Completion Dev Staging",
        },
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {
                    "source": "local",
                    "path": f"./plugins/{PLUGIN_NAME}",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }
    write_json(marketplace_path, marketplace)
    return marketplace_path


def stage_plugin(
    source_root: Path,
    cachebuster: str,
    keep_dir: Path | None,
) -> tuple[Path, Path, Path, str]:
    marketplace_root = prepare_staging_root(source_root, keep_dir)
    plugin_dest = marketplace_root / "plugins" / PLUGIN_NAME
    if plugin_dest.exists():
        # Only reachable for marker-owned staging that was cleared incompletely.
        if plugin_dest.is_dir():
            shutil.rmtree(plugin_dest)
        else:
            plugin_dest.unlink()
    plugin_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, plugin_dest, ignore=ignore_copy)
    staged_version = apply_cachebuster(plugin_dest, cachebuster)
    marketplace_path = write_marketplace_manifest(marketplace_root)
    return marketplace_root, marketplace_path, plugin_dest, staged_version


def source_version(source_root: Path) -> str:
    manifest = load_json(source_root / ".codex-plugin" / "plugin.json")
    version = manifest.get("version")
    if not isinstance(version, str):
        raise StagingError("source Codex plugin version missing")
    return version


def install_env(codex_home: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if codex_home is None:
        return env
    home = codex_home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    # Isolate both Codex state and the implicit personal marketplace under HOME.
    fake_home = home / "home"
    fake_home.mkdir(parents=True, exist_ok=True)
    (fake_home / ".agents" / "plugins").mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(fake_home)
    env["CODEX_HOME"] = str(home)
    # Prevent leaking user config paths through XDG variables when present.
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("XDG_DATA_HOME", None)
    env.pop("XDG_STATE_HOME", None)
    return env


def log(message: str, *, as_json: bool) -> None:
    """Write human logs to stderr when machine JSON is expected on stdout."""
    stream = sys.stderr if as_json else sys.stdout
    print(message, file=stream)


def run_install(
    marketplace_root: Path,
    codex_home: Path | None,
    *,
    as_json: bool,
) -> dict[str, object]:
    env = install_env(codex_home)
    commands = [
        ["codex", "plugin", "marketplace", "add", str(marketplace_root)],
        [
            "codex",
            "plugin",
            "add",
            f"{PLUGIN_NAME}@{MARKETPLACE_NAME}",
            "--json",
        ],
    ]
    results: list[dict[str, object]] = []
    for command in commands:
        log("+ " + " ".join(command), as_json=as_json)
        completed = subprocess.run(
            command,
            check=True,
            env=env,
            capture_output=True,
            text=True,
        )
        if completed.stdout.strip():
            log(completed.stdout.rstrip(), as_json=as_json)
        if completed.stderr.strip():
            print(completed.stderr.rstrip(), file=sys.stderr)
        entry: dict[str, object] = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        if "--json" in command:
            try:
                entry["json"] = json.loads(completed.stdout)
            except json.JSONDecodeError:
                entry["json"] = None
        results.append(entry)
    return {"commands": results, "isolated": codex_home is not None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Copy the plugin to a temporary staging directory, apply a Codex "
            "cachebuster only there, and optionally reinstall from that copy."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (defaults to repository containing this script)",
    )
    parser.add_argument(
        "--cachebuster",
        default=None,
        help="optional cachebuster token (default: UTC timestamp)",
    )
    parser.add_argument(
        "--keep-dir",
        type=Path,
        default=None,
        help=(
            "optional durable staging directory (default: system temp dir). "
            "Must not overlap the source tree. Existing directories are reused "
            f"only when empty or marked with {STAGING_MARKER_NAME}."
        ),
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="run codex marketplace add + plugin add against the staging copy",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help=(
            "isolated CODEX_HOME for --install (also sets a disposable HOME). "
            "Omit only when intentionally updating the real user Codex config."
        ),
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="emit machine-readable staging paths and versions",
    )
    args = parser.parse_args(argv)

    try:
        source_root = (
            args.root if args.root is not None else plugin_root_from(Path(__file__))
        )
        before = source_version(source_root)
        if "+codex." in before.lower():
            raise StagingError(
                "source .codex-plugin/plugin.json already contains cachebuster "
                "metadata; restore plain SemVer before staging"
            )

        if args.codex_home is not None and not args.install:
            raise StagingError("--codex-home requires --install")

        cachebuster = sanitize_cachebuster(args.cachebuster or default_cachebuster())
        marketplace_root, marketplace_path, staged_plugin, staged_version = stage_plugin(
            source_root, cachebuster, args.keep_dir
        )
        after = source_version(source_root)
        if after != before:
            raise StagingError(
                "source working tree changed during staging; aborting. "
                f"before={before!r} after={after!r}"
            )

        payload: dict[str, object] = {
            "sourceRoot": str(source_root.resolve()),
            "sourceVersion": before,
            "marketplaceRoot": str(marketplace_root),
            "marketplaceManifest": str(marketplace_path),
            "stagedPluginRoot": str(staged_plugin),
            "stagedVersion": staged_version,
            "marketplaceName": MARKETPLACE_NAME,
            "installSpec": f"{PLUGIN_NAME}@{MARKETPLACE_NAME}",
            "sourceUnchanged": True,
        }

        if args.install:
            install_result = run_install(
                marketplace_root,
                args.codex_home,
                as_json=args.print_json,
            )
            payload["installed"] = True
            payload["install"] = install_result
            # Re-check source after install path work.
            if source_version(source_root) != before:
                raise StagingError("source version changed during install")
        else:
            payload["installed"] = False
            payload["installHint"] = [
                f"codex plugin marketplace add {marketplace_root}",
                f"codex plugin add {PLUGIN_NAME}@{MARKETPLACE_NAME}",
            ]

        if args.print_json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"source version (unchanged): {before}")
            print(f"staged version: {staged_version}")
            print(f"staged plugin: {staged_plugin}")
            print(f"marketplace manifest: {marketplace_path}")
            print(f"staging marketplace root: {marketplace_root}")
            if args.install:
                print(f"installed {PLUGIN_NAME}@{MARKETPLACE_NAME}")
                if args.codex_home is not None:
                    print(f"isolated codex home: {args.codex_home.resolve()}")
            else:
                print("staging only; pass --install to reinstall from the copy")
                for hint in payload["installHint"]:  # type: ignore[union-attr]
                    print(f"  {hint}")
        return 0
    except StagingError as error:
        print(str(error), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        detail = ""
        if error.stdout:
            detail += f"\nstdout:\n{error.stdout}"
        if error.stderr:
            detail += f"\nstderr:\n{error.stderr}"
        print(
            f"command failed with exit {error.returncode}: {error.cmd}{detail}",
            file=sys.stderr,
        )
        return error.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
