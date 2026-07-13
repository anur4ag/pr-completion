#!/usr/bin/env python3
"""Reject forbidden merge-mutation instructions from the public skill bundle.

Terminal success for take-pr-to-completion is verified merge readiness.
The public skill tree must not authorize merge, auto-merge enablement,
merge-queue enrollment, protection bypass, or force-push mutations.

Scans every shipped textual/executable file under skills/, including test
trees. Test-data exemptions are path-aware and data-only: a marker line
never exempts production scripts or executable helpers. Full-file exemption
is limited to non-executable data files under an explicit
`tests/safety-scanner-fixtures/` subtree. Prohibition language is allowed
only via tightly scoped canonical patterns, not broad negation heuristics.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".markdown",
        ".txt",
        ".rst",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".ini",
        ".cfg",
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".js",
        ".ts",
        ".mjs",
        ".cjs",
        ".rb",
        ".go",
        ".rs",
        ".graphql",
        ".gql",
    }
)

SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)

# Marker required for data-only fixture exemption. Never sufficient alone:
# path must be under tests/safety-scanner-fixtures/ and the file must be
# non-executable data (not a production script or helper).
TEST_DATA_EXEMPTION_MARKER = "pr-completion-safety-test-data"
TEST_DATA_EXEMPTION_RE = re.compile(
    rf"(?i)(?:#|//|/\*|--)\s*{re.escape(TEST_DATA_EXEMPTION_MARKER)}\b"
    rf"|{re.escape(TEST_DATA_EXEMPTION_MARKER)}\s*=\s*True\b"
)
# Explicit fixture subtree relative to a skill root (…/skills/<skill>/…).
SAFETY_FIXTURE_DIR_NAME = "safety-scanner-fixtures"
DATA_ONLY_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".yaml",
        ".yml",
        ".sample",
        ".fixture",
        ".csv",
    }
)
# Paths that are never fully exempt, even with a marker.
NEVER_EXEMPT_PATH_PARTS = frozenset(
    {
        "scripts",
        "bin",
        "hooks",
        "agents",
    }
)
EXECUTABLE_SUFFIXES = frozenset(
    {
        ".py",
        ".pyi",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".js",
        ".ts",
        ".mjs",
        ".cjs",
        ".rb",
        ".go",
        ".rs",
        ".php",
        ".pl",
    }
)

# Compacted (whitespace-collapsed) patterns for mutation surfaces.
FORBIDDEN_SURFACES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "gh pr merge",
        re.compile(r"\bgh\s+pr\s+merge\b", re.IGNORECASE),
    ),
    (
        "gh alias to pr merge",
        re.compile(
            r"(?:"
            r"\bgh\s+alias\s+set\b.{0,80}\bpr\s+merge\b"
            r"|"
            r"\balias\b[^\n]{0,120}\bgh\s+pr\s+merge\b"
            r"|"
            r"""\balias\b[^\n]{0,80}=[^\n]{0,40}['\"]gh\s+pr\s+merge"""
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "shell function wrapping gh pr merge",
        re.compile(
            r"(?:"
            r"\bfunction\s+\w+\s*\([^)]*\)\s*\{[^}]{0,200}\bgh\s+pr\s+merge\b"
            r"|"
            r"\b\w+\s*\(\)\s*\{[^}]{0,200}\bgh\s+pr\s+merge\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "enablePullRequestAutoMerge",
        re.compile(r"\benablePullRequestAutoMerge\b"),
    ),
    (
        "disablePullRequestAutoMerge",
        re.compile(r"\bdisablePullRequestAutoMerge\b"),
    ),
    (
        "enqueuePullRequest",
        re.compile(r"\benqueuePullRequest\b"),
    ),
    (
        "mergePullRequest GraphQL",
        re.compile(r"\bmergePullRequest\b"),
    ),
    (
        "git push --force",
        re.compile(r"\bgit\s+push\b.{0,120}(?:--force\b|--force-with-lease\b)", re.IGNORECASE),
    ),
    (
        "git push -f",
        re.compile(r"\bgit\s+push\b.{0,80}(?<!\S)-f\b", re.IGNORECASE),
    ),
    (
        "REST pull merge endpoint",
        re.compile(
            r"(?:"
            r"\b(?:PUT|POST|PATCH)\b.{0,80}/pulls?/\w+/merge\b"
            r"|"
            r"/repos/[^/\s\"']+/[^/\s\"']+/pulls?/\w+/merge\b"
            r"|"
            r"api\.github\.com/repos/[^/\s\"']+/[^/\s\"']+/pulls?/\w+/merge\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "merge-queue enqueue endpoint",
        re.compile(
            r"/repos/[^/\s\"']+/[^/\s\"']+/(?:pulls?/\w+/merge-queue|merge-queues?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "subprocess gh pr merge",
        re.compile(
            r"(?:subprocess\.(?:run|call|Popen|check_call|check_output)|"
            r"os\.system|os\.popen|os\.execv?e?p?)"
            r".{0,200}?\bgh\b.{0,80}?\bpr\b.{0,40}?\bmerge\b",
            re.IGNORECASE,
        ),
    ),
    (
        "python argv gh pr merge",
        re.compile(
            r"""\[\s*["']gh["']\s*,\s*["']pr["']\s*,\s*["']merge["']""",
            re.IGNORECASE,
        ),
    ),
    (
        "shell gh api merge mutation",
        re.compile(
            r"\bgh\s+api\b.{0,200}(?:"
            r"/pulls?/\w+/merge\b|"
            r"enablePullRequestAutoMerge|"
            r"disablePullRequestAutoMerge|"
            r"enqueuePullRequest|"
            r"mergePullRequest"
            r")",
            re.IGNORECASE,
        ),
    ),
)

FORBIDDEN_INSTRUCTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "enable auto-merge instruction",
        re.compile(
            r"(?:"
            r"\b(?:enable|enabling|turn on|turns on)\s+auto[- ]merge\b"
            r"|"
            r"\bauto[- ]merge\s+(?:enable|enabled|enabling|on)\b"
            r"|"
            r"\bturn\s+auto[- ]merge\s+on\b"
            r"|"
            r"\bauto[- ]merge\s+should\s+be\s+enabled\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        "merge immediately instruction",
        re.compile(r"\bmerge\s+immediately\b", re.IGNORECASE),
    ),
    (
        "use merge method instruction",
        re.compile(
            r"\b(?:use|choose|select)\s+the\s+(?:requested\s+)?merge\s+method\b",
            re.IGNORECASE,
        ),
    ),
    (
        "enable merge instruction",
        re.compile(r"\benable\s+merge\b", re.IGNORECASE),
    ),
    (
        "join merge queue instruction",
        re.compile(
            r"\b(?:join|enroll|enqueue)(?:ing)?\s+(?:a\s+|the\s+)?merge\s+queue\b",
            re.IGNORECASE,
        ),
    ),
    (
        "admin bypass instruction",
        re.compile(
            r"\b(?:bypass|override|bypassing|overriding)\s+(?:branch\s+)?protections?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "authorized force-push instruction",
        re.compile(r"\bforce[- ]push(?:ing)?\b", re.IGNORECASE),
    ),
)

# Only these canonical prohibition forms may mention forbidden surfaces.
# Broad "do not" anywhere nearby is intentionally not enough.
CANONICAL_PROHIBITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)this\s+skill\s+never\s+mutates\s+merge\s+state\.\s*"
        r"do\s+not\s+run\s+`?gh\s+pr\s+merge`?"
    ),
    re.compile(
        r"(?i)do\s+not\s+run\s+`?gh\s+pr\s+merge`?"
        r".{0,300}(?:enablePullRequestAutoMerge|disablePullRequestAutoMerge|enqueuePullRequest)"
    ),
    re.compile(
        r"(?i)do\s+not\s+run\s+`?gh\s+pr\s+merge`?"
        r".{0,200}(?:enable|disable)\s+(?:or\s+disable\s+)?auto[- ]merge"
    ),
    re.compile(r"(?i)never\s+force[- ]push\b"),
    re.compile(
        r"(?i)never\s+rewrite\s+history,\s*bypass\s+protections,\s*merge"
    ),
    re.compile(
        r"(?i)stops?\s+without\s+merging,\s*enabling\s+auto[- ]merge,\s*"
        r"joining\s+a\s+merge\s+queue,\s*bypassing\s+protections,\s*or\s*force[- ]pushing"
    ),
    re.compile(
        r"(?i)do\s+not\s+merge,\s*enable\s+auto[- ]merge,\s*or\s*join\s+a\s+merge\s+queue"
    ),
    re.compile(
        r"(?i)do\s+not\s+disable,\s*reconfigure,\s*or\s*replace\s+that\s+setting"
    ),
    re.compile(
        r"(?i)without\s+merging,\s*enabling\s+auto[- ]merge,\s*"
        r"joining\s+a\s+merge\s+queue,\s*bypassing\s+protections,\s*or\s*force[- ]pushing"
    ),
    re.compile(
        r"(?i)issue\s+no\s+merge[- ]state\s+mutation"
    ),
    re.compile(
        r"(?i)do\s+not\s+merge\s+or\s+enable\s+auto[- ]merge"
    ),
)

# Mixed-negation: a soft/partial prohibit that does not govern the mutation command.
MIXED_NEGATION = re.compile(
    r"(?i)\b(?:do\s+not|don't|never|must\s+not)\b.{0,80}?\b(?:"
    r"skip|ignore|forget|worry|mind|bother|hesitate|block|stop\s+you"
    r")\b.{0,100}?\b(?:run|invoke|issue|call|execute|enable|use|perform|just)\b"
)

REQUIRED_CONTRACT_MARKERS: tuple[str, ...] = (
    "verified merge readiness",
    "never mutates merge state",
    "gh pr merge",
    "auto_merge",
    "current head",
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


def is_probably_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    if path.name in {"SKILL.md", "Makefile", "Dockerfile", "Jenkinsfile"}:
        return True
    if path.suffix == "" and path.parent.name in {"scripts", "bin", "hooks"}:
        return True
    # Extensionless executables under tests still ship with the skill tree.
    if path.suffix == "" and os_access_executable(path):
        return True
    return False


def os_access_executable(path: Path) -> bool:
    try:
        return bool(path.stat().st_mode & 0o111)
    except OSError:
        return False


def skill_files(root: Path) -> list[Path]:
    skills = root / "skills"
    if not skills.is_dir():
        raise SystemExit(f"missing skills directory: {skills}")
    files: list[Path] = []
    for path in sorted(skills.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin"}:
            continue
        if not is_probably_text(path):
            try:
                sample = path.read_bytes()[:512]
            except OSError:
                continue
            if b"\0" in sample:
                continue
        files.append(path)
    return files


def has_test_data_marker(content: str) -> bool:
    return bool(TEST_DATA_EXEMPTION_RE.search(content))


def skill_relative_parts(path: Path, root: Path) -> tuple[str, ...] | None:
    """Return path parts under skills/, or None if outside the skill tree."""
    try:
        rel = path.resolve().relative_to((root / "skills").resolve())
    except ValueError:
        return None
    return rel.parts


def is_data_only_fixture_path(path: Path, root: Path) -> bool:
    """True only for non-executable data under tests/safety-scanner-fixtures/."""
    parts = skill_relative_parts(path, root)
    if not parts:
        return False
    # Never exempt production-capable locations.
    if any(part in NEVER_EXEMPT_PATH_PARTS for part in parts):
        return False
    if SAFETY_FIXTURE_DIR_NAME not in parts:
        return False
    # Require …/tests/safety-scanner-fixtures/…
    try:
        tests_idx = parts.index("tests")
        fixture_idx = parts.index(SAFETY_FIXTURE_DIR_NAME)
    except ValueError:
        return False
    if fixture_idx != tests_idx + 1:
        return False
    suffix = path.suffix.lower()
    if suffix in EXECUTABLE_SUFFIXES:
        return False
    if suffix not in DATA_ONLY_SUFFIXES and suffix != "":
        return False
    # Extensionless or data suffix files must not be executable helpers.
    if os_access_executable(path):
        return False
    if suffix == "" and path.name.endswith((".py", ".sh")):
        return False
    return True


def is_full_file_exempt(path: Path, root: Path, content: str) -> bool:
    """Marker + path-aware data-only fixture subtree. Never exempts scripts."""
    if not has_test_data_marker(content):
        return False
    return is_data_only_fixture_path(path, root)


def collapse_whitespace(text: str) -> str:
    without_continuations = re.sub(r"\\(?:\r\n|\n|\r)", " ", text)
    without_continuations = without_continuations.replace("\\\n", " ").replace("\\\r\n", " ")
    return re.sub(r"\s+", " ", without_continuations)


def extract_sentence(compacted: str, start: int, end: int) -> str:
    left = 0
    for sep in (". ", "; ", "! ", "? "):
        idx = compacted.rfind(sep, 0, start)
        if idx != -1:
            left = max(left, idx + len(sep))
    left = max(left, start - 500)
    right = len(compacted)
    for sep in (". ", "; ", "! ", "? "):
        idx = compacted.find(sep, end)
        if idx != -1:
            right = min(right, idx + 1)
    right = min(right, end + 500)
    return compacted[left:right].strip()


def is_canonical_prohibition(sentence: str) -> bool:
    if not sentence:
        return False
    if MIXED_NEGATION.search(sentence):
        return False
    normalized = collapse_whitespace(sentence)
    return any(pattern.search(normalized) for pattern in CANONICAL_PROHIBITION_PATTERNS)


def scan_compacted(
    path: Path,
    compacted: str,
    original_lines: list[str],
    findings: list[str],
    patterns: tuple[tuple[str, re.Pattern[str]], ...],
) -> None:
    for label, pattern in patterns:
        for match in pattern.finditer(compacted):
            sentence = extract_sentence(compacted, match.start(), match.end())
            if is_canonical_prohibition(sentence):
                continue
            # Broader window catch for multi-clause SKILL contract sentences.
            window = compacted[
                max(0, match.start() - 400) : min(len(compacted), match.end() + 200)
            ]
            if is_canonical_prohibition(window) and not MIXED_NEGATION.search(window):
                # Require the match itself still sits under a canonical clause.
                if any(p.search(window) for p in CANONICAL_PROHIBITION_PATTERNS):
                    # Mixed-negation already rejected; allow only if a canonical
                    # pattern covers a span that includes this match position.
                    covered = False
                    for p in CANONICAL_PROHIBITION_PATTERNS:
                        for cm in p.finditer(compacted):
                            if cm.start() <= match.start() <= cm.end() or (
                                cm.start() <= match.end() <= cm.end()
                            ):
                                covered = True
                                break
                        if covered:
                            break
                    if covered:
                        continue
            token = match.group(0).split()[0]
            line_number = 1
            line_text = match.group(0)
            for index, line in enumerate(original_lines, start=1):
                if token.lower() in line.lower() or match.group(0).lower() in collapse_whitespace(
                    line
                ).lower():
                    line_number = index
                    line_text = line.strip()
                    break
            findings.append(
                f"{path}:{line_number}: forbidden mutation surface ({label}): {line_text}"
            )


def scan_file(path: Path, root: Path, findings: list[str]) -> None:
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            findings.append(f"{path}: could not read: {error}")
            return
    except OSError as error:
        findings.append(f"{path}: could not read: {error}")
        return

    # Marker alone is never enough. Production scripts / executable helpers with
    # a marker are still fully scanned and must fail on forbidden surfaces.
    if is_full_file_exempt(path, root, content):
        return

    lines = content.splitlines()
    compacted = collapse_whitespace(content)
    scan_compacted(path, compacted, lines, findings, FORBIDDEN_SURFACES)
    scan_compacted(path, compacted, lines, findings, FORBIDDEN_INSTRUCTION_PATTERNS)


def check_required_contract(root: Path, findings: list[str]) -> None:
    skill = root / "skills" / "take-pr-to-completion" / "SKILL.md"
    if not skill.is_file():
        findings.append(f"missing required skill file: {skill}")
        return
    text = skill.read_text(encoding="utf-8")
    lowered = text.lower()
    for marker in REQUIRED_CONTRACT_MARKERS:
        if marker.lower() not in lowered:
            findings.append(
                f"{skill}: missing required safety contract marker: {marker!r}"
            )
    if re.search(r"`ready`\s*:\s*enable\s+merge", text, re.IGNORECASE):
        findings.append(f"{skill}: ready state still instructs enabling merge")


def check_skill_bundle(root: Path) -> list[str]:
    findings: list[str] = []
    root = root.resolve()
    for path in skill_files(root):
        scan_file(path, root, findings)
    check_required_contract(root, findings)
    seen: set[str] = set()
    unique: list[str] = []
    for finding in findings:
        if finding in seen:
            continue
        seen.add(finding)
        unique.append(finding)
    return unique


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Scan the public skill bundle for forbidden merge-state mutation "
            "commands and instructions."
        )
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin root (defaults to repository containing this script)",
    )
    args = parser.parse_args(argv)
    root = args.root if args.root is not None else plugin_root_from(Path(__file__))
    findings = check_skill_bundle(root)
    if findings:
        print("merge-ready safety check failed:", file=sys.stderr)
        for finding in findings:
            print(f"  - {finding}", file=sys.stderr)
        return 1
    print(f"merge-ready safety check passed for {root / 'skills'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
