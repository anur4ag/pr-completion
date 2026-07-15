#!/usr/bin/env python3
"""Focused regressions for generated documentation accessibility and routes."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent


def load_script(module_name: str, path: Path) -> types.ModuleType:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


build_docs = load_script("pr_completion_build_docs", SCRIPTS / "build-docs.py")
check_docs = load_script("pr_completion_check_docs", SCRIPTS / "check-docs-links.py")


class GeneratedMarkupTests(unittest.TestCase):
    def test_installation_heading_ids_are_unique_and_deterministic(self) -> None:
        source = (ROOT / "docs" / "installation.md").read_text(encoding="utf-8")
        rendered = build_docs.markdown_to_html(source)
        ids = re.findall(r'<h[1-6] id="([^"]+)"', rendered)

        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("install", ids)
        self.assertIn("install-2", ids)
        self.assertIn("pin-v0-2-1-2", ids)
        self.assertIn("update-2", ids)
        self.assertIn("inspect-or-remove-2", ids)

    def test_horizontal_regions_are_focusable_and_labeled(self) -> None:
        rendered = build_docs.markdown_to_html(
            "```bash\necho hello\n```\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        )

        self.assertIn('<pre tabindex="0" role="region" aria-label=', rendered)
        self.assertIn('<table tabindex="0" aria-label=', rendered)


class CanonicalLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="docs-canonical-")
        self.root = Path(self.temporary.name)
        self.site_dir = self.root / "docs" / "_site"
        self.site_dir.mkdir(parents=True)
        self.site = {
            "public_origin": "https://anur4ag.github.io",
            "base_url": "/pr-completion",
            "pages": [{"path": "/"}],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _findings_for(self, canonical_markup: str) -> list[str]:
        (self.site_dir / "index.html").write_text(
            f"<!doctype html><html><head>{canonical_markup}</head><body></body></html>",
            encoding="utf-8",
        )
        findings: list[str] = []
        check_docs.check_built_site_internal(
            self.root, self.site, self.site_dir, findings
        )
        return findings

    def test_expected_canonical_passes(self) -> None:
        findings = self._findings_for(
            '<link rel="canonical" href="https://anur4ag.github.io/pr-completion/">'
        )
        self.assertEqual(findings, [])

    def test_wrong_but_live_canonical_fails(self) -> None:
        findings = self._findings_for(
            '<link rel="canonical" '
            'href="https://anur4ag.github.io/pr-completion/support/">'
        )
        self.assertTrue(any("does not match" in item for item in findings), findings)

    def test_missing_or_duplicate_canonical_fails_exactly_one_contract(self) -> None:
        missing = self._findings_for("")
        duplicate = self._findings_for(
            '<link rel="canonical" href="https://anur4ag.github.io/pr-completion/">'
            '<link rel="canonical" href="https://anur4ag.github.io/pr-completion/">'
        )
        self.assertTrue(any("found 0" in item for item in missing), missing)
        self.assertTrue(any("found 2" in item for item in duplicate), duplicate)

    def test_cli_reports_external_site_wrong_canonical_without_traceback(self) -> None:
        external_site = self.root / "external-site"
        build = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "build-docs.py"),
                "--out",
                str(external_site),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(build.returncode, 0, build.stderr)

        installation = external_site / "installation" / "index.html"
        rendered = installation.read_text(encoding="utf-8")
        rendered = rendered.replace(
            "https://anur4ag.github.io/pr-completion/installation/",
            "https://anur4ag.github.io/pr-completion/",
            1,
        )
        installation.write_text(rendered, encoding="utf-8")

        checked = subprocess.run(
            [
                sys.executable,
                "-B",
                str(SCRIPTS / "check-docs-links.py"),
                "--site-dir",
                str(external_site),
                "--skip-external",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
        self.assertIn(str(installation.resolve()), checked.stderr)
        self.assertIn("canonical", checked.stderr)
        self.assertIn("does not match", checked.stderr)
        self.assertNotIn("Traceback", checked.stderr)

    def test_exact_pending_release_url_is_the_only_external_skip(self) -> None:
        pending = "https://github.com/anur4ag/pr-completion/releases/tag/v0.1.2"
        findings: list[str] = []
        checked, unique, skipped = check_docs.check_external_http_links(
            hrefs=[("docs/index.md", pending)],
            extra_urls=[],
            timeout=0.1,
            max_workers=1,
            skip_urls={pending},
            findings=findings,
        )
        self.assertEqual((checked, unique, skipped), (0, 1, 1))
        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
