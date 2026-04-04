"""Web clipping pipeline: fetch page with Playwright, extract content, download images."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from ..core.config import settings
from ..models.bookmark import Bookmark, BookmarkMetadata, ClipStatus

logger = logging.getLogger(__name__)

_TIMEOUT_MS = 30_000
_IMAGE_MIN_BYTES = 5 * 1024  # Skip images smaller than 5KB
_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # Skip images larger than 10MB
_CLIP_CONTENT_MAX = 500 * 1024  # Truncate clip content at 500KB
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".ico"})


async def clip_bookmark(bookmark: Bookmark) -> None:
    """Run the full clipping pipeline for a bookmark.

    1. Update status to processing
    2. Fetch page with Playwright
    3. Capture thumbnail screenshot
    4. Extract article content with trafilatura
    5. Download images and rewrite URLs
    6. Convert to Markdown
    7. Update bookmark with results
    """
    bookmark.clip_status = ClipStatus.processing
    bookmark.clip_error = ""
    await bookmark.save_updated()

    try:
        html, page_url, metadata, screenshot_bytes, readability_html = await _fetch_page(bookmark.url)

        # Update metadata if not already set
        if not bookmark.metadata.meta_title and metadata.meta_title:
            bookmark.metadata = metadata
        if bookmark.title == bookmark.url and metadata.meta_title:
            bookmark.title = metadata.meta_title

        # Save thumbnail
        asset_dir = Path(settings.BOOKMARK_ASSETS_DIR) / str(bookmark.id)
        asset_dir.mkdir(parents=True, exist_ok=True)

        if screenshot_bytes:
            thumb_path = asset_dir / "thumb.jpg"
            await asyncio.to_thread(thumb_path.write_bytes, screenshot_bytes)
            bookmark.thumbnail_path = "thumb.jpg"

        # Extract article content
        # Priority: Readability.js (preserves original HTML structure) > trafilatura XML
        article_html = readability_html
        if not article_html or len(article_html.strip()) < 100:
            extracted = await _extract_content(html, page_url)
            if extracted:
                article_html = extracted

        if not article_html:
            bookmark.clip_status = ClipStatus.failed
            bookmark.clip_error = "No article content could be extracted"
            await bookmark.save_updated()
            return

        # Sanitize HTML (remove script/style/event handlers)
        article_html = _sanitize_html(article_html)

        # Download images and rewrite URLs
        processed_html, local_images = await _process_images(
            article_html, page_url, str(bookmark.id), asset_dir,
        )

        # Truncate if too large
        if len(processed_html.encode("utf-8")) > _CLIP_CONTENT_MAX:
            processed_html = processed_html[:_CLIP_CONTENT_MAX] + "\n\n<!-- truncated -->"

        bookmark.clip_content = processed_html
        bookmark.clip_markdown = await _html_to_markdown(processed_html)
        bookmark.local_images = local_images
        bookmark.clip_status = ClipStatus.done
        await bookmark.save_updated()

        # Publish SSE event
        try:
            from .events import publish_event
            await publish_event(
                str(bookmark.id),
                "bookmark:clipped",
                {"bookmark_id": str(bookmark.id), "status": "done"},
            )
        except Exception:
            pass

        logger.info("Clipped bookmark %s: %s", bookmark.id, bookmark.url)

    except Exception as e:
        logger.exception("Clip failed for bookmark %s", bookmark.id)
        bookmark.clip_status = ClipStatus.failed
        bookmark.clip_error = str(e)[:500]
        await bookmark.save_updated()


def _sanitize_html(html: str) -> str:
    """Remove dangerous elements/attributes from HTML while preserving structure."""
    import re

    # Remove <script> and <style> tags with content
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove event handler attributes (onclick, onerror, onload, etc.)
    html = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', '', html, flags=re.IGNORECASE)
    html = re.sub(r'\s+on\w+\s*=\s*\S+', '', html, flags=re.IGNORECASE)

    # Remove javascript: URLs
    html = re.sub(r'href\s*=\s*["\']javascript:[^"\']*["\']', 'href="#"', html, flags=re.IGNORECASE)

    # Remove <iframe> (except youtube/vimeo)
    html = re.sub(
        r'<iframe(?![^>]*(?:youtube|vimeo))[^>]*>.*?</iframe>',
        '', html, flags=re.DOTALL | re.IGNORECASE,
    )

    return html


async def _fetch_page(
    url: str,
) -> tuple[str, str, BookmarkMetadata, bytes | None, str | None]:
    """Fetch page using Playwright.

    Returns (full_html, final_url, metadata, screenshot_bytes, readability_html).
    readability_html is the article extracted by Readability.js (None if extraction fails).
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="ja-JP",
            )
            page = await context.new_page()

            await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
            final_url = page.url

            # Extract metadata from page
            meta = await _extract_page_metadata(page, final_url)

            # Capture screenshot
            screenshot = await page.screenshot(type="jpeg", quality=80)

            # Get full HTML
            html = await page.content()

            # Extract article HTML via Readability.js (injected inline)
            readability_html = await _extract_with_readability(page)

            await context.close()
            return html, final_url, meta, screenshot, readability_html
        finally:
            await browser.close()


async def _extract_with_readability(page) -> str | None:
    """Inject Readability.js into the page and extract article HTML."""
    try:
        result = await page.evaluate("""() => {
            // Minimal Readability.js implementation (Mozilla algorithm, simplified)
            // Clone the document to avoid mutating the live page
            const doc = document.cloneNode(true);

            // Remove unwanted elements
            const removeSelectors = [
                'script', 'style', 'nav', 'footer', 'header',
                '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
                '.sidebar', '.side-bar', '.ad', '.advertisement', '.social-share',
                '.share-buttons', '.comments-section', '.related-posts',
                'iframe:not([src*="youtube"]):not([src*="vimeo"])',
            ];
            removeSelectors.forEach(sel => {
                doc.querySelectorAll(sel).forEach(el => el.remove());
            });

            // Try to find the main article content
            const articleSelectors = [
                'article',
                '[role="main"]',
                'main',
                '.article-body',
                '.article-content',
                '.post-content',
                '.entry-content',
                '.content',
                '#content',
                '.note-body',          // note.com
                '.znc',                // zenn.dev
                '.article__body',
                '.md-html',
            ];

            let article = null;
            for (const sel of articleSelectors) {
                const el = doc.querySelector(sel);
                if (el && el.textContent.trim().length > 200) {
                    article = el;
                    break;
                }
            }

            if (!article) {
                // Fallback: find the largest text block
                const candidates = doc.querySelectorAll('div, section');
                let best = null;
                let bestLen = 0;
                candidates.forEach(el => {
                    const len = el.textContent.trim().length;
                    if (len > bestLen) {
                        bestLen = len;
                        best = el;
                    }
                });
                if (best && bestLen > 200) {
                    article = best;
                }
            }

            if (!article) return null;

            // Clean up remaining unwanted elements within article
            article.querySelectorAll(
                'script, style, .share-buttons, .social-share'
            ).forEach(el => el.remove());

            return article.innerHTML;
        }""")
        return result
    except Exception:
        return None


async def _extract_page_metadata(page, url: str) -> BookmarkMetadata:
    """Extract metadata from a Playwright page."""
    try:
        data = await page.evaluate("""() => {
            const getMeta = (name) => {
                const el = document.querySelector(`meta[property="${name}"], meta[name="${name}"]`);
                return el ? el.getAttribute('content') || '' : '';
            };
            const getLink = (rel) => {
                const el = document.querySelector(`link[rel*="${rel}"]`);
                return el ? el.getAttribute('href') || '' : '';
            };
            return {
                title: document.title || '',
                og_title: getMeta('og:title'),
                og_description: getMeta('og:description'),
                description: getMeta('description'),
                og_image: getMeta('og:image'),
                site_name: getMeta('og:site_name'),
                author: getMeta('author') || getMeta('article:author'),
                published: getMeta('article:published_time'),
                favicon: getLink('icon'),
            };
        }""")

        favicon = data.get("favicon", "")
        if favicon and not favicon.startswith(("http://", "https://")):
            favicon = urljoin(url, favicon)

        return BookmarkMetadata(
            meta_title=data.get("og_title") or data.get("title", ""),
            meta_description=data.get("og_description") or data.get("description", ""),
            favicon_url=favicon,
            og_image_url=data.get("og_image", ""),
            site_name=data.get("site_name", ""),
            author=data.get("author", ""),
            published_date=data.get("published") or None,
        )
    except Exception:
        return BookmarkMetadata()


async def _extract_content(html: str, url: str) -> str | None:
    """Extract article content from HTML using trafilatura.

    trafilatura's HTML output drops images, so we use the XML output
    and convert it to simple HTML with <graphic> → <img> replacement.
    """
    try:
        import re
        import trafilatura

        # Use XML output which preserves images as <graphic> tags in correct positions
        result_xml = await asyncio.to_thread(
            trafilatura.extract,
            html,
            url=url,
            output_format="xml",
            include_images=True,
            include_links=True,
            include_tables=True,
            favor_recall=True,
        )

        if not result_xml:
            return None

        return _xml_to_html(result_xml)
    except Exception:
        logger.warning("trafilatura extraction failed for %s", url, exc_info=True)
        return None


def _xml_to_html(xml: str) -> str:
    """Convert trafilatura XML output to simple HTML.

    Handles: <p>, <head rend="h2">, <hi rend="bold|italic">,
    <ref target="...">, <graphic src="..." alt="..."/>,
    <list><item>, <lb/>, <row><cell> (tables).
    """
    import re

    out: list[str] = []

    # Remove the XML declaration and <doc> wrapper
    body = re.sub(r'<\?xml[^>]*\?>\s*', '', xml)
    body = re.sub(r'<doc[^>]*>', '', body)
    body = re.sub(r'</doc>', '', body)

    # Convert <graphic> to <img>, skipping duplicates (keep first occurrence)
    _seen_srcs: set[str] = set()

    def _graphic_to_img(m: re.Match) -> str:
        src = m.group(1)
        if src in _seen_srcs:
            return ''  # skip duplicate
        _seen_srcs.add(src)
        alt = m.group(2) or ''
        return f'<p><img src="{src}" alt="{alt}" /></p>'

    body = re.sub(
        r'<graphic\s+src=["\']([^"\']+)["\'](?:\s+alt=["\']([^"\']*)["\'])?[^/]*/?>',
        _graphic_to_img,
        body,
    )

    # Convert <head rend="h2"> etc to headings
    body = re.sub(r'<head\s+rend="h(\d+)">', r'<h\1>', body)
    body = re.sub(r'<head>', '<h2>', body)
    body = re.sub(r'</head>', lambda m: '</h2>', body)
    # Fix closing tags for headings
    for i in range(1, 7):
        body = re.sub(f'<h{i}>([^<]*)</h2>', f'<h{i}>\\1</h{i}>', body)

    # Convert <hi rend="bold"> → <strong>, <hi rend="italic"> → <em>
    body = re.sub(r'<hi\s+rend="bold">', '<strong>', body)
    body = re.sub(r'<hi\s+rend="italic">', '<em>', body)
    body = re.sub(r'<hi[^>]*>', '<strong>', body)  # fallback
    body = re.sub(r'</hi>', '</strong>', body)

    # Convert <ref target="url">text</ref> → <a href="url">text</a>
    body = re.sub(r'<ref\s+target=["\']([^"\']+)["\']>', r'<a href="\1">', body)
    body = re.sub(r'</ref>', '</a>', body)

    # Convert <lb/> → <br>
    body = re.sub(r'<lb\s*/>', '<br>', body)

    # Convert <list><item> → <ul><li>
    body = body.replace('<list>', '<ul>').replace('</list>', '</ul>')
    body = body.replace('<item>', '<li>').replace('</item>', '</li>')

    # Convert <table><row><cell> → <table><tr><td>
    body = body.replace('<row>', '<tr>').replace('</row>', '</tr>')
    body = body.replace('<cell>', '<td>').replace('</cell>', '</td>')
    body = re.sub(r'<table[^>]*>', '<table>', body)

    # Remove remaining XML-only tags
    body = re.sub(r'</?(?:main|comments|quote)[^>]*>', '', body)

    return body.strip()


async def _process_images(
    html: str,
    page_url: str,
    bookmark_id: str,
    asset_dir: Path,
) -> tuple[str, dict[str, str]]:
    """Download images referenced in HTML and rewrite URLs to local paths.

    Returns (processed_html, {original_url: local_filename}).
    """
    import re

    local_images: dict[str, str] = {}
    # Match both <img src="..."> and markdown ![...](...)
    img_urls: list[str] = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    img_urls += re.findall(r'!\[[^\]]*\]\(([^)]+)\)', html)

    if not img_urls:
        return html, local_images

    # Deduplicate
    unique_urls = list(dict.fromkeys(img_urls))

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=15.0,
        headers={"User-Agent": _USER_AGENT},
    ) as client:
        for img_url in unique_urls:
            try:
                abs_url = img_url if img_url.startswith(("http://", "https://")) else urljoin(page_url, img_url)

                # Validate URL
                parsed = urlparse(abs_url)
                if parsed.scheme not in ("http", "https"):
                    continue

                resp = await client.get(abs_url)
                resp.raise_for_status()

                content = resp.content
                if len(content) < _IMAGE_MIN_BYTES or len(content) > _IMAGE_MAX_BYTES:
                    continue

                # Determine extension from content-type or URL
                ct = resp.headers.get("content-type", "")
                ext = _content_type_to_ext(ct)
                if not ext:
                    url_ext = Path(parsed.path).suffix.lower()
                    ext = url_ext if url_ext in _IMAGE_EXTENSIONS else ".jpg"

                # Hash-based filename
                file_hash = hashlib.sha256(content).hexdigest()[:16]
                filename = f"{file_hash}{ext}"
                filepath = asset_dir / filename

                await asyncio.to_thread(filepath.write_bytes, content)
                local_images[img_url] = filename

                # Rewrite URL in HTML
                local_api_url = f"/api/v1/bookmark-assets/{bookmark_id}/{filename}"
                html = html.replace(img_url, local_api_url)

            except Exception:
                logger.debug("Failed to download image: %s", img_url, exc_info=True)
                continue

    return html, local_images


def _content_type_to_ext(content_type: str) -> str:
    """Map content-type to file extension."""
    ct = content_type.lower().split(";")[0].strip()
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/avif": ".avif",
        "image/x-icon": ".ico",
    }
    return mapping.get(ct, "")


async def _html_to_markdown(html: str) -> str:
    """Convert HTML to Markdown using markdownify."""
    try:
        from markdownify import markdownify

        md = await asyncio.to_thread(
            markdownify,
            html,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style"],
        )
        # Clean up excessive whitespace
        import re
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()
    except Exception:
        logger.warning("markdownify conversion failed", exc_info=True)
        return html
