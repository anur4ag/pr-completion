#!/usr/bin/env python3
"""Validate documentation links for the local Pages build.

Two distinct phases (never conflate them in output):

1. **Internal checks** - repository-relative links, built-site routes, and
   manifest/planned-route consistency. These do not require the public origin.
2. **External HTTP checks** - bounded live HEAD/GET for public http(s) links
   that are not on the exact planned-not-live allowlist (ticket-5 URLs).

Planned publication URLs are allowlisted by **exact string match** so they are
skipped until ticket 5 verifies live hosting. Prefix matching is intentionally
not used for that allowlist.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urldefrag, urlparse


HREF_RE = re.compile(r"""(?:href|src)=["']([^"']+)["']""", re.IGNORECASE)
MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
MD_AUTOLINK_RE = re.compile(r"<(https?://[^>]+)>")

# Exact planned-not-live URLs (ticket 5). Do not use prefix matching here.
DEFAULT_PLANNED_NOT_LIVE_URLS = frozenset(
    {
        "https://anur4ag.github.io/pr-completion/",
        "https://anur4ag.github.io/pr-completion/installation/",
        "https://anur4ag.github.io/pr-completion/skills/",
        "https://anur4ag.github.io/pr-completion/support/",
        "https://anur4ag.github.io/pr-completion/privacy/",
        "https://anur4ag.github.io/pr-completion/terms/",
        "https://github.com/anur4ag/pr-completion",
        "https://github.com/anur4ag/pr-completion/issues",
        "https://github.com/anur4ag/pr-completion/security/advisories/new",
        "https://github.com/anur4ag/pr-completion/blob/main/SECURITY.md",
        "https://github.com/anur4ag/pr-completion/blob/main/LICENSE",
        "https://github.com/anur4ag/pr-completion/releases/tag/v0.1.0",
        "https://github.com/anur4ag/pr-completion/compare/v0.1.0...HEAD",
    }
)

USER_AGENT = "pr-completion-docs-link-check/0.1 (+local validation; bounded)"
DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_WORKERS = 6


class HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value for key, value in attrs}
        if tag == "a" and attr_map.get("href"):
            self.hrefs.append(attr_map["href"] or "")
        if tag in {"link", "script", "img"} and attr_map.get("href"):
            self.hrefs.append(attr_map["href"] or "")
        if tag in {"script", "img"} and attr_map.get("src"):
            self.hrefs.append(attr_map["src"] or "")


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "docs" / "site.json").is_file() and (path / "skills").is_dir():
            return path
    raise SystemExit(f"could not locate plugin root from {start}")


def load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SystemExit(f"could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if path == "/":
        return f"{base}/" if base else "/"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def planned_public_url(site: dict, path: str) -> str:
    origin = str(site.get("planned_origin") or "").rstrip("/")
    return f"{origin}{join_url(str(site.get('base_url') or ''), path)}"


def is_http_external(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"}


def is_skip_scheme(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"mailto", "data", "javascript"} or url.startswith("//")


def normalize_href(url: str) -> str:
    """Strip fragments for allowlist and HTTP checks; keep query string."""
    cleaned, _fragment = urldefrag(url.strip())
    return cleaned


def site_planned_urls(site: dict) -> set[str]:
    urls = set(DEFAULT_PLANNED_NOT_LIVE_URLS)
    for page in site.get("pages") or []:
        path = page.get("path")
        if isinstance(path, str):
            urls.add(planned_public_url(site, path))
    urls.add(planned_public_url(site, "/"))
    return urls


def normalize_site_path(base_url: str, href: str, current_page_path: str) -> str | None:
    """Return a site-absolute path (including base_url) for an internal href, or None if skip."""
    if not href or href.startswith("#") or is_skip_scheme(href) or is_http_external(href):
        return None

    parsed = urlparse(href)
    path = unquote(parsed.path or "")
    base = base_url.rstrip("/")

    if path.startswith("/"):
        return path if path.startswith(base) or base == "" else path

    if current_page_path == "/":
        current_dir = base + "/"
    else:
        current_dir = join_url(base_url, current_page_path)
        if not current_dir.endswith("/"):
            current_dir += "/"
    combined = current_dir + path
    parts: list[str] = []
    for part in combined.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    resolved = "/" + "/".join(parts)
    if resolved != "/" and href.endswith("/"):
        resolved += "/"
    return resolved


def site_path_to_file(site_dir: Path, base_url: str, site_path: str) -> Path | None:
    base = base_url.rstrip("/")
    path = site_path
    if base and path.startswith(base):
        path = path[len(base) :] or "/"
    if not path.startswith("/"):
        path = "/" + path

    if path.endswith("/"):
        candidates = [site_dir / path.strip("/") / "index.html"]
    elif path.endswith(".html"):
        candidates = [site_dir / path.lstrip("/")]
    elif path.endswith((".css", ".js", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico")):
        candidates = [site_dir / path.lstrip("/")]
    else:
        candidates = [
            site_dir / path.strip("/") / "index.html",
            site_dir / (path.lstrip("/") + ".html"),
            site_dir / path.lstrip("/"),
        ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def collect_md_links(text: str) -> list[str]:
    links = [match.group(2).strip() for match in MD_LINK_RE.finditer(text)]
    links.extend(match.group(1).strip() for match in MD_AUTOLINK_RE.finditer(text))
    return links


def iter_doc_source_files(root: Path) -> list[Path]:
    files = [
        root / "README.md",
        root / "SECURITY.md",
        root / "CHANGELOG.md",
        root / "LICENSE",
    ]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        files.extend(sorted(p for p in docs_dir.rglob("*") if p.is_file() and p.suffix in {".md", ".html", ".css"}))
        # Exclude generated site from source scans.
        files = [p for p in files if "_site" not in p.parts]
    return [p for p in files if p.is_file()]


def collect_all_hrefs(root: Path, site_dir: Path | None) -> list[tuple[str, str]]:
    """Return (source_label, href) pairs from markdown/html sources and optional built site."""
    found: list[tuple[str, str]] = []
    for path in iter_doc_source_files(root):
        text = path.read_text(encoding="utf-8")
        label = str(path.relative_to(root))
        if path.suffix == ".md":
            for href in collect_md_links(text):
                found.append((label, href))
        else:
            for href in HREF_RE.findall(text):
                found.append((label, href))

    if site_dir and site_dir.is_dir():
        for path in sorted(site_dir.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            label = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            parser = HrefParser()
            parser.feed(text)
            hrefs = set(parser.hrefs)
            hrefs.update(HREF_RE.findall(text))
            for href in hrefs:
                found.append((label, href))
    return found


def check_manifest_urls(root: Path, site: dict, findings: list[str]) -> None:
    expected_home = planned_public_url(site, "/")
    expected_privacy = planned_public_url(site, "/privacy/")
    expected_terms = planned_public_url(site, "/terms/")

    claude = load_json(root / ".claude-plugin" / "plugin.json")
    codex = load_json(root / ".codex-plugin" / "plugin.json")

    for label, payload in (
        (".claude-plugin/plugin.json", claude),
        (".codex-plugin/plugin.json", codex),
    ):
        homepage = payload.get("homepage")
        if homepage != expected_home:
            findings.append(
                f"{label} homepage {homepage!r} does not match planned site {expected_home!r}"
            )

    interface = codex.get("interface")
    if not isinstance(interface, dict):
        findings.append(".codex-plugin/plugin.json missing interface object")
        return

    for field, expected in (
        ("websiteURL", expected_home),
        ("privacyPolicyURL", expected_privacy),
        ("termsOfServiceURL", expected_terms),
    ):
        value = interface.get(field)
        if value != expected:
            findings.append(
                f".codex-plugin/plugin.json interface.{field} {value!r} "
                f"does not match planned {expected!r}"
            )

    support_paths = {page.get("path") for page in site.get("pages") or []}
    for required in ("/", "/installation/", "/skills/", "/support/", "/privacy/", "/terms/"):
        if required not in support_paths:
            findings.append(f"docs/site.json missing required page path {required!r}")


def check_internal_markdown_links(root: Path, findings: list[str]) -> None:
    for path in iter_doc_source_files(root):
        if path.suffix != ".md":
            continue
        text = path.read_text(encoding="utf-8")
        label = path.relative_to(root)
        for href in collect_md_links(text):
            if href.startswith("#") or is_http_external(href) or is_skip_scheme(href):
                continue
            target = href.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                findings.append(f"{label}: link escapes repository: {href}")
                continue
            if not resolved.exists():
                findings.append(f"{label}: broken relative link {href} -> {resolved}")


def check_built_site_internal(
    root: Path, site: dict, site_dir: Path, findings: list[str]
) -> None:
    if not site_dir.is_dir():
        findings.append(
            f"missing built site directory: {site_dir} (run scripts/build-docs.py first)"
        )
        return

    base_url = str(site.get("base_url") or "")
    for page in site.get("pages") or []:
        page_path = page["path"]
        file_path = site_path_to_file(site_dir, base_url, join_url(base_url, page_path))
        if file_path is None:
            file_path = site_path_to_file(site_dir, "", page_path)
        if file_path is None:
            findings.append(f"built site missing page for {page_path}")
            continue

        html_text = file_path.read_text(encoding="utf-8")
        parser = HrefParser()
        parser.feed(html_text)
        hrefs = set(parser.hrefs)
        hrefs.update(HREF_RE.findall(html_text))

        for href in sorted(hrefs):
            if href.startswith("#") or is_skip_scheme(href) or is_http_external(href):
                continue
            normalized = normalize_site_path(base_url, href, page_path)
            if normalized is None:
                continue
            target = site_path_to_file(site_dir, base_url, normalized)
            if target is None:
                target = site_path_to_file(site_dir, "", normalized)
            if target is None and base_url and normalized.startswith(base_url.rstrip("/") + "/"):
                stripped = normalized[len(base_url.rstrip("/")) :] or "/"
                target = site_path_to_file(site_dir, "", stripped)
            if target is None:
                findings.append(
                    f"{file_path.relative_to(root)}: broken internal link {href} "
                    f"(page {page_path})"
                )


def probe_url(url: str, timeout: float) -> tuple[str, str | None]:
    """Return (url, error_message_or_None). Uses HEAD then GET fallback."""
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}

    def once(method: str) -> None:
        request = urllib.request.Request(url, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status is None or int(status) >= 400:
                raise urllib.error.HTTPError(
                    url, int(status or 0), f"HTTP {status}", response.headers, None
                )

    try:
        try:
            once("HEAD")
        except (urllib.error.HTTPError, urllib.error.URLError) as head_error:
            # Some hosts reject HEAD; retry GET once.
            if isinstance(head_error, urllib.error.HTTPError) and head_error.code in {
                403,
                405,
                501,
            }:
                once("GET")
            elif isinstance(head_error, urllib.error.HTTPError) and head_error.code < 400:
                return url, None
            else:
                # GET fallback for other HEAD failures too when body-less HEAD is flaky.
                once("GET")
        return url, None
    except urllib.error.HTTPError as error:
        # 401/403 mean the host is reachable but access-restricted (auth wall or
        # bot filtering from CI egress). That is not a broken/missing page; still
        # fail hard on 404/410 and other transport errors.
        if error.code in {401, 403}:
            return url, None
        return url, f"HTTP {error.code}"
    except urllib.error.URLError as error:
        return url, f"URL error: {error.reason!r}"
    except TimeoutError:
        return url, "timeout"
    except Exception as error:  # noqa: BLE001 - surface unexpected probe failures
        return url, f"{type(error).__name__}: {error}"


def check_external_http_links(
    *,
    hrefs: list[tuple[str, str]],
    planned_not_live: set[str],
    extra_urls: list[str],
    timeout: float,
    max_workers: int,
    findings: list[str],
) -> tuple[int, int, int]:
    """Validate external http(s) links.

    Returns (checked_count, allowlisted_skip_count, unique_external_count).
    """
    # Map normalized URL -> sources that referenced it.
    sources: dict[str, set[str]] = {}

    def add(url: str, source: str) -> None:
        if not is_http_external(url):
            return
        key = normalize_href(url)
        sources.setdefault(key, set()).add(source)

    for source, href in hrefs:
        add(href, source)
    for url in extra_urls:
        add(url, "<injected>")

    allowlisted = 0
    to_check: list[str] = []
    for url in sorted(sources):
        if url in planned_not_live:
            allowlisted += 1
            continue
        to_check.append(url)

    if not to_check:
        return 0, allowlisted, len(sources)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe_url, url, timeout): url for url in to_check}
        for future in as_completed(futures):
            url, error = future.result()
            if error:
                where = ", ".join(sorted(sources.get(url, {"?"})))
                findings.append(f"external HTTP failed for {url} ({error}); sources: {where}")

    return len(to_check), allowlisted, len(sources)


def run_internal_checks(
    root: Path, site: dict, site_dir: Path, findings: list[str]
) -> None:
    check_manifest_urls(root, site, findings)
    check_internal_markdown_links(root, findings)
    check_built_site_internal(root, site, site_dir, findings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin repository root (default: discover from this script)",
    )
    parser.add_argument(
        "--site-dir",
        type=Path,
        default=None,
        help="built site directory (default: <root>/docs/_site)",
    )
    parser.add_argument(
        "--skip-external",
        action="store_true",
        help="run internal checks only (does not claim external validation)",
    )
    parser.add_argument(
        "--extra-url",
        action="append",
        default=[],
        help="inject an extra external URL into the HTTP check (repeatable; for regressions)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"per-URL timeout seconds (default {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"bounded concurrent external probes (default {DEFAULT_MAX_WORKERS})",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve() if args.root else plugin_root_from(Path(__file__))
    site_dir = args.site_dir.resolve() if args.site_dir else root / "docs" / "_site"
    site = load_json(root / "docs" / "site.json")
    planned = site_planned_urls(site)

    internal_findings: list[str] = []
    run_internal_checks(root, site, site_dir, internal_findings)

    external_findings: list[str] = []
    checked = allowlisted = unique_external = 0
    if not args.skip_external:
        hrefs = collect_all_hrefs(root, site_dir)
        checked, allowlisted, unique_external = check_external_http_links(
            hrefs=hrefs,
            planned_not_live=planned,
            extra_urls=list(args.extra_url or []),
            timeout=args.timeout,
            max_workers=max(1, args.max_workers),
            findings=external_findings,
        )

    failed = False
    if internal_findings:
        failed = True
        print("internal docs link check failed:", file=sys.stderr)
        for item in internal_findings:
            print(f"  - {item}", file=sys.stderr)
    else:
        print("internal docs link check passed")
        print(f"  planned home (config only): {planned_public_url(site, '/')}")
        print(f"  planned support (config only): {planned_public_url(site, '/support/')}")
        print(f"  planned privacy (config only): {planned_public_url(site, '/privacy/')}")
        print(f"  planned terms (config only): {planned_public_url(site, '/terms/')}")

    if args.skip_external:
        print("external HTTP link check skipped (--skip-external); not externally validated")
    elif external_findings:
        failed = True
        print("external HTTP link check failed:", file=sys.stderr)
        for item in external_findings:
            print(f"  - {item}", file=sys.stderr)
        print(
            f"external HTTP summary: checked={checked} "
            f"allowlisted_planned_not_live={allowlisted} unique_http={unique_external}",
            file=sys.stderr,
        )
    else:
        print("external HTTP link check passed")
        print(
            f"  checked={checked} allowlisted_planned_not_live={allowlisted} "
            f"unique_http={unique_external}"
        )
        print(
            f"  exact planned-not-live allowlist size={len(planned)} "
            "(skipped; not treated as live)"
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
