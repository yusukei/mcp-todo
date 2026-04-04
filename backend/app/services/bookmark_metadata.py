"""Fetch page metadata (title, description, OG tags, favicon) for a URL."""

from __future__ import annotations

import logging
from html.parser import HTMLParser

import httpx

from ..models.bookmark import BookmarkMetadata

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_MAX_HEAD_BYTES = 512 * 1024  # Read at most 512KB for metadata extraction
_USER_AGENT = (
    "Mozilla/5.0 (compatible; MCPTodoBot/1.0; +https://github.com/VTechStudio/claude-todo)"
)


class _MetaParser(HTMLParser):
    """Lightweight HTML parser to extract <title>, <meta>, and <link rel="icon">."""

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.og_title = ""
        self.og_description = ""
        self.og_image = ""
        self.og_site_name = ""
        self.og_author = ""
        self.og_published = ""
        self.favicon = ""
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}

        if tag == "title":
            self._in_title = True
            self._title_parts = []
            return

        if tag == "meta":
            name = attr_dict.get("name", "").lower()
            prop = attr_dict.get("property", "").lower()
            content = attr_dict.get("content", "")

            if name == "description":
                self.meta_description = content
            elif prop == "og:title":
                self.og_title = content
            elif prop == "og:description":
                self.og_description = content
            elif prop == "og:image":
                self.og_image = content
            elif prop == "og:site_name":
                self.og_site_name = content
            elif name == "author" or prop == "article:author":
                self.og_author = content
            elif prop in ("article:published_time", "article:published"):
                self.og_published = content

        if tag == "link":
            rel = attr_dict.get("rel", "").lower()
            href = attr_dict.get("href", "")
            if "icon" in rel and href and not self.favicon:
                self.favicon = href

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title" and self._in_title:
            self._in_title = False
            self.title = "".join(self._title_parts).strip()


async def fetch_metadata(url: str) -> BookmarkMetadata:
    """Fetch page metadata from a URL using httpx.

    Falls back gracefully if the page is unreachable.
    """
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()

            # Only parse HTML content
            ct = resp.headers.get("content-type", "")
            if "html" not in ct.lower():
                return BookmarkMetadata()

            text = resp.text[:_MAX_HEAD_BYTES]

        parser = _MetaParser()
        parser.feed(text)

        # Resolve relative favicon URL
        favicon = parser.favicon
        if favicon and not favicon.startswith(("http://", "https://", "//")):
            from urllib.parse import urljoin
            favicon = urljoin(url, favicon)

        return BookmarkMetadata(
            meta_title=parser.og_title or parser.title,
            meta_description=parser.og_description or parser.meta_description,
            favicon_url=favicon,
            og_image_url=parser.og_image,
            site_name=parser.og_site_name,
            author=parser.og_author,
            published_date=parser.og_published or None,
        )

    except Exception:
        logger.warning("Failed to fetch metadata for %s", url, exc_info=True)
        return BookmarkMetadata()
