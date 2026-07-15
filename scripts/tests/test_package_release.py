#!/usr/bin/env python3
"""Deterministic packaging tests for ticket 5 release artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1]
ROOT = SCRIPTS.parent
PACKAGE = SCRIPTS / "package-release.py"


def load_package_module() -> types.ModuleType:
    sys.dont_write_bytecode = True
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    spec = importlib.util.spec_from_file_location(
        "pr_completion_package_release", PACKAGE
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {PACKAGE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


package_mod = load_package_module()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class PackageReleaseDeterminismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="pkg-rel-")
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self._seed_min_tree(self.source)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _seed_min_tree(self, root: Path) -> None:
        ignore = shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            "docs/_site",
            "release-out",
        )
        for name in (
            "VERSION",
            "LICENSE",
            "CHANGELOG.md",
            ".gitignore",
            "README.md",
            "SECURITY.md",
            ".claude-plugin",
            ".codex-plugin",
            "assets",
            "skills",
            "scripts",
            "docs",
            ".github",
        ):
            source = ROOT / name
            dest = root / name
            if source.is_dir():
                shutil.copytree(source, dest, ignore=ignore)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
        self.version = (root / "VERSION").read_text(encoding="utf-8").strip()

    def test_cli_builds_expected_artifacts(self) -> None:
        out_dir = self.base / "out-a"
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                str(PACKAGE),
                "--root",
                str(self.source),
                "--out-dir",
                str(out_dir),
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(completed.returncode, 0, msg=completed.stderr)
        plugin = out_dir / f"pr-completion-{self.version}-plugin.zip"
        skills = out_dir / f"pr-completion-{self.version}-skills-source.zip"
        checksums = out_dir / "SHA256SUMS.txt"
        self.assertTrue(plugin.is_file())
        self.assertTrue(skills.is_file())
        self.assertTrue(checksums.is_file())
        text = checksums.read_text(encoding="utf-8")
        self.assertIn(plugin.name, text)
        self.assertIn(skills.name, text)
        self.assertIn(_sha256(plugin), text)
        self.assertIn(_sha256(skills), text)

    def test_repeated_builds_are_byte_identical(self) -> None:
        out_a = self.base / "det-a"
        out_b = self.base / "det-b"
        for out_dir in (out_a, out_b):
            result = package_mod.package_release(self.source, out_dir)
            self.assertEqual(result["version"], self.version)
        for name in (
            f"pr-completion-{self.version}-plugin.zip",
            f"pr-completion-{self.version}-skills-source.zip",
            "SHA256SUMS.txt",
        ):
            a = (out_a / name).read_bytes()
            b = (out_b / name).read_bytes()
            self.assertEqual(a, b, msg=f"{name} not deterministic")

    def test_plugin_zip_contains_manifests_and_four_skills(self) -> None:
        out_dir = self.base / "contents"
        package_mod.package_release(self.source, out_dir)
        plugin = out_dir / f"pr-completion-{self.version}-plugin.zip"
        with zipfile.ZipFile(plugin) as archive:
            names = set(archive.namelist())
        prefix = f"pr-completion-{self.version}/"
        for relative in (
            "VERSION",
            ".claude-plugin/plugin.json",
            ".claude-plugin/marketplace.json",
            ".codex-plugin/plugin.json",
            "assets/traycer-icon.png",
            "assets/traycer-icon-dark.png",
            "skills/take-pr-to-completion/SKILL.md",
            "skills/commit-workspace-changes/SKILL.md",
            "skills/gh-review-comment-triage/SKILL.md",
            "skills/merge-conflict-resolution/SKILL.md",
            ".github/workflows/release.yml",
            "scripts/package-release.py",
        ):
            self.assertIn(prefix + relative, names)

    def test_skills_zip_is_skills_tree_only(self) -> None:
        out_dir = self.base / "skills-only"
        package_mod.package_release(self.source, out_dir)
        skills = out_dir / f"pr-completion-{self.version}-skills-source.zip"
        with zipfile.ZipFile(skills) as archive:
            names = archive.namelist()
        self.assertTrue(names)
        for name in names:
            self.assertTrue(
                name.startswith(f"pr-completion-{self.version}-skills/skills/"),
                msg=name,
            )
        # Must not include top-level manifests outside skills/.
        self.assertFalse(any(name.endswith("plugin.json") for name in names))

    def test_cachebuster_version_is_rejected(self) -> None:
        (self.source / "VERSION").write_text("0.1.0+codex.bad\n", encoding="utf-8")
        with self.assertRaises(package_mod.PackageError):
            package_mod.package_release(self.source, self.base / "bad")


if __name__ == "__main__":
    unittest.main()
