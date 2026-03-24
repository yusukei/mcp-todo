"""Task export service — Markdown and PDF generation."""

import asyncio
import tempfile
from pathlib import Path

import markdown
from pymdownx import emoji

from ..models.task import Task

# Reuse same Markdown extensions as document export
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

_STATUS_LABELS = {
    "todo": "TODO",
    "in_progress": "進行中",
    "on_hold": "保留",
    "done": "完了",
    "cancelled": "キャンセル",
}

_PRIORITY_LABELS = {
    "urgent": "緊急",
    "high": "高",
    "medium": "中",
    "low": "低",
}

_MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"

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
pre code { background: none; padding: 0; }
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
th { background: #f1f5f9; font-weight: 600; }
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
hr { border: none; border-top: 1px solid #e2e8f0; margin: 24px 0; }
img, svg { max-width: 100%; height: auto; }
.mermaid { text-align: center; margin: 16px 0; }
.task-separator { page-break-before: always; }
.task-meta {
    font-size: 9pt;
    color: #64748b;
    margin-bottom: 16px;
}
.task-meta span {
    display: inline-block;
    margin-right: 16px;
}
.task-badge {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 9pt;
    font-weight: 600;
}
.badge-status { background: #e2e8f0; color: #475569; }
.badge-priority-urgent { background: #fee2e2; color: #b91c1c; }
.badge-priority-high { background: #ffedd5; color: #c2410c; }
.badge-priority-medium { background: #fef9c3; color: #a16207; }
.badge-priority-low { background: #f1f5f9; color: #64748b; }
.comment-section { margin-top: 16px; }
.comment {
    border-left: 3px solid #e2e8f0;
    padding: 8px 12px;
    margin: 8px 0;
    background: #f8fafc;
    border-radius: 0 6px 6px 0;
}
.comment-author {
    font-size: 9pt;
    color: #64748b;
    margin-bottom: 4px;
}
"""


def _task_to_markdown(task: Task, index: int) -> str:
    """Convert a single Task to Markdown."""
    parts: list[str] = []
    if index > 0:
        parts.append("\n\n---\n\n")

    parts.append(f"# {task.title}\n\n")

    # Metadata line
    meta_parts = []
    status_label = _STATUS_LABELS.get(task.status, task.status)
    priority_label = _PRIORITY_LABELS.get(task.priority, task.priority)
    meta_parts.append(f"**ステータス:** {status_label}")
    meta_parts.append(f"**優先度:** {priority_label}")
    if task.tags:
        meta_parts.append(f"**タグ:** {', '.join(task.tags)}")
    if task.due_date:
        meta_parts.append(f"**期日:** {task.due_date.strftime('%Y-%m-%d')}")
    parts.append(" | ".join(meta_parts) + "\n\n")

    # Description
    if task.description:
        parts.append(f"{task.description}\n")

    # Decision context
    if task.decision_context:
        dc = task.decision_context
        if dc.background:
            parts.append(f"\n## 背景\n\n{dc.background}\n")
        if dc.decision_point:
            parts.append(f"\n## 判断ポイント\n\n{dc.decision_point}\n")
        if dc.options:
            parts.append("\n## 選択肢\n\n")
            for opt in dc.options:
                parts.append(f"- **{opt.label}**")
                if opt.description:
                    parts.append(f": {opt.description}")
                parts.append("\n")

    # Completion report
    if task.completion_report:
        parts.append(f"\n## 完了レポート\n\n{task.completion_report}\n")

    # Comments
    if task.comments:
        parts.append(f"\n## コメント ({len(task.comments)})\n\n")
        for c in task.comments:
            date_str = c.created_at.strftime("%Y-%m-%d %H:%M")
            parts.append(f"> **{c.author_name}** ({date_str})\n>\n")
            for line in c.content.split("\n"):
                parts.append(f"> {line}\n")
            parts.append("\n")

    return "".join(parts)


def export_tasks_markdown(tasks: list[Task]) -> str:
    """Concatenate tasks into a single Markdown string."""
    parts: list[str] = []
    for i, task in enumerate(tasks):
        parts.append(_task_to_markdown(task, i))
    return "".join(parts)


def _md_to_html(content: str) -> str:
    md = markdown.Markdown(
        extensions=_MD_EXTENSIONS,
        extension_configs=_MD_EXTENSION_CONFIGS,
    )
    return md.convert(content)


def _build_tasks_html(tasks: list[Task]) -> str:
    """Build full HTML page from tasks for PDF rendering."""
    sections: list[str] = []
    for i, task in enumerate(tasks):
        separator_cls = ' class="task-separator"' if i > 0 else ""

        # Meta badges
        status_label = _STATUS_LABELS.get(task.status, task.status)
        priority_label = _PRIORITY_LABELS.get(task.priority, task.priority)
        priority_class = f"badge-priority-{task.priority}"

        meta = (
            f'<div class="task-meta">'
            f'<span class="task-badge badge-status">{status_label}</span> '
            f'<span class="task-badge {priority_class}">{priority_label}</span>'
        )
        if task.tags:
            meta += f' <span>タグ: {", ".join(task.tags)}</span>'
        if task.due_date:
            meta += f' <span>期日: {task.due_date.strftime("%Y-%m-%d")}</span>'
        meta += "</div>"

        # Body: description as markdown
        body_parts: list[str] = []
        if task.description:
            body_parts.append(_md_to_html(task.description))

        if task.decision_context:
            dc = task.decision_context
            if dc.background:
                body_parts.append(f"<h3>背景</h3>{_md_to_html(dc.background)}")
            if dc.decision_point:
                body_parts.append(f"<h3>判断ポイント</h3>{_md_to_html(dc.decision_point)}")
            if dc.options:
                opts_html = "<h3>選択肢</h3><ul>"
                for opt in dc.options:
                    desc = f": {opt.description}" if opt.description else ""
                    opts_html += f"<li><strong>{opt.label}</strong>{desc}</li>"
                opts_html += "</ul>"
                body_parts.append(opts_html)

        if task.completion_report:
            body_parts.append(f"<h3>完了レポート</h3>{_md_to_html(task.completion_report)}")

        if task.comments:
            comments_html = f'<div class="comment-section"><h3>コメント ({len(task.comments)})</h3>'
            for c in task.comments:
                date_str = c.created_at.strftime("%Y-%m-%d %H:%M")
                comment_body = _md_to_html(c.content)
                comments_html += (
                    f'<div class="comment">'
                    f'<div class="comment-author">{c.author_name} — {date_str}</div>'
                    f'{comment_body}</div>'
                )
            comments_html += "</div>"
            body_parts.append(comments_html)

        body = "\n".join(body_parts)

        sections.append(
            f'<article{separator_cls}>'
            f'<h1>{task.title}</h1>'
            f'{meta}'
            f'{body}'
            f'</article>'
        )

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


async def export_tasks_pdf(tasks: list[Task]) -> bytes:
    """Render tasks to a single PDF via Playwright."""
    from playwright.async_api import async_playwright

    html = _build_tasks_html(tasks)

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

            await page.wait_for_function(
                """() => {
                    const els = document.querySelectorAll('.mermaid');
                    if (els.length === 0) return true;
                    return [...els].every(el => el.querySelector('svg'));
                }""",
                timeout=15000,
            )
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
