#!/usr/bin/env python3
"""Set the canonical package version across VERSION and dual-harness manifests.

Updates:
  - VERSION
  - .claude-plugin/plugin.json
  - .codex-plugin/plugin.json
  - .claude-plugin/marketplace.json (pr-completion plugin entry)

Release source must remain plain SemVer (no +codex.<timestamp> build metadata).

All targets are loaded and validated before any mutation. Writes are atomic per
file; any failure rolls back already-written targets so the tree is unchanged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

PLUGIN_NAME = "pr-completion"


class SetVersionError(Exception):
    """Validation or write failure for set-version."""


@dataclass(frozen=True)
class PlannedWrite:
    path: Path
    original: bytes | None
    new_content: bytes
    summary: str


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (
            (path / ".claude-plugin").is_dir() or (path / ".codex-plugin").is_dir()
        ):
            return path
    raise SetVersionError(f"could not locate plugin root from {start}")


def validate_version(version: str, *, allow_build_metadata: bool) -> str:
    value = version.strip()
    if not value:
        raise SetVersionError("version must be a non-empty string")
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise SetVersionError(f"version is not valid SemVer: {version!r}")
    if not allow_build_metadata and match.group(5):
        raise SetVersionError(
            "release source versions must be plain SemVer without build metadata "
            f"(got {version!r}; strip +codex.* before release)"
        )
    return value


def read_optional_bytes(path: Path) -> bytes | None:
    if not path.is_file():
        return None
    return path.read_bytes()


def load_json_object(path: Path, original: bytes) -> dict:
    try:
        payload = json.loads(original.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise SetVersionError(f"{path}: not valid UTF-8: {error}") from error
    except json.JSONDecodeError as error:
        raise SetVersionError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SetVersionError(f"{path} must contain a JSON object")
    return payload


def encode_json(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2) + "\n").encode("utf-8")


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def plan_set_version(root: Path, version: str) -> list[PlannedWrite]:
    plain = validate_version(version, allow_build_metadata=False)
    plans: list[PlannedWrite] = []

    version_path = root / "VERSION"
    version_original = read_optional_bytes(version_path)
    previous = (
        version_original.decode("utf-8").strip()
        if version_original is not None
        else ""
    )
    plans.append(
        PlannedWrite(
            path=version_path,
            original=version_original,
            new_content=(plain + "\n").encode("utf-8"),
            summary=f"VERSION: {previous or '(missing)'} -> {plain}",
        )
    )

    for relative in (
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
    ):
        path = root / relative
        original = read_optional_bytes(path)
        if original is None:
            raise SetVersionError(f"missing required version file: {relative}")
        payload = load_json_object(path, original)
        old = payload.get("version")
        payload["version"] = plain
        plans.append(
            PlannedWrite(
                path=path,
                original=original,
                new_content=encode_json(payload),
                summary=f"{relative}: {old!r} -> {plain!r}",
            )
        )

    marketplace_path = root / ".claude-plugin" / "marketplace.json"
    marketplace_original = read_optional_bytes(marketplace_path)
    if marketplace_original is None:
        raise SetVersionError("missing required version file: .claude-plugin/marketplace.json")
    marketplace = load_json_object(marketplace_path, marketplace_original)
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        raise SetVersionError(
            f"{marketplace_path}: missing plugins array"
        )
    found = False
    old_market: object = None
    for entry in plugins:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != PLUGIN_NAME:
            continue
        old_market = entry.get("version")
        entry["version"] = plain
        found = True
        break
    if not found:
        raise SetVersionError(
            f"{marketplace_path}: no plugin entry named {PLUGIN_NAME!r} to update"
        )
    plans.append(
        PlannedWrite(
            path=marketplace_path,
            original=marketplace_original,
            new_content=encode_json(marketplace),
            summary=(
                f".claude-plugin/marketplace.json plugins[{PLUGIN_NAME}]: "
                f"{old_market!r} -> {plain!r}"
            ),
        )
    )
    return plans


def apply_plans(plans: list[PlannedWrite]) -> None:
    written: list[PlannedWrite] = []
    try:
        for plan in plans:
            atomic_write(plan.path, plan.new_content)
            written.append(plan)
    except Exception as error:
        # Best-effort rollback so a partial update never sticks.
        for plan in reversed(written):
            try:
                if plan.original is None:
                    plan.path.unlink(missing_ok=True)
                else:
                    atomic_write(plan.path, plan.original)
            except Exception as rollback_error:  # noqa: BLE001
                raise SetVersionError(
                    f"write failed ({error}); rollback also failed for "
                    f"{plan.path}: {rollback_error}"
                ) from error
        raise SetVersionError(f"write failed; all targets restored: {error}") from error


def set_version(root: Path, version: str) -> list[str]:
    plans = plan_set_version(root, version)
    apply_plans(plans)
    return [plan.summary for plan in plans]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Set VERSION and dual-harness/marketplace versions to the same plain SemVer."
        )
    )
    parser.add_argument(
        "version",
        help="plain SemVer such as 0.1.0 (build metadata is rejected)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (defaults to repository containing this script)",
    )
    args = parser.parse_args(argv)
    try:
        root = args.root if args.root is not None else plugin_root_from(Path(__file__))
        for line in set_version(root, args.version):
            print(line)
        print(f"set-version complete for {root}")
        return 0
    except SetVersionError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
