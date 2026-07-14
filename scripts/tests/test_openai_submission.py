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
        # Use \u2014 so Windows source decoding cannot corrupt the portal label.
        self.assertEqual(
            listing["identity"]["portalLabel"], "Business \u2014 Traycer"
        )
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
        listing = json.loads(
            (ROOT / "submission/openai/listing.json").read_text(encoding="utf-8")
        )
        self.assertEqual(listing["source"]["tag"], submission.RELEASE_REF)
        self.assertEqual(listing["source"]["version"], submission.RELEASE_VERSION)
        self.assertEqual(listing["developerIdentity"]["displayName"], "Traycer")
        self.assertEqual(
            listing["developerIdentity"]["portalLabel"],
            "Business \u2014 Traycer",
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

    def test_working_tree_portal_build_matches_contemporaneous_package_release(
        self,
    ) -> None:
        """Working-tree path must not demand the immutable tagged checksum.

        Post-tag main may differ from v0.1.1; only package-release identity
        is required for --from-working-tree.
        """
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
            # Post-tag main can (and currently does) differ from the published
            # pin; working-tree builds must still succeed.
            if submission.RELEASE_PLUGIN_SHA256:
                # Either equal (clean tree at tag) or unequal (main moved on);
                # both are valid for working-tree mode.
                self.assertIsInstance(digest, str)
            layout = submission.inspect_portal_zip_layout(result["portal_zip"])
            self.assertEqual(
                layout["topLevel"], f"pr-completion-{submission.RELEASE_VERSION}"
            )
            with zipfile.ZipFile(result["portal_zip"]) as archive:
                names = archive.namelist()
            self.assertTrue(
                any(name.endswith(".codex-plugin/plugin.json") for name in names)
            )
            self.assertTrue(
                any(name.endswith("assets/traycer-icon.png") for name in names)
            )

    def test_working_tree_build_skips_published_checksum_enforcement(self) -> None:
        """Direct unit guard: enforce_published_checksum=False ignores pin."""
        members = self._base_members()
        # Deliberately incomplete members produce a digest != published pin.
        with tempfile.TemporaryDirectory(prefix="openai-no-pin-") as temporary:
            out = Path(temporary) / "portal.zip"
            digest = submission.build_portal_plugin_zip(
                out,
                members,
                enforce_published_checksum=False,
            )
            self.assertEqual(len(digest), 64)
            if submission.RELEASE_PLUGIN_SHA256:
                # Minimal member set cannot match the full published release pin.
                self.assertNotEqual(digest, submission.RELEASE_PLUGIN_SHA256)

    def test_immutable_tag_reconstruction_enforces_published_checksum(self) -> None:
        require = os.environ.get("PR_COMPLETION_REQUIRE_RELEASE_TAG") == "1"
        try:
            members = submission.load_tagged_files(ROOT)
        except submission.SubmissionError as error:
            if require:
                self.fail(
                    f"immutable release tag {submission.RELEASE_REF} is required "
                    f"but unavailable: {error}"
                )
            message = str(error)
            if (
                "unknown revision" in message
                or "Needed a single revision" in message
                or "failed" in message
            ):
                self.skipTest(f"release tag unavailable locally: {error}")
            raise
        meta = submission.validate_portal_package(members)
        self.assertEqual(meta["version"], submission.RELEASE_VERSION)
        with tempfile.TemporaryDirectory(prefix="openai-tagged-") as temporary:
            out = Path(temporary) / "portal.zip"
            digest = submission.build_portal_plugin_zip(
                out,
                members,
                enforce_published_checksum=True,
            )
            self.assertEqual(len(digest), 64)
            self.assertTrue(submission.RELEASE_PLUGIN_SHA256)
            self.assertEqual(digest, submission.RELEASE_PLUGIN_SHA256)
            # Full package_submission default path (tag) also succeeds.
            full = submission.package_submission(
                ROOT,
                Path(temporary) / "full",
                probe_urls=False,
                from_working_tree=False,
            )
            self.assertEqual(full["portal_sha256"], submission.RELEASE_PLUGIN_SHA256)


class ListingPinNegativeTests(unittest.TestCase):
    def _materials_with_source(self, **source_overrides: object) -> Path:
        temporary = tempfile.TemporaryDirectory(prefix="openai-listing-pin-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "openai"
        shutil.copytree(ROOT / "submission/openai", root)
        listing_path = root / "listing.json"
        listing = json.loads(listing_path.read_text(encoding="utf-8"))
        listing["source"].update(source_overrides)
        listing_path.write_text(
            json.dumps(listing, indent=2) + "\n", encoding="utf-8"
        )
        return root

    def test_empty_commit_rejected_when_release_commit_pinned(self) -> None:
        self.assertTrue(submission.RELEASE_COMMIT)
        root = self._materials_with_source(commit="")
        materials = submission.load_materials(root)
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_listing(root, materials)
        self.assertIn("source.commit", str(ctx.exception))

    def test_wrong_commit_rejected_when_release_commit_pinned(self) -> None:
        self.assertTrue(submission.RELEASE_COMMIT)
        root = self._materials_with_source(commit="0" * 40)
        materials = submission.load_materials(root)
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_listing(root, materials)
        self.assertIn("source.commit", str(ctx.exception))

    def test_wrong_portal_plugin_checksum_rejected(self) -> None:
        self.assertTrue(submission.RELEASE_PLUGIN_SHA256)
        root = self._materials_with_source(portalPluginSHA256="0" * 64)
        materials = submission.load_materials(root)
        with self.assertRaises(submission.SubmissionError) as ctx:
            submission.validate_listing(root, materials)
        self.assertIn("portalPluginSHA256", str(ctx.exception))


class ImmutableTagDriftNegativeTests(unittest.TestCase):
    def test_retag_mismatch_is_rejected(self) -> None:
        """Tag resolving to a different commit than RELEASE_COMMIT must fail."""
        if not submission.RELEASE_COMMIT:
            self.skipTest("RELEASE_COMMIT not pinned")
        require = os.environ.get("PR_COMPLETION_REQUIRE_RELEASE_TAG") == "1"
        # Patch RELEASE_COMMIT to a wrong value while keeping real tag resolution.
        original = submission.RELEASE_COMMIT
        submission.RELEASE_COMMIT = "deadbeef" * 5
        try:
            with self.assertRaises(submission.SubmissionError) as ctx:
                submission.resolve_tag(ROOT)
            self.assertIn("refusing retag/drift", str(ctx.exception))
        except submission.SubmissionError as error:
            # resolve_tag may fail earlier if tag missing; re-raise only if required.
            if "refusing retag/drift" not in str(error):
                if require:
                    raise
                self.skipTest(f"tag unavailable: {error}")
            raise
        finally:
            submission.RELEASE_COMMIT = original

    def test_published_checksum_mismatch_is_rejected_on_tag_path(self) -> None:
        members = {
            "VERSION": (0o644, b"0.1.1\n"),
            ".codex-plugin/plugin.json": (
                0o644,
                b'{"name":"pr-completion","version":"0.1.1"}\n',
            ),
        }
        original = submission.RELEASE_PLUGIN_SHA256
        submission.RELEASE_PLUGIN_SHA256 = "a" * 64
        try:
            with tempfile.TemporaryDirectory(prefix="openai-checksum-neg-") as temporary:
                out = Path(temporary) / "portal.zip"
                with self.assertRaises(submission.SubmissionError) as ctx:
                    submission.build_portal_plugin_zip(
                        out,
                        members,
                        enforce_published_checksum=True,
                    )
                self.assertIn("does not match published pin", str(ctx.exception))
        finally:
            submission.RELEASE_PLUGIN_SHA256 = original


if __name__ == "__main__":
    unittest.main()
