#!/usr/bin/env python3
"""Tests for the immutable OpenAI skills-only submission packager."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
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
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


submission = load_module()


class OpenAISubmissionMaterialTests(unittest.TestCase):
    def test_materials_and_exact_case_counts_validate(self) -> None:
        materials_root = ROOT / "submission/openai"
        materials = submission.load_materials(materials_root)
        listing = submission.validate_listing(materials_root, materials)
        self.assertEqual(listing["logo"], {"width": 1024, "height": 1024})
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
            b"0.1.0+codex.20260714123456",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(submission.SubmissionError):
                    submission._scan_bytes("skills/example/SKILL.md", payload)

    def test_pinned_identity_constants_are_consistent_with_listing(self) -> None:
        listing = json.loads((ROOT / "submission/openai/listing.json").read_text())
        self.assertEqual(listing["source"]["tag"], submission.RELEASE_REF)
        self.assertEqual(listing["source"]["commit"], submission.RELEASE_COMMIT)
        self.assertEqual(
            listing["source"]["skillsSourceSHA256"],
            submission.RELEASE_SKILLS_SHA256,
        )
        self.assertEqual(
            listing["developerIdentity"]["portalVerification"],
            "requires-confirmation",
        )

    def test_tagged_integration_when_release_ref_is_available(self) -> None:
        try:
            tagged = submission.load_tagged_skills(ROOT)
        except submission.SubmissionError as error:
            if "unknown revision" in str(error) or "Needed a single revision" in str(error):
                self.skipTest("CI checkout does not contain v0.1.0 tag")
            raise
        with tempfile.TemporaryDirectory(prefix="openai-package-") as temporary:
            out = Path(temporary) / "skills.zip"
            digest = submission.build_skills_archive(out, tagged)
            self.assertEqual(digest, submission.RELEASE_SKILLS_SHA256)
            cases = submission.validate_cases(ROOT / "submission/openai", tagged)
            self.assertEqual(len(cases), 8)


if __name__ == "__main__":
    unittest.main()
