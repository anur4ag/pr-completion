#!/usr/bin/env python3
"""Validate Developer Certificate of Origin trailers with identity matching.

Rules:
  - Every commit in the reviewed range must include at least one
    ``Signed-off-by: Name <email>`` trailer.
  - At least one trailer must match the commit author or committer identity
    (case-insensitive name and email). An unrelated signatory alone fails.
  - Exactly two historical SHAs are exempt (immutable v0.1.1 ancestry):
    a93a5d77f51a713f86578255271d59bf96a8e991
    4af89ae8e5648c4a6846773817aa9856c5f979a4

Usage:
  python3 -B scripts/check-dco.py --range BASE..HEAD
  python3 -B scripts/check-dco.py --commit SHA
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


# Fixed one-time exception list. Do not expand without a new maintainer decision.
DCO_EXCEPTION_SHAS: frozenset[str] = frozenset(
    {
        "a93a5d77f51a713f86578255271d59bf96a8e991",
        "4af89ae8e5648c4a6846773817aa9856c5f979a4",
    }
)

SIGNED_OFF_RE = re.compile(
    r"^Signed-off-by:\s*(?P<name>.+?)\s*<(?P<email>[^>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)


class DCOError(Exception):
    """DCO validation failure."""


@dataclass(frozen=True)
class Identity:
    name: str
    email: str

    def normalized(self) -> tuple[str, str]:
        return self.name.strip().casefold(), self.email.strip().casefold()


@dataclass(frozen=True)
class SignOff:
    name: str
    email: str

    def matches(self, identity: Identity) -> bool:
        return (
            self.name.strip().casefold(),
            self.email.strip().casefold(),
        ) == identity.normalized()


def _run_git(repo: Path, args: list[str]) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DCOError(
            f"git {' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )
    return completed.stdout


def parse_signoffs(message: str) -> list[SignOff]:
    found: list[SignOff] = []
    for match in SIGNED_OFF_RE.finditer(message):
        found.append(
            SignOff(name=match.group("name").strip(), email=match.group("email").strip())
        )
    return found


def load_commit_identities(repo: Path, sha: str) -> tuple[Identity, Identity, str]:
    raw = _run_git(
        repo,
        [
            "show",
            "-s",
            "--format=%an%n%ae%n%cn%n%ce%n%B",
            sha,
        ],
    )
    lines = raw.splitlines()
    if len(lines) < 4:
        raise DCOError(f"could not parse identities for {sha}")
    author = Identity(name=lines[0], email=lines[1])
    committer = Identity(name=lines[2], email=lines[3])
    # Message starts after the four identity lines; show includes a trailing newline.
    message = "\n".join(lines[4:])
    return author, committer, message


def validate_commit(repo: Path, sha: str) -> str:
    full = _run_git(repo, ["rev-parse", sha]).strip()
    if full in DCO_EXCEPTION_SHAS:
        return f"OK (historical DCO exception): {full}"

    author, committer, message = load_commit_identities(repo, full)
    signoffs = parse_signoffs(message)
    if not signoffs:
        raise DCOError(
            f"MISSING Signed-off-by: {full} "
            f"(author={author.name} <{author.email}>)"
        )

    if any(s.matches(author) or s.matches(committer) for s in signoffs):
        return f"OK: {full}"

    signatory_summary = ", ".join(f"{s.name} <{s.email}>" for s in signoffs)
    raise DCOError(
        f"UNRELATED Signed-off-by: {full} has trailer(s) [{signatory_summary}] "
        f"but neither matches author {author.name} <{author.email}> "
        f"nor committer {committer.name} <{committer.email}>"
    )


def list_commits(repo: Path, rev_range: str) -> list[str]:
    raw = _run_git(repo, ["rev-list", "--reverse", rev_range]).strip()
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def exception_list_is_exact() -> bool:
    """Guard against accidental expansion of the historical exception set."""
    return DCO_EXCEPTION_SHAS == frozenset(
        {
            "a93a5d77f51a713f86578255271d59bf96a8e991",
            "4af89ae8e5648c4a6846773817aa9856c5f979a4",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="git repository root",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--range",
        dest="rev_range",
        help="git revision range to check (e.g. BASE..HEAD)",
    )
    group.add_argument(
        "--commit",
        help="single commit SHA to check",
    )
    args = parser.parse_args(argv)

    if not exception_list_is_exact():
        print(
            "DCO exception list drift: expected only a93a5d7 and 4af89ae full SHAs",
            file=sys.stderr,
        )
        return 2

    repo = args.repo.resolve()
    try:
        if args.commit:
            commits = [_run_git(repo, ["rev-parse", args.commit]).strip()]
        else:
            commits = list_commits(repo, args.rev_range)
    except DCOError as error:
        print(f"DCO check failed: {error}", file=sys.stderr)
        return 1

    if not commits:
        print(f"No commits in range {args.rev_range}; nothing to check.")
        return 0

    failed = 0
    for sha in commits:
        try:
            print(validate_commit(repo, sha))
        except DCOError as error:
            print(str(error), file=sys.stderr)
            failed = 1

    if failed:
        print(
            "Use: git commit -s (sign-off must match author or committer identity).",
            file=sys.stderr,
        )
        print(
            "Exception list is fixed to a93a5d7 and 4af89ae only; do not expand it.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
