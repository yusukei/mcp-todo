"""Document export service — Markdown and PDF generation."""

import asyncio
import tempfile
from pathlib import Path

import markdown
from pymdownx import emoji

from ..models.document import ProjectDocument

# Markdown extensions for rich rendering
_MD_EXTENSIONS = [
    "markdown.extensions.tables",
    "markdown.extensions.fenced_code",
    "markdown.extensions.codehilite",
    "markdown.extensions.toc",
    "markdown.extensions.attr_list",
    "markdown.extensions.def_list",
    "pymdownx.tasklist",
    "pymdownx.superfences",
    "pymdownx.emoji",
]

_MD_EXTENSION_CONFIGS = {
    "markdown.extensions.codehilite": {
        "css_class": "highlight",
        "guess_lang": False,
    },
    "pymdownx.superfences": {
        "custom_fences": [
            {
                "name": "mermaid",
                "class": "mermaid",
                "format": lambda source, language, css_class, options, md, **kwargs: (
                    f'<pre class="mermaid">{source}</pre>'
                ),
            }
        ]
    },
    "pymdownx.emoji": {
        "emoji_index": emoji.twemoji,
        "emoji_generator": emoji.to_svg,
    },
}

_CATEGORY_LABELS = {
    "spec": "仕様",
    "design": "設計",
    "api": "API",
    "guide": "ガイド",
    "notes": "ノート",
}

# Mermaid JS CDN
_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

# CSS for PDF rendering
_PDF_CSS = """
@page {
    size: A4;
    margin: 20mm 15mm 20mm 15mm;
}
body {
    font-family: "Noto Sans JP", "Noto Sans CJK JP", "Hiragino Kaku Gothic ProN",
                 "Yu Gothic", "Meiryo", sans-serif;
    font-size: 11pt;
    line-height: 1.7;
    color: #1a1a1a;
    max-width: 100%;
}
h1 { font-size: 22pt; margin-top: 0; padding-bottom: 6px; border-bottom: 2px solid #2563eb; color: #1e3a5f; }
h2 { font-size: 17pt; margin-top: 20px; padding-bottom: 4px; border-bottom: 1px solid #cbd5e1; color: #1e3a5f; }
h3 { font-size: 14pt; margin-top: 16px; color: #334155; }
h4, h5, h6 { font-size: 12pt; margin-top: 12px; color: #475569; }
code {
    font-family: "Consolas", "Menlo", "DejaVu Sans Mono", monospace;
    background: #f1f5f9;
    padding: 2px 5px;
    border-radius: 3px;
    font-size: 0.9em;
}
pre {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    padding: 12px 16px;
    overflow-x: auto;
    font-size: 0.85em;
    line-height: 1.5;
}
pre code {
    background: none;
    padding: 0;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 12px 0;
}
th, td {
    border: 1px solid #cbd5e1;
    padding: 8px 12px;
    text-align: left;
}
th {
    background: #f1f5f9;
    font-weight: 600;
}
tr:nth-child(even) { background: #f8fafc; }
blockquote {
    border-left: 4px solid #2563eb;
    margin: 12px 0;
    padding: 8px 16px;
    background: #eff6ff;
    color: #1e40af;
}
a { color: #2563eb; text-decoration: none; }
ul, ol { padding-left: 24px; }
li { margin: 4px 0; }
hr {
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 24px 0;
}
img, svg { max-width: 100%; height: auto; }
.mermaid { text-align: center; margin: 16px 0; }

/* Cover page */
.doc-cover {
    page-break-before: always;
    page-break-after: always;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    min-height: 80vh;
    text-align: center;
}
.doc-cover:first-child {
    page-break-before: auto;
}
.doc-cover-title {
    font-size: 32pt;
    font-weight: 700;
    color: #1e3a5f;
    margin-bottom: 24px;
    line-height: 1.3;
    border: none;
    padding: 0;
}
.doc-cover-category {
    display: inline-block;
    font-size: 12pt;
    font-weight: 600;
    color: #2563eb;
    background: #eff6ff;
    padding: 6px 20px;
    border-radius: 8px;
    margin-bottom: 16px;
}
.doc-cover-tags {
    font-size: 10pt;
    color: #64748b;
    margin-bottom: 24px;
}
.doc-cover-date {
    font-size: 10pt;
    color: #94a3b8;
}
.doc-cover-version {
    font-size: 10pt;
    color: #94a3b8;
    margin-top: 4px;
}
.doc-cover-divider {
    width: 80px;
    height: 3px;
    background: #2563eb;
    margin: 24px auto;
    border-radius: 2px;
}

/* Content page */
.doc-content {
    page-break-before: auto;
}
.doc-content-header {
    margin-bottom: 8px;
    padding-bottom: 4px;
}
.doc-meta {
    font-size: 9pt;
    color: #64748b;
    margin-bottom: 16px;
}
"""


def _md_to_html(content: str) -> str:
    """Convert Markdown text to HTML fragment."""
    md = markdown.Markdown(
        extensions=_MD_EXTENSIONS,
        extension_configs=_MD_EXTENSION_CONFIGS,
    )
    return md.convert(content)


def export_markdown(documents: list[ProjectDocument]) -> str:
    """Concatenate documents into a single Markdown string."""
    parts: list[str] = []
    for i, doc in enumerate(documents):
        if i > 0:
            parts.append("\n\n---\n\n")
        # Cover section
        category = doc.category.value if doc.category else ""
        cat_label = _CATEGORY_LABELS.get(category, category)
        tags_str = ", ".join(doc.tags) if doc.tags else ""
        date_str = doc.updated_at.strftime("%Y-%m-%d")

        parts.append(f"# {doc.title}\n\n")
        meta = []
        if cat_label:
            meta.append(f"**カテゴリ:** {cat_label}")
        if tags_str:
            meta.append(f"**タグ:** {tags_str}")
        meta.append(f"**更新日:** {date_str}")
        meta.append(f"**バージョン:** v{doc.version}")
        parts.append(" | ".join(meta) + "\n\n---\n\n")
        parts.append(doc.content)
    return "".join(parts)


def _build_html(documents: list[ProjectDocument]) -> str:
    """Build a full HTML page from documents for PDF rendering."""
    sections: list[str] = []
    for i, doc in enumerate(documents):
        html_content = _md_to_html(doc.content)
        category = doc.category.value if doc.category else ""
        cat_label = _CATEGORY_LABELS.get(category, category)
        tags_str = ", ".join(doc.tags) if doc.tags else ""
        date_str = doc.updated_at.strftime("%Y-%m-%d")

        # Cover page
        cover_parts = [f'<div class="doc-cover">']
        if cat_label:
            cover_parts.append(f'<div class="doc-cover-category">{cat_label}</div>')
        cover_parts.append(f'<h1 class="doc-cover-title">{doc.title}</h1>')
        cover_parts.append('<div class="doc-cover-divider"></div>')
        if tags_str:
            cover_parts.append(f'<div class="doc-cover-tags">{tags_str}</div>')
        cover_parts.append(f'<div class="doc-cover-date">{date_str}</div>')
        cover_parts.append(f'<div class="doc-cover-version">v{doc.version}</div>')
        cover_parts.append('</div>')

        # Content page
        cover_parts.append(f'<article class="doc-content">{html_content}</article>')

        sections.append("\n".join(cover_parts))

    body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<style>{_PDF_CSS}</style>
</head>
<body>
{body}
<script src="{_MERMAID_CDN}"></script>
<script>
mermaid.initialize({{ startOnLoad: true, theme: 'neutral', securityLevel: 'loose' }});
</script>
</body>
</html>"""


async def export_pdf(documents: list[ProjectDocument]) -> bytes:
    """Render documents to a single PDF via Playwright."""
    from playwright.async_api import async_playwright

    html = _build_html(documents)

    # Write HTML to temp file so Playwright can load it
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html)
        tmp_path = Path(f.name)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto(tmp_path.as_uri())

            # Wait for Mermaid rendering to complete
            await page.wait_for_function(
                """() => {
                    const els = document.querySelectorAll('.mermaid');
                    if (els.length === 0) return true;
                    return [...els].every(el => el.querySelector('svg'));
                }""",
                timeout=15000,
            )
            # Small extra wait for SVG layout to settle
            await asyncio.sleep(0.3)

            pdf_bytes = await page.pdf(
                format="A4",
                margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                print_background=True,
            )
            await browser.close()

        return pdf_bytes
    finally:
        tmp_path.unlink(missing_ok=True)
