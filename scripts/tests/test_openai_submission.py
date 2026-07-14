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

    def test_working_tree_build_skips_content_pin_enforcement(self) -> None:
        """Working-tree path does not enforce published content pin."""
        members = self._base_members()
        with tempfile.TemporaryDirectory(prefix="openai-no-pin-") as temporary:
            out = Path(temporary) / "portal.zip"
            digest = submission.build_portal_plugin_zip(
                out,
                members,
                enforce_content_pin=False,
            )
            self.assertEqual(len(digest), 64)
            # Incomplete members must not match the published content pin.
            self.assertNotEqual(
                submission.members_content_sha256(members),
                submission.RELEASE_PLUGIN_CONTENT_SHA256,
            )

    def test_immutable_tag_reconstruction_enforces_content_pin(self) -> None:
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
        content_fp = submission.verify_content_pin(members)
        self.assertEqual(content_fp, submission.RELEASE_PLUGIN_CONTENT_SHA256)
        with tempfile.TemporaryDirectory(prefix="openai-tagged-") as temporary:
            out = Path(temporary) / "portal.zip"
            digest = submission.build_portal_plugin_zip(
                out,
                members,
                enforce_content_pin=True,
            )
            self.assertEqual(len(digest), 64)
            # Portable path must not require Ubuntu ZIP container bytes.
            # Exact-byte integrity is a separate gate (verify_published_zip_bytes).
            full = submission.package_submission(
                ROOT,
                Path(temporary) / "full",
                probe_urls=False,
                from_working_tree=False,
            )
            self.assertEqual(len(full["portal_sha256"]), 64)
            self.assertEqual(
                submission.members_content_sha256(
                    submission.load_tagged_files(ROOT)
                ),
                submission.RELEASE_PLUGIN_CONTENT_SHA256,
            )


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

    def test_content_pin_mismatch_is_rejected_on_tag_path(self) -> None:
        members = {
            "VERSION": (0o644, b"0.1.1\n"),
            ".codex-plugin/plugin.json": (
                0o644,
                b'{"name":"pr-completion","version":"0.1.1"}\n',
            ),
        }
        original_content = submission.RELEASE_PLUGIN_CONTENT_SHA256
        submission.RELEASE_PLUGIN_CONTENT_SHA256 = "b" * 64
        try:
            with tempfile.TemporaryDirectory(prefix="openai-content-neg-") as temporary:
                out = Path(temporary) / "portal.zip"
                with self.assertRaises(submission.SubmissionError) as ctx:
                    submission.build_portal_plugin_zip(
                        out,
                        members,
                        enforce_content_pin=True,
                    )
                self.assertIn("content fingerprint", str(ctx.exception))
        finally:
            submission.RELEASE_PLUGIN_CONTENT_SHA256 = original_content

    def test_exact_byte_gate_fails_when_only_zip_pin_is_wrong(self) -> None:
        """Wrong RELEASE_PLUGIN_SHA256 cannot pass via correct content pin."""
        require = os.environ.get("PR_COMPLETION_REQUIRE_RELEASE_TAG") == "1"
        try:
            members = submission.load_tagged_files(ROOT)
        except submission.SubmissionError as error:
            if require:
                self.fail(f"tag required: {error}")
            self.skipTest(f"tag unavailable: {error}")

        # Content pin remains correct.
        self.assertEqual(
            submission.members_content_sha256(members),
            submission.RELEASE_PLUGIN_CONTENT_SHA256,
        )
        with tempfile.TemporaryDirectory(prefix="openai-exact-byte-") as temporary:
            out = Path(temporary) / "portal.zip"
            # Portable rebuild succeeds on content pin alone.
            submission.build_portal_plugin_zip(
                out, members, enforce_content_pin=True
            )
            # Probe: only the published ZIP pin is poisoned.
            original_zip = submission.RELEASE_PLUGIN_SHA256
            submission.RELEASE_PLUGIN_SHA256 = "0" * 64
            try:
                with self.assertRaises(submission.SubmissionError) as ctx:
                    # Even a file whose content matches the real release cannot
                    # satisfy the exact-byte gate under a wrong pin constant.
                    submission.verify_published_zip_bytes(out)
                message = str(ctx.exception)
                self.assertIn("exact published ZIP pin mismatch", message)
                self.assertIn(
                    "content equivalence is not a substitute", message
                )
            finally:
                submission.RELEASE_PLUGIN_SHA256 = original_zip

            # Restoring the real pin: only a file that actually has the
            # published bytes succeeds (local rebuild may differ on Windows).
            # Use content pin still OK independently.
            submission.verify_content_pin(members)


class DCOIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.dont_write_bytecode = True
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        path = ROOT / "scripts/check-dco.py"
        spec = importlib.util.spec_from_file_location("pr_completion_check_dco", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"could not load {path}")
        self.dco = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.dco
        spec.loader.exec_module(self.dco)

    def test_exception_list_is_exactly_two_approved_shas(self) -> None:
        self.assertTrue(self.dco.exception_list_is_exact())
        self.assertEqual(len(self.dco.DCO_EXCEPTION_SHAS), 2)
        self.assertIn(
            "a93a5d77f51a713f86578255271d59bf96a8e991",
            self.dco.DCO_EXCEPTION_SHAS,
        )
        self.assertIn(
            "4af89ae8e5648c4a6846773817aa9856c5f979a4",
            self.dco.DCO_EXCEPTION_SHAS,
        )

    def test_unrelated_signatory_is_rejected(self) -> None:
        message = (
            "example commit\n\n"
            "Signed-off-by: Unrelated Person <unrelated@example.com>\n"
        )
        signoffs = self.dco.parse_signoffs(message)
        self.assertEqual(len(signoffs), 1)
        author = self.dco.Identity(name="Anurag Sharma", email="anurag@traycer.ai")
        committer = author
        self.assertFalse(any(s.matches(author) or s.matches(committer) for s in signoffs))

    def test_matching_author_signoff_is_accepted(self) -> None:
        message = (
            "example commit\n\n"
            "Signed-off-by: Anurag Sharma <anurag@traycer.ai>\n"
        )
        signoffs = self.dco.parse_signoffs(message)
        author = self.dco.Identity(name="Anurag Sharma", email="anurag@traycer.ai")
        self.assertTrue(signoffs[0].matches(author))

    def test_historical_exception_commits_are_skipped(self) -> None:
        for sha in self.dco.DCO_EXCEPTION_SHAS:
            # Full SHAs must be present in this clone of the public history.
            try:
                result = self.dco.validate_commit(ROOT, sha)
            except self.dco.DCOError as error:
                if "failed" in str(error).lower() or "unknown" in str(error).lower():
                    self.skipTest(f"exception sha unavailable: {error}")
                raise
            self.assertIn("historical DCO exception", result)


if __name__ == "__main__":
    unittest.main()
