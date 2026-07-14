#!/usr/bin/env python3
"""Build and validate the OpenAI portal upload package for a public release.

Portal evidence:
  1. skills-only ZIP rejected: missing supported plugin manifest at ZIP root
     or inside the sole top-level directory.
  2. full plugin ZIP rejected without interface.composerIcon and interface.logo
     referencing square images.
  3. v0.1.1 passed client-side manifest/icon checks but its 93-member,
     1,067,320-byte full-release upload failed generically.
  4. Verified portal identity is Business — Traycer.

The authenticated upload artifact is therefore a minimal plugin package:
the Codex manifest, runtime skill files, and referenced square assets under
one top-level directory. Release workflows, docs, tests, submission materials,
and alternate-harness manifests are intentionally excluded.

Pins below describe the last published release whose immutable full-plugin
artifact is checked independently. A pre-release ``--from-working-tree`` build
uses VERSION and does not reuse these historical pins.
Immutable-tag reconstruction enforces the portable content fingerprint.
Exact published ZIP bytes are verified only by the independent hosted
release-integrity job (not by content equivalence alone).

Usage:
  python3 -B scripts/package-openai-submission.py
  python3 -B scripts/package-openai-submission.py --from-working-tree
  python3 -B scripts/package-openai-submission.py --from-pinned-release
  python3 -B scripts/package-openai-submission.py --check-urls
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


RELEASE_VERSION = "0.1.1"
RELEASE_REF = "v0.1.1"
# Filled after tag + release publish. Empty string means "resolve from tag /
# working tree and skip published-checksum pin until set".
RELEASE_COMMIT = "52b2f8b710a20389237204092bbe67dd65ed89e8"
# Published GitHub Release installable plugin ZIP bytes.
RELEASE_INSTALLABLE_SHA256 = (
    "3811207f95feda2d79bc3995f316411ed32e5f7bcad139863ec70c94735af02c"
)
# Published portal-upload ZIP bytes. v0.1.1 reused the full installable ZIP;
# v0.1.2 and later publish a distinct minimal portal asset.
RELEASE_PORTAL_SHA256 = (
    "3811207f95feda2d79bc3995f316411ed32e5f7bcad139863ec70c94735af02c"
)
# Platform-independent fingerprint of sorted (path, mode, content) members of
# that same package. ZIP container bytes can differ across zlib/platform even
# when member payloads are identical; content pin covers that case.
RELEASE_PLUGIN_CONTENT_SHA256 = (
    "39fa994d3cebddbcffde2a7ebdf1ea669a1f0880cc362d0f4ce8d3cdfa8fa989"
)

ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
MAX_PORTAL_ZIP_BYTES = 1024 * 1024
PORTAL_SKILL_EXCLUDED_PARTS = frozenset({"tests", "__pycache__"})

ALLOWED_MATERIAL_PATHS = (
    "README.md",
    "assets/logo.png",
    "availability.md",
    "fixtures/ci-failure.json",
    "listing.json",
    "portal-checklist.md",
    "release-notes.md",
    "starter-prompts.json",
    "test-cases.json",
)

FORBIDDEN_PATH_PARTS = frozenset(
    {
        ".git",
        ".cache",
        ".codex-staging",
        ".env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "release-out",
        "submission-out",
        "tmp",
        "venv",
    }
)
FORBIDDEN_FILE_SUFFIXES = (".pyc", ".pyo", ".pem", ".key", ".log", ".tmp")
_MAC_HOME = b"/" + b"Users/"
_LINUX_HOME = b"/" + b"home/"
_WINDOWS_HOME = rb"[A-Za-z]:\\" + b"Users" + rb"\\[^\\\r\n]+\\"
PERSONAL_PATH_RE = re.compile(
    rb"(?:"
    + re.escape(_MAC_HOME)
    + rb"[A-Za-z0-9._-]+/|"
    + re.escape(_LINUX_HOME)
    + rb"[A-Za-z0-9._-]+/|"
    + _WINDOWS_HOME
    + rb")"
)
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE " + b"KEY-----"),
    re.compile(rb"(?:" + b"gh" + rb"p_|github" + b"_pat_" + rb")[A-Za-z0-9_]{20,}"),
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
)
CACHEBUSTER_RE = re.compile(rb"\+codex\.[0-9]{8,}", re.IGNORECASE)
OFFICIAL_SUBMISSION_DOC = "https://learn.chatgpt.com/docs/submit-plugins"
SUPPORTED_MANIFEST_RELATIVES = (
    ".codex-plugin/plugin.json",
    ".claude-plugin/plugin.json",
    ".agent-plugin/plugin.json",
)
# Portal dropdown label (em dash). Use unicode escape for Windows source safety.
PORTAL_BUSINESS_IDENTITY = "Business \u2014 Traycer"


class SubmissionError(Exception):
    """Submission input is unsafe, incomplete, or not the pinned release."""


def _load_package_release_module() -> Any:
    path = Path(__file__).resolve().parent / "package-release.py"
    spec = importlib.util.spec_from_file_location("pr_completion_package_release", path)
    if spec is None or spec.loader is None:
        raise SubmissionError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_git(repo: Path, args: list[str], *, text: bool = False) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=text,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise SubmissionError(f"git {' '.join(args)} failed: {stderr}")
    return completed.stdout


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_relative_path(relative: str) -> None:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise SubmissionError(f"unsafe package path: {relative}")
    if any(part in FORBIDDEN_PATH_PARTS for part in path.parts):
        raise SubmissionError(f"forbidden cache/local path: {relative}")
    if relative.endswith(FORBIDDEN_FILE_SUFFIXES):
        raise SubmissionError(f"forbidden file type: {relative}")


def _scan_bytes(relative: str, data: bytes) -> None:
    _validate_relative_path(relative)
    if PERSONAL_PATH_RE.search(data):
        raise SubmissionError(f"personal absolute path found in {relative}")
    # Cachebusters are fatal only in release identity files (tests may mention
    # the pattern when asserting rejection).
    if relative == "VERSION" or relative.endswith(".json"):
        if CACHEBUSTER_RE.search(data):
            raise SubmissionError(f"timestamp cachebuster found in {relative}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            raise SubmissionError(f"credential-like content found in {relative}")


def resolve_tag(repo: Path) -> str:
    resolved = str(
        _run_git(
            repo,
            ["rev-parse", "--verify", f"refs/tags/{RELEASE_REF}^{{commit}}"],
            text=True,
        )
    ).strip()
    if RELEASE_COMMIT and resolved != RELEASE_COMMIT:
        raise SubmissionError(
            f"{RELEASE_REF} resolves to {resolved}, expected {RELEASE_COMMIT}; "
            "refusing retag/drift"
        )
    return resolved


def load_tagged_files(repo: Path) -> dict[str, tuple[int, bytes]]:
    """Load the full public plugin tree from the immutable release tag."""
    commit = resolve_tag(repo)
    raw = bytes(_run_git(repo, ["ls-tree", "-r", "-z", "--full-tree", commit]))
    entries: dict[str, tuple[int, bytes]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode_raw, kind_raw, object_id = metadata.split(b" ", 2)
        relative = raw_path.decode("utf-8")
        if kind_raw != b"blob":
            continue
        if mode_raw not in {b"100644", b"100755"}:
            raise SubmissionError(
                f"tagged entry is not an allowed regular file: {relative} "
                f"({mode_raw.decode()} {kind_raw.decode()})"
            )
        # Skip paths that package-release would exclude from the plugin ZIP.
        if relative.startswith(
            (
                "docs/_site/",
                "release-out/",
                "submission-out/",
                ".codex-staging/",
                ".cachebust/",
            )
        ):
            continue
        if any(
            part in FORBIDDEN_PATH_PARTS
            for part in Path(relative).parts
        ):
            continue
        data = bytes(_run_git(repo, ["cat-file", "blob", object_id.decode("ascii")]))
        _scan_bytes(relative, data)
        entries[relative] = (0o755 if mode_raw == b"100755" else 0o644, data)
    if not entries:
        raise SubmissionError("tagged plugin tree is empty")
    return entries


def load_working_tree_files(repo: Path) -> dict[str, tuple[int, bytes]]:
    package_mod = _load_package_release_module()
    relatives = package_mod.list_plugin_files(repo)
    entries: dict[str, tuple[int, bytes]] = {}
    for relative in relatives:
        path = repo / relative
        data = path.read_bytes()
        _scan_bytes(relative, data)
        mode = 0o755 if package_mod.file_mode(path) == 0o755 else 0o644
        entries[relative] = (mode, data)
    return entries


def read_working_version(repo: Path) -> str:
    value = (repo / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
        raise SubmissionError(f"working VERSION must be plain SemVer (got {value!r})")
    return value


def select_portal_members(
    source_members: dict[str, tuple[int, bytes]],
) -> dict[str, tuple[int, bytes]]:
    """Return only files required by the authenticated skills-only upload."""
    manifest_relative = ".codex-plugin/plugin.json"
    codex = _decode_json_member(source_members, manifest_relative)
    interface = codex.get("interface")
    if not isinstance(interface, dict):
        raise SubmissionError(f"{manifest_relative} missing interface object")

    selected_paths = {manifest_relative}
    for field in ("composerIcon", "logo"):
        relative = resolve_plugin_relative(interface.get(field))
        selected_paths.add(relative)

    selected_paths.update(
        relative
        for relative in source_members
        if relative.startswith("skills/")
        and not any(
            part in PORTAL_SKILL_EXCLUDED_PARTS for part in Path(relative).parts
        )
    )
    missing = sorted(selected_paths - source_members.keys())
    if missing:
        raise SubmissionError(f"minimal portal package missing required members: {missing}")
    selected = {relative: source_members[relative] for relative in sorted(selected_paths)}
    if not any(relative.endswith("/SKILL.md") for relative in selected):
        raise SubmissionError("minimal portal package contains no SKILL.md files")
    return selected


def _write_zip(path: Path, members: list[tuple[str, int, bytes]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for archive_name, mode, data in members:
            info = zipfile.ZipInfo(archive_name, date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = mode << 16
            info.create_system = 3
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def members_content_sha256(members: dict[str, tuple[int, bytes]]) -> str:
    """Stable fingerprint of package members independent of ZIP container bytes."""
    digest = hashlib.sha256()
    for relative in sorted(members):
        mode, data = members[relative]
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(struct.pack(">I", int(mode)))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def verify_content_pin(members: dict[str, tuple[int, bytes]]) -> str:
    """Portable gate: logical package identity only (cross-platform safe)."""
    if not RELEASE_PLUGIN_CONTENT_SHA256:
        return members_content_sha256(members)
    content_digest = members_content_sha256(members)
    if content_digest != RELEASE_PLUGIN_CONTENT_SHA256:
        raise SubmissionError(
            "reconstructed portal plugin content fingerprint does not match "
            f"published content pin: {content_digest} != {RELEASE_PLUGIN_CONTENT_SHA256}"
        )
    return content_digest


def verify_published_installable_zip_bytes(zip_path: Path) -> str:
    """Independent exact-byte gate for the published installable plugin ZIP.

    This path cannot pass via content fingerprint alone. A wrong
    ``RELEASE_INSTALLABLE_SHA256`` always fails here even when member content is
    correct. Use only for release-integrity (Ubuntu download / pin probe).
    """
    if not RELEASE_INSTALLABLE_SHA256:
        raise SubmissionError(
            "RELEASE_INSTALLABLE_SHA256 is not pinned; cannot verify exact "
            "published installable ZIP bytes"
        )
    digest = sha256_file(zip_path)
    if digest != RELEASE_INSTALLABLE_SHA256:
        raise SubmissionError(
            "exact published installable ZIP pin mismatch (content equivalence is not "
            f"a substitute): {digest} != {RELEASE_INSTALLABLE_SHA256}"
        )
    return digest


def verify_published_portal_zip_bytes(zip_path: Path) -> str:
    """Independent exact-byte gate for the minimal portal-upload ZIP."""
    if not RELEASE_PORTAL_SHA256:
        raise SubmissionError(
            "RELEASE_PORTAL_SHA256 is not pinned; cannot verify exact published "
            "portal ZIP bytes"
        )
    digest = sha256_file(zip_path)
    if digest != RELEASE_PORTAL_SHA256:
        raise SubmissionError(
            "exact published portal ZIP pin mismatch: "
            f"{digest} != {RELEASE_PORTAL_SHA256}"
        )
    return digest


def build_portal_plugin_zip(
    out_path: Path,
    members: dict[str, tuple[int, bytes]],
    *,
    archive_version: str,
    enforce_content_pin: bool = False,
) -> str:
    """Write the portal plugin ZIP.

    Portable reconstruction (tag path on any OS) may set
    ``enforce_content_pin=True`` so member identity is checked without
    requiring Ubuntu ZIP container bytes. Exact published ZIP bytes are
    enforced only via the separate published-asset byte gates.
    """
    ordered = sorted(members)
    archive_root = f"pr-completion-{archive_version}"
    zip_members = [
        (f"{archive_root}/{relative}", members[relative][0], members[relative][1])
        for relative in ordered
    ]
    _write_zip(out_path, zip_members)
    archive_size = out_path.stat().st_size
    if archive_size > MAX_PORTAL_ZIP_BYTES:
        out_path.unlink(missing_ok=True)
        raise SubmissionError(
            "minimal portal ZIP exceeds the conservative 1 MiB upload guard: "
            f"{archive_size} > {MAX_PORTAL_ZIP_BYTES} bytes"
        )
    digest = sha256_file(out_path)
    if enforce_content_pin:
        verify_content_pin(members)
    return digest


def validate_png_bytes(data: bytes, *, label: str) -> dict[str, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SubmissionError(f"{label} is not a valid PNG with an IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if width != height or width < 512 or width > 2048:
        raise SubmissionError(
            f"{label} must be square and 512-2048px (got {width}x{height})"
        )
    return {"width": width, "height": height}


def _decode_json_member(
    members: dict[str, tuple[int, bytes]], relative: str
) -> dict[str, Any]:
    if relative not in members:
        raise SubmissionError(f"portal package missing {relative}")
    try:
        payload = json.loads(members[relative][1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SubmissionError(f"invalid JSON in {relative}: {error}") from error
    if not isinstance(payload, dict):
        raise SubmissionError(f"{relative} must contain a JSON object")
    return payload


def discover_manifest_relative(members: dict[str, tuple[int, bytes]]) -> str:
    for relative in SUPPORTED_MANIFEST_RELATIVES:
        if relative in members:
            return relative
    raise SubmissionError(
        "portal package missing a supported plugin manifest "
        "(.codex-plugin/plugin.json, .agent-plugin/plugin.json, or "
        ".claude-plugin/plugin.json) under the sole top-level plugin directory"
    )


def resolve_plugin_relative(value: str) -> str:
    if not isinstance(value, str) or not value.startswith("./"):
        raise SubmissionError(
            f"visual path must be plugin-root-relative starting with ./ (got {value!r})"
        )
    if ".." in Path(value).parts:
        raise SubmissionError(f"visual path escapes plugin root: {value}")
    return value[2:]


def validate_portal_package(
    members: dict[str, tuple[int, bytes]], *, expected_version: str
) -> dict[str, Any]:
    """Enforce authenticated portal preflight against reconstructed members."""
    if not members:
        raise SubmissionError("portal package has no members")
    if ".codex-plugin/plugin.json" not in members:
        raise SubmissionError(
            "portal package must include public .codex-plugin/plugin.json "
            "(skills-only ZIPs are rejected by the authenticated upload flow)"
        )
    if not any(path.startswith("skills/") and path.endswith("/SKILL.md") for path in members):
        raise SubmissionError("portal package must include skills/")

    manifest_rel = discover_manifest_relative(members)
    payload = _decode_json_member(members, manifest_rel)
    version = payload.get("version")
    if version != expected_version:
        raise SubmissionError(
            f"{manifest_rel} version {version!r} does not match release "
            f"{expected_version!r}"
        )
    if payload.get("name") != "pr-completion":
        raise SubmissionError(f"{manifest_rel} name must be pr-completion")
    if payload.get("skills") != "./skills/":
        raise SubmissionError(
            f"{manifest_rel} skills must be the plugin-root-relative ./skills/ path"
        )

    # Codex portal path: visual fields live on the Codex manifest interface.
    codex = _decode_json_member(members, ".codex-plugin/plugin.json")
    interface = codex.get("interface")
    if not isinstance(interface, dict):
        raise SubmissionError(".codex-plugin/plugin.json missing interface object")
    if interface.get("developerName") != "Traycer":
        raise SubmissionError(
            "interface.developerName must be Traycer "
            f"({PORTAL_BUSINESS_IDENTITY} portal identity)"
        )
    default_prompt = interface.get("defaultPrompt")
    if (
        not isinstance(default_prompt, list)
        or not default_prompt
        or any(not isinstance(item, str) or not item.strip() for item in default_prompt)
    ):
        raise SubmissionError(
            "interface.defaultPrompt must be a non-empty array of non-empty strings"
        )
    visuals = {}
    for field in ("composerIcon", "logo"):
        if field not in interface:
            raise SubmissionError(
                f".codex-plugin/plugin.json missing required interface.{field}"
            )
        relative = resolve_plugin_relative(interface[field])
        if relative not in members:
            raise SubmissionError(
                f"interface.{field} references missing package member {relative}"
            )
        dims = validate_png_bytes(members[relative][1], label=f"interface.{field}")
        visuals[field] = {"path": f"./{relative}", **dims}

    # Assets must live inside the plugin root members (already enforced by
    # relative path resolution without .. and membership check).
    unexpected = sorted(
        relative
        for relative in members
        if relative != ".codex-plugin/plugin.json"
        and not relative.startswith("skills/")
        and relative not in {item["path"].removeprefix("./") for item in visuals.values()}
    )
    if unexpected:
        raise SubmissionError(
            f"minimal portal package contains non-runtime files: {unexpected}"
        )
    return {
        "manifest": manifest_rel,
        "version": version,
        "visuals": visuals,
        "memberCount": len(members),
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SubmissionError(f"invalid JSON in {path}: {error}") from error


def load_materials(materials_root: Path) -> dict[str, bytes]:
    if not materials_root.is_dir():
        raise SubmissionError(f"missing materials directory: {materials_root}")
    discovered: list[str] = []
    for dirpath, dirnames, filenames in os.walk(materials_root):
        current = Path(dirpath)
        for dirname in dirnames:
            relative_dir = (current / dirname).relative_to(materials_root).as_posix()
            _validate_relative_path(relative_dir)
        for filename in filenames:
            path = current / filename
            relative = path.relative_to(materials_root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise SubmissionError(f"submission material is not a regular file: {relative}")
            discovered.append(relative)
    discovered.sort()
    expected = list(ALLOWED_MATERIAL_PATHS)
    if discovered != expected:
        missing = sorted(set(expected) - set(discovered))
        extra = sorted(set(discovered) - set(expected))
        raise SubmissionError(
            f"submission material allowlist mismatch; missing={missing}, extra={extra}"
        )
    materials: dict[str, bytes] = {}
    for relative in discovered:
        data = (materials_root / relative).read_bytes()
        _scan_bytes(f"submission/openai/{relative}", data)
        materials[relative] = data
    return materials


def validate_listing(
    materials_root: Path,
    materials: dict[str, bytes],
    *,
    expected_version: str,
    expected_ref: str,
    enforce_published_pins: bool,
) -> dict[str, Any]:
    listing = _load_json(materials_root / "listing.json")
    if not isinstance(listing, dict):
        raise SubmissionError("listing.json must contain an object")
    required_values = {
        "submissionType": "skills-only",
        "pluginName": "PR Completion",
        "category": "Productivity",
        "containsMCP": False,
    }
    for key, expected in required_values.items():
        if listing.get(key) != expected:
            raise SubmissionError(f"listing.{key} must be {expected!r}")
    source = listing.get("source")
    if not isinstance(source, dict) or source.get("tag") != expected_ref:
        raise SubmissionError("listing source.tag must match the target release tag")
    if source.get("version") != expected_version:
        raise SubmissionError("listing source.version must match the target release version")
    # Once RELEASE_COMMIT is populated, listing source.commit must equal it
    # exactly. Empty and wrong values both fail (no soft allowance for "").
    if enforce_published_pins and RELEASE_COMMIT:
        if source.get("commit") != RELEASE_COMMIT:
            raise SubmissionError(
                "listing source.commit must equal the pinned RELEASE_COMMIT "
                f"({RELEASE_COMMIT!r}); got {source.get('commit')!r}"
            )
    if enforce_published_pins and RELEASE_PORTAL_SHA256:
        if source.get("portalPluginSHA256") != RELEASE_PORTAL_SHA256:
            raise SubmissionError(
                "listing source.portalPluginSHA256 does not match the published pin"
            )
    identity = listing.get("developerIdentity")
    if not isinstance(identity, dict):
        raise SubmissionError("listing developerIdentity is required")
    if identity.get("displayName") != "Traycer":
        raise SubmissionError("listing developerIdentity.displayName must be Traycer")
    if identity.get("portalLabel") != PORTAL_BUSINESS_IDENTITY:
        raise SubmissionError(
            "listing developerIdentity.portalLabel must be "
            f"{PORTAL_BUSINESS_IDENTITY!r}"
        )
    if identity.get("publisherType") != "business":
        raise SubmissionError("listing developerIdentity.publisherType must be business")
    urls = []
    for key in (
        "websiteURL",
        "supportURL",
        "privacyPolicyURL",
        "termsOfServiceURL",
        "repositoryURL",
        "releaseURL",
    ):
        value = listing.get(key)
        if not isinstance(value, str) or not value.startswith("https://"):
            raise SubmissionError(f"listing.{key} must be a public HTTPS URL")
        urls.append(value)
    if listing.get("logo") != "assets/logo.png":
        raise SubmissionError("listing logo must point to assets/logo.png")
    logo = validate_png_bytes(materials["assets/logo.png"], label="assets/logo.png")
    expected_release = (
        f"https://github.com/anur4ag/pr-completion/releases/tag/{expected_ref}"
    )
    if listing.get("releaseURL") != expected_release:
        raise SubmissionError(f"listing.releaseURL must be {expected_release}")
    return {"urls": urls, "logo": logo, "identity": identity}


def validate_prompts(materials_root: Path) -> int:
    payload = _load_json(materials_root / "starter-prompts.json")
    prompts = payload.get("prompts") if isinstance(payload, dict) else None
    if not isinstance(prompts, list) or len(prompts) != 5:
        raise SubmissionError("starter-prompts.json must contain exactly five prompts")
    if any(not isinstance(prompt, str) or len(prompt.strip()) < 40 for prompt in prompts):
        raise SubmissionError("each starter prompt must be a substantive string")
    if len(set(prompts)) != len(prompts):
        raise SubmissionError("starter prompts must be unique")
    return len(prompts)


def _source_bytes(
    reference: str,
    materials_root: Path,
    members: dict[str, tuple[int, bytes]],
) -> bytes:
    if reference.startswith("tag:"):
        relative = reference.removeprefix("tag:")
        if relative not in members:
            raise SubmissionError(f"case references non-package path: {relative}")
        return members[relative][1]
    if reference.startswith("submission:"):
        relative = reference.removeprefix("submission:")
        if relative not in ALLOWED_MATERIAL_PATHS:
            raise SubmissionError(f"case references non-allowlisted material: {relative}")
        return (materials_root / relative).read_bytes()
    raise SubmissionError(f"case source must use tag: or submission: prefix: {reference}")


def _extract_members(root: Path, members: dict[str, tuple[int, bytes]]) -> None:
    for relative, (mode, data) in members.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def validate_cases(
    materials_root: Path, members: dict[str, tuple[int, bytes]]
) -> list[dict[str, Any]]:
    payload = _load_json(materials_root / "test-cases.json")
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise SubmissionError("test-cases.json must contain a cases array")
    positives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "positive"]
    negatives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "negative"]
    if len(cases) != 8 or len(positives) != 5 or len(negatives) != 3:
        raise SubmissionError(
            "test-cases.json must contain exactly five positive and three negative cases"
        )
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(item, str) for item in ids):
        raise SubmissionError("test case ids must be unique strings")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pr-completion-openai-cases-") as temporary:
        extracted = Path(temporary) / "package"
        _extract_members(extracted, members)
        watcher = extracted / "skills/take-pr-to-completion/scripts/pr_watch.py"
        for case in cases:
            for required in (
                "id",
                "kind",
                "title",
                "userPrompt",
                "expectedBehavior",
                "expectedResultShape",
                "reproduction",
                "checks",
            ):
                if required not in case:
                    raise SubmissionError(f"case {case.get('id')} missing {required}")
            checks = case["checks"]
            if not isinstance(checks, list) or not checks:
                raise SubmissionError(f"case {case['id']} must include validation checks")
            check_results = []
            for check in checks:
                if not isinstance(check, dict):
                    raise SubmissionError(f"case {case['id']} has a non-object check")
                check_type = check.get("type")
                if check_type == "containsAll":
                    text = _source_bytes(check["path"], materials_root, members).decode("utf-8")
                    missing = [value for value in check["values"] if value not in text]
                    if missing:
                        raise SubmissionError(
                            f"case {case['id']} missing required source text: {missing}"
                        )
                    check_results.append({"type": check_type, "status": "passed"})
                elif check_type == "jsonPaths":
                    data = json.loads(_source_bytes(check["path"], materials_root, members))
                    serialized = json.dumps(data, sort_keys=True)
                    missing = [
                        value for value in check["requiredValues"] if value not in serialized
                    ]
                    if missing:
                        raise SubmissionError(
                            f"case {case['id']} missing fixture values: {missing}"
                        )
                    check_results.append({"type": check_type, "status": "passed"})
                elif check_type == "watcherFixture":
                    fixture_data = _source_bytes(check["fixture"], materials_root, members)
                    fixture = Path(temporary) / f"{case['id']}.json"
                    fixture.write_bytes(fixture_data)
                    completed = subprocess.run(
                        [sys.executable, "-B", str(watcher), "--fixture", str(fixture)],
                        cwd=str(extracted),
                        capture_output=True,
                        text=True,
                        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                        check=False,
                    )
                    if completed.returncode not in {0, 20}:
                        raise SubmissionError(
                            f"case {case['id']} watcher exited {completed.returncode}: "
                            f"{completed.stderr.strip()}"
                        )
                    try:
                        observation = json.loads(completed.stdout)
                    except json.JSONDecodeError as error:
                        raise SubmissionError(
                            f"case {case['id']} emitted invalid watcher JSON"
                        ) from error
                    action_types = [
                        action.get("type") for action in observation.get("actions", [])
                    ]
                    if observation.get("state") != check["expectedState"]:
                        raise SubmissionError(
                            f"case {case['id']} state {observation.get('state')!r} != "
                            f"{check['expectedState']!r}"
                        )
                    if action_types != check["expectedActionTypes"]:
                        raise SubmissionError(
                            f"case {case['id']} actions {action_types!r} != "
                            f"{check['expectedActionTypes']!r}"
                        )
                    check_results.append(
                        {
                            "type": check_type,
                            "status": "passed",
                            "state": observation.get("state"),
                            "actionTypes": action_types,
                        }
                    )
                else:
                    raise SubmissionError(
                        f"case {case['id']} has unknown check type {check_type!r}"
                    )
            results.append({"id": case["id"], "kind": case["kind"], "checks": check_results})
    return results


def check_urls(urls: list[str]) -> list[dict[str, Any]]:
    results = []
    for url in urls:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "pr-completion-openai-submission-validator/0.1.1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                status = response.status
                response.read(1)
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SubmissionError(f"public URL check failed for {url}: {error}") from error
        if not 200 <= status < 400:
            raise SubmissionError(f"public URL returned HTTP {status}: {url}")
        results.append({"url": url, "status": status})
    return results


def build_materials_archive(
    out_path: Path, materials: dict[str, bytes], *, archive_version: str
) -> str:
    archive_root = f"pr-completion-{archive_version}-openai-materials"
    members = [
        (f"{archive_root}/{relative}", 0o644, materials[relative])
        for relative in ALLOWED_MATERIAL_PATHS
    ]
    _write_zip(out_path, members)
    return sha256_file(out_path)


def inspect_portal_zip_layout(zip_path: Path) -> dict[str, Any]:
    """Assert ZIP shape: sole top-level directory with supported manifest."""
    with zipfile.ZipFile(zip_path) as archive:
        names = [name for name in archive.namelist() if name and not name.endswith("/")]
    if not names:
        raise SubmissionError("portal ZIP is empty")
    tops = {name.split("/", 1)[0] for name in names}
    if len(tops) != 1:
        raise SubmissionError(
            f"portal ZIP must have exactly one top-level directory (found {sorted(tops)})"
        )
    top = next(iter(tops))
    prefix = f"{top}/"
    relatives = [name[len(prefix) :] for name in names if name.startswith(prefix)]
    if not any(rel in SUPPORTED_MANIFEST_RELATIVES for rel in relatives):
        raise SubmissionError(
            "portal ZIP missing supported plugin manifest under the sole top-level directory"
        )
    return {"topLevel": top, "members": len(relatives)}


def validate_extracted_portal_runtime(
    zip_path: Path, *, fixture_bytes: bytes
) -> dict[str, Any]:
    """Smoke-test the exact minimal archive from an installed-like location."""
    expected_skills = {
        "commit-workspace-changes",
        "gh-review-comment-triage",
        "merge-conflict-resolution",
        "take-pr-to-completion",
    }
    with tempfile.TemporaryDirectory(prefix="pr-completion-portal-runtime-") as temporary:
        root = Path(temporary)
        extracted = root / "installed"
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            for info in infos:
                path = Path(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise SubmissionError(
                        f"portal ZIP contains unsafe extraction path: {info.filename}"
                    )
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise SubmissionError(
                        f"portal ZIP contains unsupported symlink: {info.filename}"
                    )
            archive.extractall(extracted)

        layout = inspect_portal_zip_layout(zip_path)
        plugin_root = extracted / layout["topLevel"]
        discovered_skills = {
            path.parent.name
            for path in (plugin_root / "skills").glob("*/SKILL.md")
            if path.is_file()
        }
        if discovered_skills != expected_skills:
            raise SubmissionError(
                "extracted portal ZIP skill inventory mismatch: "
                f"{sorted(discovered_skills)} != {sorted(expected_skills)}"
            )

        watcher = (
            plugin_root
            / "skills"
            / "take-pr-to-completion"
            / "scripts"
            / "pr_watch.py"
        )
        if not watcher.is_file():
            raise SubmissionError("extracted portal ZIP is missing pr_watch.py")
        fixture = root / "ready-to-merge.json"
        fixture.write_bytes(fixture_bytes)
        run_cwd = root / "third-party-repository"
        run_cwd.mkdir()
        completed = subprocess.run(
            [sys.executable, "-B", str(watcher), "--fixture", str(fixture)],
            cwd=run_cwd,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        if completed.returncode != 0:
            raise SubmissionError(
                "extracted portal watcher smoke failed with exit "
                f"{completed.returncode}: {completed.stderr.strip()}"
            )
        try:
            observation = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise SubmissionError(
                "extracted portal watcher emitted invalid JSON"
            ) from error
        if observation.get("state") != "ready" or observation.get("actions") != []:
            raise SubmissionError(
                "extracted portal watcher did not produce ready with no actions"
            )
        return {
            "skills": sorted(discovered_skills),
            "watcherState": observation["state"],
            "watcherActions": observation["actions"],
        }


def package_submission(
    repo: Path,
    out_dir: Path,
    *,
    probe_urls: bool,
    from_working_tree: bool,
) -> dict[str, Any]:
    repo = repo.resolve()
    materials_root = repo / "submission/openai"
    if from_working_tree:
        source_members = load_working_tree_files(repo)
        package_version = read_working_version(repo)
        source_ref = "working-tree"
        target_ref = f"v{package_version}"
        source_commit = str(_run_git(repo, ["rev-parse", "HEAD"], text=True)).strip()
    else:
        source_commit = resolve_tag(repo)
        source_members = load_tagged_files(repo)
        package_version = RELEASE_VERSION
        source_ref = RELEASE_REF
        target_ref = RELEASE_REF

    if not from_working_tree:
        verify_content_pin(source_members)
    portal_members = select_portal_members(source_members)
    portal_meta = validate_portal_package(
        portal_members, expected_version=package_version
    )
    materials = load_materials(materials_root)
    listing_result = validate_listing(
        materials_root,
        materials,
        expected_version=package_version,
        expected_ref=target_ref,
        enforce_published_pins=not from_working_tree,
    )
    prompt_count = validate_prompts(materials_root)
    case_results = validate_cases(materials_root, source_members)
    url_results = check_urls(listing_result["urls"]) if probe_urls else []

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    portal_zip = out_dir / f"pr-completion-{package_version}-portal-plugin.zip"
    materials_zip = out_dir / f"pr-completion-{package_version}-openai-materials.zip"
    portal_digest = build_portal_plugin_zip(
        portal_zip,
        portal_members,
        archive_version=package_version,
        enforce_content_pin=False,
    )
    layout = inspect_portal_zip_layout(portal_zip)
    runtime_fixture_relative = (
        "skills/take-pr-to-completion/tests/fixtures/ready-to-merge.json"
    )
    if runtime_fixture_relative not in source_members:
        raise SubmissionError(
            f"runtime smoke fixture missing from source: {runtime_fixture_relative}"
        )
    runtime_smoke = validate_extracted_portal_runtime(
        portal_zip,
        fixture_bytes=source_members[runtime_fixture_relative][1],
    )
    materials_digest = build_materials_archive(
        materials_zip, materials, archive_version=package_version
    )

    checksums = out_dir / "SHA256SUMS.txt"
    checksums.write_text(
        f"{materials_digest}  {materials_zip.name}\n"
        f"{portal_digest}  {portal_zip.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "schemaVersion": 1,
        "officialSubmissionDocumentation": OFFICIAL_SUBMISSION_DOC,
        "submissionType": "skills-only",
        "portalUpload": {
            "artifact": portal_zip.name,
            "layout": layout,
            "manifest": portal_meta["manifest"],
            "visuals": portal_meta["visuals"],
            "sha256": portal_digest,
            "bytes": portal_zip.stat().st_size,
            "runtimeSmoke": runtime_smoke,
            "note": (
                "Upload this minimal ZIP on the portal Skills tab. It contains "
                "only the Codex manifest, runtime skill files, and referenced "
                "square visual assets under one top-level plugin directory."
            ),
        },
        "source": {
            "ref": source_ref,
            "commit": source_commit or RELEASE_COMMIT or None,
            "version": package_version,
            "memberCount": portal_meta["memberCount"],
            "portalPluginSHA256": portal_digest,
            "pinnedInstallableSHA256": RELEASE_INSTALLABLE_SHA256 or None,
            "pinnedPortalSHA256": RELEASE_PORTAL_SHA256 or None,
        },
        "materials": {
            "members": len(materials),
            "logo": listing_result["logo"],
            "identity": listing_result["identity"],
            "starterPrompts": prompt_count,
            "positiveCases": sum(item["kind"] == "positive" for item in case_results),
            "negativeCases": sum(item["kind"] == "negative" for item in case_results),
            "materialsSHA256": materials_digest,
        },
        "testCases": case_results,
        "urlChecks": url_results,
        "externalPortalState": {
            "verifiedIdentity": PORTAL_BUSINESS_IDENTITY,
            "appsManagementWrite": "confirmed-by-user",
            "submissionId": None,
            "status": "not-submitted",
        },
    }
    report_path = out_dir / "validation-report.json"
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {
        "portal_zip": portal_zip,
        "portal_sha256": portal_digest,
        "materials_zip": materials_zip,
        "materials_sha256": materials_digest,
        "checksums": checksums,
        "report": report_path,
        "case_count": len(case_results),
        "url_count": len(url_results),
        "absolute_portal_zip": str(portal_zip.resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the portal-compliant OpenAI upload package from the public "
            f"{RELEASE_REF} release (or working tree)."
        )
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git repository containing the release tag or working tree",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("submission-out"),
        help="output directory (default: submission-out)",
    )
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--from-working-tree",
        action="store_true",
        help=(
            "explicitly build from the current working tree (this is the default)"
        ),
    )
    source_group.add_argument(
        "--from-pinned-release",
        action="store_true",
        help=(
            "reconstruct the immutable RELEASE_REF package; intended only after "
            "that release has a portal-compatible manifest and populated pins"
        ),
    )
    parser.add_argument(
        "--check-urls",
        action="store_true",
        help="probe every public listing URL over HTTPS",
    )
    args = parser.parse_args(argv)
    try:
        result = package_submission(
            args.repo,
            args.out_dir,
            probe_urls=args.check_urls,
            from_working_tree=not args.from_pinned_release,
        )
    except SubmissionError as error:
        print(f"OpenAI submission packaging failed: {error}", file=sys.stderr)
        return 1
    print(f"validated {result['case_count']} reproducible test cases")
    if args.check_urls:
        print(f"validated {result['url_count']} public HTTPS URLs")
    print(f"portal: {result['portal_sha256']}  {result['portal_zip']}")
    print(f"portal absolute path: {result['absolute_portal_zip']}")
    print(f"materials: {result['materials_sha256']}  {result['materials_zip']}")
    print(f"checksums: {result['checksums']}")
    print(f"report: {result['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
