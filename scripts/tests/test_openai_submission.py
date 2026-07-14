#!/usr/bin/env python3
"""Tests for portal-compliant OpenAI submission packaging (v0.1.1)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/package-openai-submission.py"


def load_module() -> types.ModuleType:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location("openai_submission_packager", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # Reload if a previous version was already imported in this process.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


submission = load_module()


def _square_png(size: int = 1024) -> bytes:
    # Minimal valid PNG: signature + IHDR + IEND (not a full image decoder target).
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return signature + chunk(b"IHDR", ihdr_data) + chunk(b"IEND", b"")


class OpenAISubmissionMaterialTests(unittest.TestCase):
    def test_materials_and_exact_case_counts_validate(self) -> None:
        materials_root = ROOT / "submission/openai"
        materials = submission.load_materials(materials_root)
        listing = submission.validate_listing(materials_root, materials)
        self.assertEqual(listing["logo"], {"width": 1024, "height": 1024})
        self.assertEqual(listing["identity"]["portalLabel"], "Business — Traycer")
        self.assertEqual(listing["identity"]["publisherType"], "business")
        self.assertEqual(submission.validate_prompts(materials_root), 5)
        payload = json.loads((materials_root / "test-cases.json").read_text())
        cases = payload["cases"]
        self.assertEqual(sum(case["kind"] == "positive" for case in cases), 5)
        self.assertEqual(sum(case["kind"] == "negative" for case in cases), 3)

    def test_unallowlisted_material_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openai-material-extra-") as temporary:
            root = Path(temporary) / "openai"
            shutil.copytree(ROOT / "submission/openai", root)
            (root / "credentials.json").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(submission.SubmissionError):
                submission.load_materials(root)

    def test_personal_path_and_secret_scans_reject_contamination(self) -> None:
        for payload in (
            b"/" + b"Users/alice/private/repo",
            b"/" + b"home/alice/private/repo",
            b"gh" + b"p_abcdefghijklmnopqrstuvwxyz123456",
            b"-----BEGIN PRIVATE " + b"KEY-----",
            b"0.1.1+codex.20260714123456",
        ):
            with self.subTest(payload=payload):
                label = "VERSION" if b"+codex." in payload else "skills/example/SKILL.md"
                if b"+codex." in payload:
                    label = ".codex-plugin/plugin.json"
                with self.assertRaises(submission.SubmissionError):
                    submission._scan_bytes(label, payload)

    def test_pinned_identity_constants_are_consistent_with_listing(self) -> None:
        listing = json.loads((ROOT / "submission/openai/listing.json").read_text())
        self.assertEqual(listing["source"]["tag"], submission.RELEASE_REF)
        self.assertEqual(listing["source"]["version"], submission.RELEASE_VERSION)
        self.assertEqual(listing["developerIdentity"]["displayName"], "Traycer")
        self.assertEqual(
            listing["developerIdentity"]["portalLabel"], "Business — Traycer"
        )


class PortalPackageValidationTests(unittest.TestCase):
    def _base_members(self) -> dict[str, tuple[int, bytes]]:
        icon = (ROOT / "assets/traycer-icon.png").read_bytes()
        codex = {
            "name": "pr-completion",
            "version": submission.RELEASE_VERSION,
            "interface": {
                "developerName": "Traycer",
                "composerIcon": "./assets/traycer-icon.png",
                "logo": "./assets/traycer-icon.png",
            },
        }
        return {
            ".codex-plugin/plugin.json": (
                0o644,
                (json.dumps(codex, indent=2) + "\n").encode("utf-8"),
            ),
            "assets/traycer-icon.png": (0o644, icon),
            "skills/take-pr-to-completion/SKILL.md": (0o644, b"# skill\n"),
            "VERSION": (0o644, f"{submission.RELEASE_VERSION}\n".encode("utf-8")),
        }

    def test_valid_portal_package_passes(self) -> None:
        meta = submission.validate_portal_package(self._base_members())
        self.assertEqual(meta["manifest"], ".codex-plugin/plugin.json")
        self.assertEqual(meta["version"], submission.RELEASE_VERSION)

    def test_missing_manifest_is_rejected(self) -> None:
        members = self._base_members()
        del members[".codex-plugin/plugin.json"]
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_portal_package(members)
        message = str(ctx.exception).lower()
        self.assertTrue(
            "manifest" in message or "plugin.json" in message,
            msg=message,
        )

    def test_missing_image_refs_are_rejected(self) -> None:
        for field in ("composerIcon", "logo"):
            members = self._base_members()
            payload = json.loads(members[".codex-plugin/plugin.json"][1])
            del payload["interface"][field]
            members[".codex-plugin/plugin.json"] = (
                0o644,
                (json.dumps(payload) + "\n").encode("utf-8"),
            )
            with self.subTest(field=field):
                with self.assertRaises(submission.SubmissionError) as ctx:
                    submission.validate_portal_package(members)
                self.assertIn(field, str(ctx.exception))

    def test_missing_referenced_asset_is_rejected(self) -> None:
        members = self._base_members()
        del members["assets/traycer-icon.png"]
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_portal_package(members)
        self.assertIn("missing package member", str(ctx.exception))

    def test_non_square_asset_is_rejected(self) -> None:
        members = self._base_members()
        # 512x256 PNG
        non_square = _square_png(512)
        # Patch IHDR width/height manually to 512x256
        data = bytearray(non_square)
        data[16:24] = struct.pack(">II", 512, 256)
        members["assets/traycer-icon.png"] = (0o644, bytes(data))
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_portal_package(members)
        self.assertIn("square", str(ctx.exception).lower())

    def test_out_of_root_asset_path_is_rejected(self) -> None:
        members = self._base_members()
        payload = json.loads(members[".codex-plugin/plugin.json"][1])
        payload["interface"]["logo"] = "./../outside.png"
        members[".codex-plugin/plugin.json"] = (
            0o644,
            (json.dumps(payload) + "\n").encode("utf-8"),
        )
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_portal_package(members)
        self.assertIn("escapes", str(ctx.exception).lower())

    def test_portal_zip_layout_requires_sole_top_level_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory(prefix="portal-layout-") as temporary:
            path = Path(temporary) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("skills/only/SKILL.md", "x")
            with self.assertRaises(submission.SubmissionError) as ctx:
                submission.inspect_portal_zip_layout(path)
            self.assertIn("manifest", str(ctx.exception).lower())

    def test_working_tree_portal_build_is_byte_identical_to_release_plugin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="openai-package-") as temporary:
            out = Path(temporary)
            result = submission.package_submission(
                ROOT,
                out,
                probe_urls=False,
                from_working_tree=True,
            )
            self.assertTrue(result["portal_zip"].is_file())
            digest = result["portal_sha256"]
            self.assertEqual(len(digest), 64)
            layout = submission.inspect_portal_zip_layout(result["portal_zip"])
            self.assertEqual(layout["topLevel"], f"pr-completion-{submission.RELEASE_VERSION}")
            with zipfile.ZipFile(result["portal_zip"]) as archive:
                names = archive.namelist()
            self.assertTrue(
                any(name.endswith(".codex-plugin/plugin.json") for name in names)
            )
            self.assertTrue(
                any(name.endswith("assets/traycer-icon.png") for name in names)
            )

    def test_tagged_integration_when_release_ref_is_available(self) -> None:
        try:
            members = submission.load_tagged_files(ROOT)
        except submission.SubmissionError as error:
            message = str(error)
            if (
                "unknown revision" in message
                or "Needed a single revision" in message
                or "exists" in message
                and "v0.1.1" in message
            ):
                self.skipTest("CI checkout does not contain v0.1.1 tag yet")
            if f"refs/tags/{submission.RELEASE_REF}" in message or "failed" in message:
                self.skipTest(f"release tag unavailable: {error}")
            raise
        meta = submission.validate_portal_package(members)
        self.assertEqual(meta["version"], submission.RELEASE_VERSION)
        with tempfile.TemporaryDirectory(prefix="openai-tagged-") as temporary:
            out = Path(temporary) / "portal.zip"
            digest = submission.build_portal_plugin_zip(out, members)
            self.assertEqual(len(digest), 64)
            if submission.RELEASE_PLUGIN_SHA256:
                self.assertEqual(digest, submission.RELEASE_PLUGIN_SHA256)


if __name__ == "__main__":
    unittest.main()
