#!/usr/bin/env python3
"""Enforce PR Completion's guarded autonomous-landing boundary.

Every shipped skill file is scanned. Merge-state mutation surfaces are rejected
everywhere except the single audited ``pr_land.py`` helper, whose exact-head,
fresh-readiness, explicit-confirmation, and no-admin invariants are checked
structurally. Data-only scanner fixtures have a narrow path-aware exemption.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from pathlib import Path


TEST_DATA_EXEMPTION_MARKER = "pr-completion-safety-test-data"
SAFETY_FIXTURE_DIR_NAME = "safety-scanner-fixtures"
DATA_ONLY_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".json", ".yaml", ".yml"})
EXECUTABLE_SUFFIXES = frozenset(
    {".py", ".pyi", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".js", ".ts", ".rb"}
)
SKIP_DIR_NAMES = frozenset(
    {"__pycache__", ".git", ".venv", "node_modules", "dist", "build", ".pytest_cache"}
)
AUTHORIZED_LANDER = Path("take-pr-to-completion/scripts/pr_land.py")
AUDITED_WATCHER = Path("take-pr-to-completion/scripts/pr_watch.py")
CONTRACT_SKILL = Path("take-pr-to-completion/SKILL.md")
AUTHORIZED_ARGV = '["gh", "pr", "merge", url, "--match-head-commit", head]'
AUDITED_RUNTIME_SHA256 = {
    AUTHORIZED_LANDER: "b2f4b23b35689e4dd7e03286f643e9f5c307ac763b507382a9357c9a0fe12f5f",
    AUDITED_WATCHER: "f4d4a2fc1cfa21adafb2c771cf456dd8b15a5f255425e75b66f4dc4c7199b517",
}

FORBIDDEN_SURFACES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("gh pr merge", re.compile(r"\bgh\s+pr\s+merge\b", re.IGNORECASE)),
    (
        "gh alias to pr merge",
        re.compile(r"\bgh\s+alias\s+set\b.{0,100}\bpr\s+merge\b|\balias\b.{0,100}\bgh\s+pr\s+merge\b", re.IGNORECASE),
    ),
    ("enablePullRequestAutoMerge", re.compile(r"\benablePullRequestAutoMerge\b")),
    ("disablePullRequestAutoMerge", re.compile(r"\bdisablePullRequestAutoMerge\b")),
    ("enqueuePullRequest", re.compile(r"\benqueuePullRequest\b")),
    ("mergePullRequest GraphQL", re.compile(r"\bmergePullRequest\b")),
    (
        "git push --force",
        re.compile(r"\bgit\s+push\b.{0,120}(?:--force(?:-with-lease)?\b|(?<!\S)-f\b)", re.IGNORECASE),
    ),
    (
        "REST pull merge endpoint",
        re.compile(r"(?:\b(?:PUT|POST|PATCH)\b.{0,80}|\bgh\s+api\b.{0,120})/?repos/[^\s]+/pulls?/[^/\s]+/merge\b", re.IGNORECASE),
    ),
    (
        "python argv gh pr merge",
        re.compile(r"\[\s*['\"]gh['\"]\s*,\s*['\"]pr['\"]\s*,\s*['\"]merge['\"]", re.IGNORECASE),
    ),
)

FORBIDDEN_INSTRUCTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "enable auto-merge instruction",
        re.compile(r"\b(?:enable|turn\s+on)\s+auto[- ]merge\b|\bauto[- ]merge\s+should\s+be\s+enabled\b", re.IGNORECASE),
    ),
    ("merge immediately instruction", re.compile(r"\bmerge\s+immediately\b", re.IGNORECASE)),
)

REQUIRED_CONTRACT_MARKERS = (
    "explicit per-PR confirmation",
    "current head SHA",
    "pr_land.py",
    "Never use `--admin`",
    "awaiting_merge",
    "phase-only child mode",
)


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "skills").is_dir() and (
            (path / ".claude-plugin").is_dir() or (path / ".codex-plugin").is_dir()
        ):
            return path
    raise SystemExit(f"could not locate plugin root from {start}")


def os_access_executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def skill_relative(path: Path, root: Path) -> Path | None:
    try:
        return path.absolute().relative_to((root / "skills").absolute())
    except ValueError:
        return None


def is_full_file_exempt(path: Path, root: Path, content: str) -> bool:
    relative = skill_relative(path, root)
    if relative is None or TEST_DATA_EXEMPTION_MARKER not in content:
        return False
    parts = relative.parts
    try:
        tests_index = parts.index("tests")
        fixture_index = parts.index(SAFETY_FIXTURE_DIR_NAME)
    except ValueError:
        return False
    return (
        fixture_index == tests_index + 1
        and path.suffix.lower() in DATA_ONLY_SUFFIXES
        and path.suffix.lower() not in EXECUTABLE_SUFFIXES
        and not os_access_executable(path)
        and not any(part in {"scripts", "bin", "hooks", "agents"} for part in parts)
    )


def skill_files(root: Path) -> list[Path]:
    skills = root / "skills"
    if not skills.is_dir():
        raise SystemExit(f"missing skills directory: {skills}")
    return [
        path
        for path in sorted(skills.rglob("*"))
        if path.is_symlink()
        or (
            path.is_file()
            and not any(part in SKIP_DIR_NAMES for part in path.parts)
            and path.suffix.lower()
            not in {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe"}
        )
    ]


def collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\\(?:\r\n|\n|\r)", " ", text))


def append_matches(
    path: Path,
    content: str,
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
    findings: list[str],
) -> None:
    compacted = collapse_whitespace(content)
    for label, pattern in patterns:
        for match in pattern.finditer(compacted):
            findings.append(
                f"{path}: forbidden mutation surface ({label}): {match.group(0)}"
            )


def constant_value(node: ast.AST) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, int, float, bool)):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = constant_value(node.left)
        right = constant_value(node.right)
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return None
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [constant_value(element) for element in node.elts]
        return values if all(value is not None for value in values) else None
    return None


def callable_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = callable_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def scan_python_process_calls(path: Path, content: str, findings: list[str]) -> None:
    try:
        tree = ast.parse(content)
    except SyntaxError as error:
        findings.append(f"{path}: Python source cannot be safety-audited: {error}")
        return
    process_calls = {
        "subprocess.run",
        "subprocess.call",
        "subprocess.Popen",
        "subprocess.check_call",
        "subprocess.check_output",
        "os.system",
        "os.popen",
        "os.execv",
        "os.execve",
        "os.execl",
        "os.execle",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or callable_name(node.func) not in process_calls:
            continue
        if not node.args:
            findings.append(f"{path}:{node.lineno}: process call has no auditable command")
            continue
        value = constant_value(node.args[0])
        if value is None:
            findings.append(
                f"{path}:{node.lineno}: unclassifiable process command in unaudited runtime file"
            )
            continue
        command = " ".join(str(item) for item in value) if isinstance(value, list) else str(value)
        append_matches(path, command, FORBIDDEN_SURFACES, findings)


def verify_audited_runtime(
    path: Path,
    relative: Path,
    content: str,
    findings: list[str],
) -> None:
    expected = AUDITED_RUNTIME_SHA256[relative]
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if actual != expected:
        findings.append(
            f"{path}: audited runtime digest changed: {actual} != {expected}; "
            "review the complete helper and update the pinned digest intentionally"
        )


def scan_authorized_lander(path: Path, content: str, findings: list[str]) -> None:
    requirements = {
        "one canonical guarded GitHub CLI argv": content.count(AUTHORIZED_ARGV) == 1,
        "explicit confirmation flag": '"--confirm"' in content,
        "fresh watcher snapshot": "watcher_snapshot(" in content,
        "verified-ready predicate": 'snapshot.get("state") != "ready"' in content,
        "exact-head comparison": "current_head != expected_head" in content,
        "head guard flag": '"--match-head-commit"' in content,
        "fixture mutation refusal": "offline fixtures cannot authorize" in content,
        "no admin bypass": '"--admin"' not in content and "'--admin'" not in content,
    }
    for label, passed in requirements.items():
        if not passed:
            findings.append(f"{path}: guarded landing invariant failed: {label}")

    # Remove only the one audited argv. Any second command/API/force surface fails.
    append_matches(
        path,
        content.replace(AUTHORIZED_ARGV, "AUTHORIZED_GITHUB_LANDING_ARGV", 1),
        FORBIDDEN_SURFACES,
        findings,
    )


def scan_file(path: Path, root: Path, findings: list[str]) -> None:
    if path.is_symlink():
        findings.append(f"{path}: symlinks are forbidden in the shipped skills tree")
        return
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        findings.append(f"{path}: could not read as UTF-8 text: {error}")
        return
    if is_full_file_exempt(path, root, content):
        return
    relative = skill_relative(path, root)
    if relative == AUTHORIZED_LANDER:
        verify_audited_runtime(path, relative, content, findings)
        scan_authorized_lander(path, content, findings)
        return
    if relative == AUDITED_WATCHER:
        verify_audited_runtime(path, relative, content, findings)
        append_matches(path, content, FORBIDDEN_SURFACES, findings)
        return
    if (
        relative is not None
        and "tests" not in relative.parts
        and (
            path.suffix.lower() in EXECUTABLE_SUFFIXES
            or os_access_executable(path)
        )
    ):
        findings.append(
            f"{path}: executable runtime is not in the explicit audited digest allowlist"
        )
    append_matches(path, content, FORBIDDEN_SURFACES, findings)
    if relative != CONTRACT_SKILL:
        append_matches(path, content, FORBIDDEN_INSTRUCTIONS, findings)
    if (
        relative is not None
        and "tests" not in relative.parts
        and path.suffix.lower() == ".py"
    ):
        scan_python_process_calls(path, content, findings)
    if (
        relative is not None
        and "tests" not in relative.parts
        and path.suffix.lower() in {".sh", ".bash", ".zsh"}
        and re.search(r"(?is)\b\w+\s*=\s*gh\b.*\$\{?\w+\}?\s+pr\s+merge\b", content)
    ):
        findings.append(f"{path}: dynamic shell command can invoke gh pr merge")


def check_required_contract(root: Path, findings: list[str]) -> None:
    skill = root / "skills" / CONTRACT_SKILL
    lander = root / "skills" / AUTHORIZED_LANDER
    if not skill.is_file():
        findings.append(f"missing required skill file: {skill}")
        return
    if not lander.is_file():
        findings.append(f"missing guarded landing helper: {lander}")
    text = skill.read_text(encoding="utf-8")
    for marker in REQUIRED_CONTRACT_MARKERS:
        if marker.lower() not in text.lower():
            findings.append(f"{skill}: missing guarded landing marker: {marker!r}")


def check_skill_bundle(root: Path) -> list[str]:
    root = root.resolve()
    findings: list[str] = []
    for path in skill_files(root):
        scan_file(path, root, findings)
    check_required_contract(root, findings)
    return list(dict.fromkeys(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan the public skills for guarded autonomous-landing safety.",
    )
    parser.add_argument("--root", type=Path)
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else plugin_root_from(Path(__file__))
    findings = check_skill_bundle(root)
    if findings:
        print("guarded landing safety check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"guarded landing safety check passed for {root / 'skills'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
