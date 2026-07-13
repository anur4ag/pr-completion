#!/usr/bin/env python3
"""Build and validate the immutable OpenAI skills-only submission artifacts.

The portal upload is reconstructed from the exact public v0.1.0 Git objects,
never from the current working tree.  Its byte checksum must match the tagged
skills-source release asset before any output is accepted.

Usage:
  python3 -B scripts/package-openai-submission.py
  python3 -B scripts/package-openai-submission.py --check-urls
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any


RELEASE_VERSION = "0.1.0"
RELEASE_REF = "v0.1.0"
RELEASE_COMMIT = "e56ef4e79f44e295cb17dc66b3b03f622c780f09"
RELEASE_SKILLS_SHA256 = (
    "1cc653d0b5b9879109c31105c98a3d211f484ad409f6b23c6336f255e525536e"
)
SKILLS_ARCHIVE_ROOT = "pr-completion-0.1.0-skills"
MATERIALS_ARCHIVE_ROOT = "pr-completion-0.1.0-openai-materials"
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)

# The complete v0.1.0 skills tree. Any addition, deletion, rename, symlink, or
# mode outside regular 0644/0755 files is a hard failure and requires a new
# versioned release rather than a relaxed allowlist.
ALLOWED_SKILL_PATHS = (
    "skills/commit-workspace-changes/SKILL.md",
    "skills/commit-workspace-changes/agents/openai.yaml",
    "skills/gh-review-comment-triage/SKILL.md",
    "skills/merge-conflict-resolution/SKILL.md",
    "skills/take-pr-to-completion/SKILL.md",
    "skills/take-pr-to-completion/agents/openai.yaml",
    "skills/take-pr-to-completion/scripts/pr_watch.py",
    "skills/take-pr-to-completion/tests/fixtures/blocked.json",
    "skills/take-pr-to-completion/tests/fixtures/conflict.json",
    "skills/take-pr-to-completion/tests/fixtures/empty-checks.json",
    "skills/take-pr-to-completion/tests/fixtures/external-auto-merge-empty-object.json",
    "skills/take-pr-to-completion/tests/fixtures/external-auto-merge-failing-ci.json",
    "skills/take-pr-to-completion/tests/fixtures/external-auto-merge-pending-ci.json",
    "skills/take-pr-to-completion/tests/fixtures/external-auto-merge.json",
    "skills/take-pr-to-completion/tests/fixtures/has-hooks-merge-state.json",
    "skills/take-pr-to-completion/tests/fixtures/incoherent-pass-failure.json",
    "skills/take-pr-to-completion/tests/fixtures/incoherent-pass-in-progress.json",
    "skills/take-pr-to-completion/tests/fixtures/malformed-check-row.json",
    "skills/take-pr-to-completion/tests/fixtures/merged.json",
    "skills/take-pr-to-completion/tests/fixtures/missing-head-sha.json",
    "skills/take-pr-to-completion/tests/fixtures/pending-ci.json",
    "skills/take-pr-to-completion/tests/fixtures/ready-to-merge.json",
    "skills/take-pr-to-completion/tests/fixtures/repository-layout.json",
    "skills/take-pr-to-completion/tests/fixtures/review-comment.json",
    "skills/take-pr-to-completion/tests/fixtures/unknown-check-bucket.json",
    "skills/take-pr-to-completion/tests/fixtures/unstable-merge-state.json",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/README.md",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/allowed-data-only.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-alias-merge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-broad-do-not.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-cli-merge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-gh-alias-set.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-gh-api-merge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-git-force.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-graphql-automerge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-mixed-negation-worry.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-mixed-negation.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-production-marker-bypass.py.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-python-subprocess.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-reordered-automerge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-rest-merge.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-test-helper-no-marker.sh.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-test-helper-with-marker.sh.txt",
    "skills/take-pr-to-completion/tests/safety-scanner-fixtures/payload-wrapped-cli.txt",
    "skills/take-pr-to-completion/tests/test_pr_watch.py",
)

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


class SubmissionError(Exception):
    """Submission input is unsafe, incomplete, or not the pinned release."""


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
    if CACHEBUSTER_RE.search(data):
        raise SubmissionError(f"timestamp cachebuster found in {relative}")
    for pattern in SECRET_PATTERNS:
        if pattern.search(data):
            raise SubmissionError(f"credential-like content found in {relative}")


def resolve_tag(repo: Path) -> str:
    resolved = str(
        _run_git(repo, ["rev-parse", "--verify", f"refs/tags/{RELEASE_REF}^{{commit}}"], text=True)
    ).strip()
    if resolved != RELEASE_COMMIT:
        raise SubmissionError(
            f"{RELEASE_REF} resolves to {resolved}, expected {RELEASE_COMMIT}; refusing retag/drift"
        )
    return resolved


def load_tagged_skills(repo: Path) -> dict[str, tuple[int, bytes]]:
    commit = resolve_tag(repo)
    raw = bytes(_run_git(repo, ["ls-tree", "-r", "-z", "--full-tree", commit, "--", "skills"]))
    entries: dict[str, tuple[int, bytes]] = {}
    discovered: list[str] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode_raw, kind_raw, object_id = metadata.split(b" ", 2)
        relative = raw_path.decode("utf-8")
        discovered.append(relative)
        if kind_raw != b"blob" or mode_raw not in {b"100644", b"100755"}:
            raise SubmissionError(
                f"tagged skill entry is not an allowed regular file: {relative} "
                f"({mode_raw.decode()} {kind_raw.decode()})"
            )
        data = bytes(_run_git(repo, ["cat-file", "blob", object_id.decode("ascii")]))
        _scan_bytes(relative, data)
        entries[relative] = (0o755 if mode_raw == b"100755" else 0o644, data)

    expected = list(ALLOWED_SKILL_PATHS)
    if discovered != expected:
        missing = sorted(set(expected) - set(discovered))
        extra = sorted(set(discovered) - set(expected))
        raise SubmissionError(
            f"tagged skill allowlist mismatch; missing={missing}, extra={extra}, "
            "or member order differs"
        )
    return entries


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


def build_skills_archive(
    out_path: Path, tagged: dict[str, tuple[int, bytes]]
) -> str:
    members = [
        (f"{SKILLS_ARCHIVE_ROOT}/{relative}", tagged[relative][0], tagged[relative][1])
        for relative in ALLOWED_SKILL_PATHS
    ]
    _write_zip(out_path, members)
    digest = sha256_file(out_path)
    if digest != RELEASE_SKILLS_SHA256:
        out_path.unlink(missing_ok=True)
        raise SubmissionError(
            f"reconstructed tagged skills checksum {digest} does not match "
            f"published {RELEASE_SKILLS_SHA256}"
        )
    return digest


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


def validate_png(data: bytes) -> dict[str, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SubmissionError("assets/logo.png is not a valid PNG with an IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if width != height or width < 512 or width > 2048:
        raise SubmissionError(
            f"assets/logo.png must be square and 512-2048px (got {width}x{height})"
        )
    return {"width": width, "height": height}


def validate_listing(materials_root: Path, materials: dict[str, bytes]) -> dict[str, Any]:
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
    expected_source = {
        "tag": RELEASE_REF,
        "commit": RELEASE_COMMIT,
        "skillsSourceSHA256": RELEASE_SKILLS_SHA256,
    }
    if source != expected_source:
        raise SubmissionError("listing source identity does not match the pinned release")
    identity = listing.get("developerIdentity")
    if not isinstance(identity, dict) or identity.get("portalVerification") != "requires-confirmation":
        raise SubmissionError("publisher verification must remain requires-confirmation until observed")
    if identity.get("displayName") != "Anurag Sharma" or identity.get("publisherType") != "individual":
        raise SubmissionError("listing developer identity does not match the public individual publisher")
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
    logo = validate_png(materials["assets/logo.png"])
    return {"urls": urls, "logo": logo}


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
    tagged: dict[str, tuple[int, bytes]],
) -> bytes:
    if reference.startswith("tag:"):
        relative = reference.removeprefix("tag:")
        if relative not in tagged:
            raise SubmissionError(f"case references non-allowlisted tagged path: {relative}")
        return tagged[relative][1]
    if reference.startswith("submission:"):
        relative = reference.removeprefix("submission:")
        if relative not in ALLOWED_MATERIAL_PATHS:
            raise SubmissionError(f"case references non-allowlisted material: {relative}")
        return (materials_root / relative).read_bytes()
    raise SubmissionError(f"case source must use tag: or submission: prefix: {reference}")


def _extract_tagged_tree(
    root: Path, tagged: dict[str, tuple[int, bytes]]
) -> None:
    for relative in ALLOWED_SKILL_PATHS:
        mode, data = tagged[relative]
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)


def validate_cases(
    materials_root: Path, tagged: dict[str, tuple[int, bytes]]
) -> list[dict[str, Any]]:
    payload = _load_json(materials_root / "test-cases.json")
    cases = payload.get("cases") if isinstance(payload, dict) else None
    if not isinstance(cases, list):
        raise SubmissionError("test-cases.json must contain a cases array")
    positives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "positive"]
    negatives = [case for case in cases if isinstance(case, dict) and case.get("kind") == "negative"]
    if len(cases) != 8 or len(positives) != 5 or len(negatives) != 3:
        raise SubmissionError("test-cases.json must contain exactly five positive and three negative cases")
    ids = [case.get("id") for case in cases]
    if len(set(ids)) != len(ids) or any(not isinstance(item, str) for item in ids):
        raise SubmissionError("test case ids must be unique strings")

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="pr-completion-openai-cases-") as temporary:
        extracted = Path(temporary) / "tagged"
        _extract_tagged_tree(extracted, tagged)
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
                    text = _source_bytes(check["path"], materials_root, tagged).decode("utf-8")
                    missing = [value for value in check["values"] if value not in text]
                    if missing:
                        raise SubmissionError(f"case {case['id']} missing required source text: {missing}")
                    check_results.append({"type": check_type, "status": "passed"})
                elif check_type == "jsonPaths":
                    data = json.loads(_source_bytes(check["path"], materials_root, tagged))
                    serialized = json.dumps(data, sort_keys=True)
                    missing = [value for value in check["requiredValues"] if value not in serialized]
                    if missing:
                        raise SubmissionError(f"case {case['id']} missing fixture values: {missing}")
                    check_results.append({"type": check_type, "status": "passed"})
                elif check_type == "watcherFixture":
                    fixture_data = _source_bytes(check["fixture"], materials_root, tagged)
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
                            f"case {case['id']} watcher exited {completed.returncode}: {completed.stderr.strip()}"
                        )
                    try:
                        observation = json.loads(completed.stdout)
                    except json.JSONDecodeError as error:
                        raise SubmissionError(f"case {case['id']} emitted invalid watcher JSON") from error
                    action_types = [action.get("type") for action in observation.get("actions", [])]
                    if observation.get("state") != check["expectedState"]:
                        raise SubmissionError(
                            f"case {case['id']} state {observation.get('state')!r} != {check['expectedState']!r}"
                        )
                    if action_types != check["expectedActionTypes"]:
                        raise SubmissionError(
                            f"case {case['id']} actions {action_types!r} != {check['expectedActionTypes']!r}"
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
                    raise SubmissionError(f"case {case['id']} has unknown check type {check_type!r}")
            results.append({"id": case["id"], "kind": case["kind"], "checks": check_results})
    return results


def check_urls(urls: list[str]) -> list[dict[str, Any]]:
    results = []
    for url in urls:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "pr-completion-openai-submission-validator/0.1.0"},
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
    out_path: Path, materials: dict[str, bytes]
) -> str:
    members = [
        (f"{MATERIALS_ARCHIVE_ROOT}/{relative}", 0o644, materials[relative])
        for relative in ALLOWED_MATERIAL_PATHS
    ]
    _write_zip(out_path, members)
    return sha256_file(out_path)


def package_submission(repo: Path, out_dir: Path, *, probe_urls: bool) -> dict[str, Any]:
    repo = repo.resolve()
    materials_root = repo / "submission/openai"
    tagged = load_tagged_skills(repo)
    materials = load_materials(materials_root)
    listing_result = validate_listing(materials_root, materials)
    prompt_count = validate_prompts(materials_root)
    case_results = validate_cases(materials_root, tagged)
    url_results = check_urls(listing_result["urls"]) if probe_urls else []

    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    skills_zip = out_dir / f"pr-completion-{RELEASE_VERSION}-openai-skills.zip"
    materials_zip = out_dir / f"pr-completion-{RELEASE_VERSION}-openai-materials.zip"
    skills_digest = build_skills_archive(skills_zip, tagged)
    materials_digest = build_materials_archive(materials_zip, materials)

    checksums = out_dir / "SHA256SUMS.txt"
    checksums.write_text(
        f"{materials_digest}  {materials_zip.name}\n"
        f"{skills_digest}  {skills_zip.name}\n",
        encoding="utf-8",
        newline="\n",
    )
    report = {
        "schemaVersion": 1,
        "officialSubmissionDocumentation": OFFICIAL_SUBMISSION_DOC,
        "submissionType": "skills-only",
        "source": {
            "ref": RELEASE_REF,
            "commit": RELEASE_COMMIT,
            "skillMembers": len(tagged),
            "skillsSHA256": skills_digest,
        },
        "materials": {
            "members": len(materials),
            "logo": listing_result["logo"],
            "starterPrompts": prompt_count,
            "positiveCases": sum(item["kind"] == "positive" for item in case_results),
            "negativeCases": sum(item["kind"] == "negative" for item in case_results),
            "materialsSHA256": materials_digest,
        },
        "testCases": case_results,
        "urlChecks": url_results,
        "externalPortalState": {
            "individualIdentityVerified": "unconfirmed",
            "appsManagementWrite": "unconfirmed",
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
        "skills_zip": skills_zip,
        "skills_sha256": skills_digest,
        "materials_zip": materials_zip,
        "materials_sha256": materials_digest,
        "checksums": checksums,
        "report": report_path,
        "case_count": len(case_results),
        "url_count": len(url_results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the pinned v0.1.0 OpenAI skills-only submission package."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Git repository containing the immutable v0.1.0 tag",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("submission-out"),
        help="ignored output directory (default: submission-out)",
    )
    parser.add_argument(
        "--check-urls",
        action="store_true",
        help="probe every public listing URL over HTTPS",
    )
    args = parser.parse_args(argv)
    try:
        result = package_submission(args.repo, args.out_dir, probe_urls=args.check_urls)
    except SubmissionError as error:
        print(f"OpenAI submission packaging failed: {error}", file=sys.stderr)
        return 1
    print(f"validated {result['case_count']} reproducible test cases")
    if args.check_urls:
        print(f"validated {result['url_count']} public HTTPS URLs")
    print(f"skills: {result['skills_sha256']}  {result['skills_zip']}")
    print(f"materials: {result['materials_sha256']}  {result['materials_zip']}")
    print(f"checksums: {result['checksums']}")
    print(f"report: {result['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
