"""Import a documentation site from a local directory.

Expects a structure like:
    docs_ja/
        _sidebar.md          # Top-level navigation
        document/
            discover/
                _sidebar.md   # Section navigation
                page-slug.md
                page-slug/
                    images/
                        img_001.webp
            unity/
                _sidebar.md
                ...
        reference/
            ...
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from ..models.docsite import DocPage, DocSite, DocSiteSection

logger = logging.getLogger(__name__)


# ── Sidebar parser ───────────────────────────────────────────

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def parse_sidebar(text: str) -> list[DocSiteSection]:
    """Parse a _sidebar.md file into a tree of DocSiteSection.

    Supports:
    - `- [Title](path.md)` → link item
    - `- **Title**` → group header (no link)
    - Nested lists via indentation (2-space increments)
    - `[← Back](/)` lines are skipped
    """
    lines = text.splitlines()
    root: list[DocSiteSection] = []
    stack: list[tuple[int, list[DocSiteSection]]] = [(-1, root)]

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            continue

        # Skip back links
        if "← Back" in stripped or "←" in stripped:
            continue

        # Detect indentation level
        indent = len(line) - len(line.lstrip())
        # Remove leading `- ` or `  - `
        content = stripped.lstrip("- ").strip()
        if not content:
            continue

        # Parse content
        link_match = _LINK_RE.search(content)
        bold_match = _BOLD_RE.search(content)

        if link_match:
            title = link_match.group(1)
            path_str = link_match.group(2)
            # Normalize path: strip .md extension
            if path_str.endswith(".md"):
                path_str = path_str[:-3]
            section = DocSiteSection(title=title, path=path_str)
        elif bold_match:
            title = bold_match.group(1)
            section = DocSiteSection(title=title, path=None)
        else:
            # Plain text items (e.g. "Window", "Scene Capture") — group headers
            section = DocSiteSection(title=content, path=None)

        # Find parent list by indent level
        while len(stack) > 1 and stack[-1][0] >= indent:
            stack.pop()

        parent_list = stack[-1][1]
        parent_list.append(section)
        stack.append((indent, section.children))

    return root


def _collect_all_sidebars(docs_dir: Path) -> dict[str, list[DocSiteSection]]:
    """Find all _sidebar.md files and parse them.

    Returns a dict mapping section prefix (e.g. "document/discover")
    to its parsed sections.
    """
    sidebars: dict[str, list[DocSiteSection]] = {}
    for sidebar_path in sorted(docs_dir.rglob("_sidebar.md")):
        rel = sidebar_path.parent.relative_to(docs_dir)
        prefix = str(rel).replace("\\", "/")
        if prefix == ".":
            prefix = ""
        text = sidebar_path.read_text(encoding="utf-8")
        sidebars[prefix] = parse_sidebar(text)
    return sidebars


def _build_top_sections(docs_dir: Path) -> list[DocSiteSection]:
    """Build the complete navigation tree from all _sidebar.md files.

    The top-level _sidebar.md defines sections like:
      - [概要・入門](document/discover/pico-os-6-overview.md)
      - [Unity SDK](document/unity-swan/about-the-pico-unity-sdk.md)

    Each section has its own _sidebar.md with detailed navigation.
    We replace the top-level link items with the detailed sub-sidebar.
    """
    sidebars = _collect_all_sidebars(docs_dir)

    # If we have a root sidebar, use it as the skeleton
    top_sidebar = sidebars.get("", [])
    if not top_sidebar:
        # No root sidebar — build from sub-sidebars
        for prefix, sections in sorted(sidebars.items()):
            if prefix:
                top_sidebar.append(DocSiteSection(title=prefix, path=None, children=sections))
        return top_sidebar

    # For each top-level item, try to find and attach its detailed sidebar
    result: list[DocSiteSection] = []
    for item in top_sidebar:
        if item.path:
            # Figure out which sub-sidebar this belongs to
            # e.g. path "document/discover/pico-os-6-overview" → prefix "document/discover"
            parts = item.path.rsplit("/", 1)
            prefix = parts[0] if len(parts) > 1 else ""

            sub_sections = sidebars.get(prefix)
            if sub_sections:
                # Use the top-level item title as section header, with sub-sidebar as children
                result.append(DocSiteSection(title=item.title, path=None, children=sub_sections))
            else:
                result.append(item)
        else:
            result.append(item)

    return result


# ── Content preprocessing ────────────────────────────────────

_TABLE_START_RE = re.compile(r"^\|")
_LIST_LINE_RE = re.compile(r"^(\s*[-*])\s|^(\s*\d+\.)\s")


def preprocess_markdown(content: str) -> str:
    """Fix common Markdown issues that break rendering.

    - Insert blank line between list items and tables so the parser
      treats the table as a separate block (not inline in the list).
    """
    lines = content.split("\n")
    result: list[str] = []
    for i, line in enumerate(lines):
        result.append(line)
        # If this line is a list item and the next line starts a table, insert blank line
        if (
            i + 1 < len(lines)
            and _LIST_LINE_RE.match(line)
            and _TABLE_START_RE.match(lines[i + 1])
        ):
            result.append("")
    return "\n".join(result)


# ── Import ───────────────────────────────────────────────────

async def import_docsite(
    name: str,
    docs_dir: Path,
    assets_dir: Path,
    source_url: str = "",
    description: str = "",
) -> DocSite:
    """Import a documentation directory into the database.

    Args:
        name: Display name for the doc site
        docs_dir: Path to the docs directory (e.g. docs_ja/)
        assets_dir: Base path for storing assets (e.g. /data/docsite_assets)
        source_url: Original source URL
        description: Site description
    """
    # Build navigation tree
    sections = _build_top_sections(docs_dir)

    # Create DocSite
    site = DocSite(
        name=name,
        description=description,
        source_url=source_url,
        sections=sections,
    )
    await site.insert()
    site_id = str(site.id)

    # Import pages and copy assets
    page_count = 0
    site_assets_dir = Path(assets_dir) / site_id
    site_assets_dir.mkdir(parents=True, exist_ok=True)

    md_files = sorted(docs_dir.rglob("*.md"))
    for i, md_path in enumerate(md_files):
        # Skip sidebar / readme files
        if md_path.name.startswith("_") or md_path.name.lower() == "readme.md":
            continue

        rel_path = md_path.relative_to(docs_dir)
        # Path without .md extension, forward slashes
        page_path = str(rel_path.with_suffix("")).replace("\\", "/")

        content = preprocess_markdown(md_path.read_text(encoding="utf-8"))

        # Extract title from first heading
        title = page_path.rsplit("/", 1)[-1]  # fallback
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        # Rewrite image paths to API URL
        # Images are referenced as `slug/images/img_001.webp` or `./images/img_001.webp`
        # We need them as relative to the page's directory for the asset copy
        page_dir = md_path.parent
        images_dir = page_dir / md_path.stem / "images"
        if not images_dir.exists():
            # Also try sibling pattern: same-name directory
            images_dir = page_dir / md_path.stem / "images"

        # Copy images if they exist
        slug_dir = page_dir / md_path.stem
        if slug_dir.is_dir():
            dest_dir = site_assets_dir / str(rel_path.parent) / md_path.stem
            if slug_dir.exists():
                shutil.copytree(str(slug_dir), str(dest_dir), dirs_exist_ok=True)

        page = DocPage(
            site_id=site_id,
            path=page_path,
            title=title,
            content=content,
            sort_order=i,
        )
        await page.insert()
        page_count += 1

        if page_count % 50 == 0:
            logger.info("Imported %d pages...", page_count)

    # Update page count
    site.page_count = page_count
    await site.save_updated()

    logger.info("Imported DocSite '%s': %d pages, assets in %s", name, page_count, site_assets_dir)

    # Build search index
    from .docsite_search import DocSiteSearchIndexer
    indexer = DocSiteSearchIndexer.get_instance()
    if indexer:
        await indexer.rebuild()

    return site
