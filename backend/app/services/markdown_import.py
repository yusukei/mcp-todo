"""Helpers for importing Markdown files into Documents and Knowledge entries.

Supports a minimal subset of YAML frontmatter so users can ship files like:

    ---
    title: Authentication design
    tags: [auth, security]
    category: design
    ---

    # Auth design

    The login flow ...

When frontmatter is absent or malformed, the file name (without `.md`) is
used as the title, the body becomes the content as-is, and tags/category
fall back to caller-supplied defaults.

The parser is intentionally **not** a full YAML implementation — it
recognises only the keys we need (`title`, `tags`, `category`) and the
two list spellings used by frontmatter authors in practice:

    tags: [a, b, c]
    tags:
      - a
      - b
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath


@dataclass
class ParsedMarkdown:
    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    category: str | None = None


_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<fm>.*?)\r?\n---\s*\r?\n?(?P<body>.*)\Z",
    re.DOTALL,
)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_inline_list(value: str) -> list[str]:
    """Parse `[a, b, c]` style inline lists."""
    inner = value.strip()
    if not (inner.startswith("[") and inner.endswith("]")):
        return []
    inner = inner[1:-1]
    return [_strip_quotes(part) for part in inner.split(",") if part.strip()]


def _parse_frontmatter(text: str) -> dict[str, object]:
    """Tiny line-based YAML parser covering scalars + (inline|block) lists."""
    out: dict[str, object] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Skip blank / comment lines
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if ":" not in line:
            i += 1
            continue

        key, _, raw_value = line.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()

        # Inline value (scalar or [a, b])
        if raw_value:
            if raw_value.startswith("[") and raw_value.endswith("]"):
                out[key] = _parse_inline_list(raw_value)
            else:
                out[key] = _strip_quotes(raw_value)
            i += 1
            continue

        # Block list — collect subsequent `  - item` lines.
        items: list[str] = []
        j = i + 1
        while j < len(lines):
            sub = lines[j]
            if not sub.strip():
                j += 1
                continue
            m = re.match(r"^\s+-\s*(.+?)\s*$", sub)
            if not m:
                break
            items.append(_strip_quotes(m.group(1)))
            j += 1
        if items:
            out[key] = items
        i = j

    return out


def parse_markdown_file(filename: str, raw: str) -> ParsedMarkdown:
    """Parse a Markdown file body and return its title/content/tags/category.

    `filename` is used as the fallback title (without the `.md` extension)
    and is sanitized to avoid path traversal — only the basename is kept.
    """
    safe_name = PurePosixPath(filename).name
    fallback_title = safe_name
    if fallback_title.lower().endswith(".md"):
        fallback_title = fallback_title[:-3]
    if fallback_title.lower().endswith(".markdown"):
        fallback_title = fallback_title[:-9]
    fallback_title = fallback_title.strip() or "Untitled"

    match = _FRONTMATTER_RE.match(raw)
    if not match:
        return ParsedMarkdown(title=fallback_title, content=raw)

    body = match.group("body").lstrip("\n")
    try:
        meta = _parse_frontmatter(match.group("fm"))
    except Exception:
        return ParsedMarkdown(title=fallback_title, content=raw)

    title_raw = meta.get("title")
    title = title_raw.strip() if isinstance(title_raw, str) and title_raw.strip() else fallback_title

    tags_raw = meta.get("tags")
    if isinstance(tags_raw, list):
        tags = [t.strip().lower() for t in tags_raw if isinstance(t, str) and t.strip()]
    elif isinstance(tags_raw, str) and tags_raw.strip():
        # Comma-separated fallback for `tags: a, b, c`
        tags = [t.strip().lower() for t in tags_raw.split(",") if t.strip()]
    else:
        tags = []

    category_raw = meta.get("category")
    category = category_raw.strip() if isinstance(category_raw, str) and category_raw.strip() else None

    return ParsedMarkdown(title=title, content=body, tags=tags, category=category)
