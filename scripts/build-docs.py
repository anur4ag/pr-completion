#!/usr/bin/env python3
"""Build the static GitHub Pages site from docs/*.md.

Stdlib only. Output defaults to docs/_site/ (gitignored).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path


def plugin_root_from(start: Path) -> Path:
    candidate = start.resolve()
    if candidate.is_file():
        candidate = candidate.parent
    for path in (candidate, *candidate.parents):
        if (path / "docs" / "site.json").is_file() and (path / "skills").is_dir():
            return path
    raise SystemExit(f"could not locate plugin root from {start}")


def load_site_config(docs_dir: Path) -> dict:
    path = docs_dir / "site.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SystemExit(f"could not read {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    return payload


def strip_front_matter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5 :]
    return text


def inline_md(text: str) -> str:
    """Render a constrained inline Markdown subset to HTML."""
    # Escape first, then re-introduce intentional markup via placeholders.
    escaped = html.escape(text)

    def code_sub(match: re.Match[str]) -> str:
        return f"<code>{match.group(1)}</code>"

    escaped = re.sub(r"`([^`]+)`", code_sub, escaped)

    def link_sub(match: re.Match[str]) -> str:
        label = match.group(1)
        href = match.group(2)
        return f'<a href="{html.escape(href, quote=True)}">{label}</a>'

    escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link_sub, escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    return escaped


def is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|")


def is_table_divider(line: str) -> bool:
    stripped = line.strip().strip("|").replace("|", "").replace("-", "").replace(":", "").strip()
    return is_table_row(line) and stripped == "" and "-" in line


def parse_table(lines: list[str], start: int) -> tuple[str, int]:
    rows: list[list[str]] = []
    index = start
    while index < len(lines) and is_table_row(lines[index]):
        if is_table_divider(lines[index]):
            index += 1
            continue
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        rows.append(cells)
        index += 1
    if not rows:
        return "", start + 1
    header = rows[0]
    body = rows[1:]
    parts = ["<table>", "<thead><tr>"]
    parts.extend(f"<th>{inline_md(cell)}</th>" for cell in header)
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        # Pad short rows to header width for stable HTML.
        padded = row + [""] * max(0, len(header) - len(row))
        parts.extend(f"<td>{inline_md(cell)}</td>" for cell in padded[: len(header)])
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts), index


def markdown_to_html(text: str) -> str:
    lines = strip_front_matter(text).replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    in_paragraph = False

    def close_paragraph() -> None:
        nonlocal in_paragraph
        if in_paragraph:
            out.append("</p>")
            in_paragraph = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped == "":
            close_paragraph()
            index += 1
            continue

        if stripped.startswith("```"):
            close_paragraph()
            lang = stripped[3:].strip()
            index += 1
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            class_attr = f' class="language-{html.escape(lang)}"' if lang else ""
            code = html.escape("\n".join(code_lines))
            out.append(f"<pre><code{class_attr}>{code}</code></pre>")
            continue

        if stripped.startswith("<div") or stripped.startswith("</div") or stripped.startswith("<"):
            # Allow a small set of raw HTML blocks used in docs (callouts).
            close_paragraph()
            out.append(line)
            index += 1
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            close_paragraph()
            level = len(heading.group(1))
            heading_text = heading.group(2)
            heading_id = re.sub(r"[^a-z0-9]+", "-", heading_text.lower()).strip("-")
            id_attr = f' id="{html.escape(heading_id, quote=True)}"' if heading_id else ""
            out.append(f"<h{level}{id_attr}>{inline_md(heading_text)}</h{level}>")
            index += 1
            continue

        if stripped.startswith(">"):
            close_paragraph()
            quote_lines: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote_lines.append(lines[index].strip()[1:].lstrip())
                index += 1
            out.append("<blockquote><p>" + inline_md(" ".join(quote_lines)) + "</p></blockquote>")
            continue

        if is_table_row(stripped) and index + 1 < len(lines) and is_table_divider(lines[index + 1]):
            close_paragraph()
            table_html, index = parse_table(lines, index)
            out.append(table_html)
            continue

        unordered = re.match(r"^[-*]\s+(.*)$", stripped)
        if unordered:
            close_paragraph()
            out.append("<ul>")
            while index < len(lines):
                item = re.match(r"^[-*]\s+(.*)$", lines[index].strip())
                if not item:
                    break
                out.append(f"<li>{inline_md(item.group(1))}</li>")
                index += 1
            out.append("</ul>")
            continue

        ordered = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if ordered:
            close_paragraph()
            out.append("<ol>")
            while index < len(lines):
                item = re.match(r"^(\d+)\.\s+(.*)$", lines[index].strip())
                if not item:
                    break
                out.append(f"<li>{inline_md(item.group(2))}</li>")
                index += 1
            out.append("</ol>")
            continue

        if not in_paragraph:
            out.append("<p>")
            in_paragraph = True
            out.append(inline_md(stripped))
        else:
            out.append(" " + inline_md(stripped))
        index += 1

    close_paragraph()
    return "\n".join(out)


def join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if path == "/":
        return f"{base}/" if base else "/"
    if not path.startswith("/"):
        path = "/" + path
    return f"{base}{path}"


def rewrite_repo_links(content_html: str, base_url: str) -> str:
    """Map repository-relative markdown targets used in source to site or repo paths."""
    # ../SECURITY.md and ../LICENSE appear in docs; on the site, point at GitHub raw-ish blob URLs
    # via relative repo links is not available on Pages. Keep anchors as GitHub repository links
    # when the site is served, by rewriting only known legal/support files to repository paths
    # using a data attribute style absolute repository path. For the static site, emit absolute
    # GitHub URLs only for those parent-relative files so link checks remain explicit.
    replacements = {
        'href="../SECURITY.md"': 'href="https://github.com/anur4ag/pr-completion/blob/main/SECURITY.md"',
        'href="../LICENSE"': 'href="https://github.com/anur4ag/pr-completion/blob/main/LICENSE"',
        'href="SECURITY.md"': 'href="https://github.com/anur4ag/pr-completion/blob/main/SECURITY.md"',
        'href="LICENSE"': 'href="https://github.com/anur4ag/pr-completion/blob/main/LICENSE"',
    }
    for old, new in replacements.items():
        content_html = content_html.replace(old, new)

    # Convert sibling .md links to site routes.
    md_link = re.compile(r'href="([A-Za-z0-9_./-]+)\.md(#.*)?"')

    def md_to_route(match: re.Match[str]) -> str:
        name = match.group(1)
        fragment = match.group(2) or ""
        if name.startswith("../") or name.startswith("http"):
            return match.group(0)
        leaf = Path(name).name
        if leaf == "index":
            route = "/"
        else:
            route = f"/{leaf}/"
        return f'href="{join_url(base_url, route)}{fragment}"'

    return md_link.sub(md_to_route, content_html)


def page_output_dir(site_dir: Path, page_path: str) -> Path:
    if page_path == "/":
        return site_dir
    return site_dir / page_path.strip("/")


def render_page(
    *,
    site: dict,
    page: dict,
    body_html: str,
    version: str,
) -> str:
    base_url = str(site["base_url"]).rstrip("/") or ""
    title = f'{page["title"]} · {site["title"]}'
    description = html.escape(str(site.get("description") or ""))
    nav_parts: list[str] = []
    for index, item in enumerate(site.get("nav") or [], start=1):
        href = join_url(base_url, item["path"])
        current = ' aria-current="page"' if item["path"] == page["path"] else ""
        nav_parts.append(
            f'<a href="{html.escape(href, quote=True)}"{current}>'
            f'<span aria-hidden="true">{index:02d}</span>{html.escape(item["label"])}</a>'
        )
    nav_html = "\n".join(nav_parts)
    css_href = join_url(base_url, "/assets/site.css")
    home_href = join_url(base_url, "/")
    repo = html.escape(str(site.get("repository") or ""), quote=True)
    release = html.escape(str(site.get("release") or ""), quote=True)
    public_origin = str(site.get("public_origin") or site.get("planned_origin") or "").rstrip("/")
    canonical = html.escape(
        f'{public_origin}{join_url(base_url, page["path"])}',
        quote=True,
    )
    page_key = Path(str(page["source"])).stem.replace("_", "-")
    body_html = rewrite_repo_links(body_html, base_url)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <meta name="description" content="{description}">
  <meta name="generator" content="pr-completion docs builder">
  <meta name="theme-color" content="#f3f0e5">
  <link rel="canonical" href="{canonical}">
  <link rel="stylesheet" href="{html.escape(css_href, quote=True)}">
</head>
<body>
  <a class="skip-link" href="#main-content">Skip to content</a>
  <div class="site-frame">
    <header class="site-header">
      <div class="utility-bar">
        <span>PR completion / operator manual</span>
        <span>Docs v{html.escape(version)} · Local plugin</span>
      </div>
      <div class="masthead">
        <a class="brand" href="{html.escape(home_href, quote=True)}">
          <span class="brand-mark" aria-hidden="true">PR/C</span>
          <span class="brand-copy">PR Completion<small>Merge-ready automation</small></span>
        </a>
        <a class="repo-link" href="{repo}">Source / GitHub <span aria-hidden="true">↗</span></a>
      </div>
      <nav class="site-nav" aria-label="Primary navigation">
        {nav_html}
      </nav>
    </header>
    <main id="main-content" class="document page-{html.escape(page_key, quote=True)}">
      {body_html}
    </main>
    <footer class="site-footer">
      <div class="footer-stamp"><span>PR/C</span><strong>Stop at ready.</strong></div>
      <div>
        <p class="footer-label">Project</p>
        <a href="{repo}">Repository</a>
        <a href="{release}">Current release</a>
      </div>
      <div>
        <p class="footer-label">Reference</p>
        <a href="{join_url(base_url, '/support/')}">Support</a>
        <a href="{join_url(base_url, '/privacy/')}">Privacy</a>
        <a href="{join_url(base_url, '/terms/')}">Terms</a>
      </div>
      <p class="footer-note">MIT-licensed local plugin by <a href="{html.escape(str(site.get('publisher_url') or ''), quote=True)}">{html.escape(str(site.get('publisher_name') or ''))}</a>.<br>Documentation for v{html.escape(version)}.</p>
    </footer>
  </div>
</body>
</html>
"""


def copy_assets(docs_dir: Path, site_dir: Path) -> None:
    source = docs_dir / "assets"
    if not source.is_dir():
        return
    target = site_dir / "assets"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def build(root: Path, site_dir: Path) -> int:
    docs_dir = root / "docs"
    site = load_site_config(docs_dir)
    version_path = root / "VERSION"
    version = version_path.read_text(encoding="utf-8").strip() if version_path.is_file() else "0.0.0"

    if site_dir.exists():
        shutil.rmtree(site_dir)
    site_dir.mkdir(parents=True)

    pages = site.get("pages")
    if not isinstance(pages, list) or not pages:
        raise SystemExit("docs/site.json pages list is required")

    for page in pages:
        source_name = page["source"]
        source_path = docs_dir / source_name
        if not source_path.is_file():
            raise SystemExit(f"missing docs page source: {source_name}")
        body = markdown_to_html(source_path.read_text(encoding="utf-8"))
        rendered = render_page(site=site, page=page, body_html=body, version=version)
        out_dir = page_output_dir(site_dir, page["path"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text(rendered, encoding="utf-8")

    copy_assets(docs_dir, site_dir)
    # Helpful marker for link checker and Pages artifact inspection.
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (site_dir / "build-meta.json").write_text(
        json.dumps(
            {
                "version": version,
                "base_url": site.get("base_url"),
                "public_origin": site.get("public_origin") or site.get("planned_origin"),
                "page_count": len(pages),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"built {len(pages)} pages into {site_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="plugin repository root (default: discover from this script)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: <root>/docs/_site)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve() if args.root else plugin_root_from(Path(__file__))
    site_dir = args.out.resolve() if args.out else root / "docs" / "_site"
    return build(root, site_dir)


if __name__ == "__main__":
    sys.exit(main())
