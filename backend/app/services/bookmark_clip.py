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
    4. Check for site-specific extraction rules
    5. Default: trafilatura extraction → Markdown
    6. Site-specific: Playwright DOM extraction → HTML + Markdown
    7. Download images and rewrite URLs
    8. Update bookmark with results
    """
    bookmark.clip_status = ClipStatus.processing
    bookmark.clip_error = ""
    await bookmark.save_updated()

    page_ref = None
    try:
        # Pre-fetch raw HTML (before JS execution) to preserve Twitter/YouTube embeds
        raw_html = await _fetch_raw_html(bookmark.url)

        html, page_url, metadata, screenshot_bytes, page_ref = await _fetch_page(bookmark.url)

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

        # Check for site-specific extraction
        site_extractor = _get_site_extractor(page_url)

        if site_extractor and page_ref:
            site_html = await site_extractor(page_ref, page_url)
            if site_html:
                await _close_page_ref(page_ref)
                page_ref = None
                site_html = _sanitize_html(site_html)
                processed_html, local_images = await _process_images(
                    site_html, page_url, str(bookmark.id), asset_dir,
                )
                bookmark.clip_content = processed_html
                bookmark.clip_markdown = await _html_to_markdown(processed_html)
                bookmark.local_images = local_images
                bookmark.clip_status = ClipStatus.done
                await bookmark.save_updated()
                _log_and_publish(bookmark)
                return

        # Close Playwright before trafilatura (no longer needed)
        await _close_page_ref(page_ref)
        page_ref = None

        # Default: trafilatura extraction → Markdown
        # Use raw HTML (pre-JS) for trafilatura if available, as it preserves embeds
        # Strip Twitter/YouTube embeds before trafilatura to avoid duplicates
        import re as _re
        source_html = raw_html or html
        source_html = _re.sub(
            r'<blockquote[^>]*class="twitter-tweet"[^>]*>.*?</blockquote>',
            '', source_html, flags=_re.DOTALL | _re.IGNORECASE,
        )
        extracted_html = await _extract_content(source_html, page_url)
        if not extracted_html:
            bookmark.clip_status = ClipStatus.failed
            bookmark.clip_error = "No article content could be extracted"
            await bookmark.save_updated()
            return

        # Restore Twitter/YouTube embeds from raw HTML (pre-JS, preserves original blockquotes)
        extracted_html = _restore_embeds(extracted_html, raw_html or html)

        processed_html, local_images = await _process_images(
            extracted_html, page_url, str(bookmark.id), asset_dir,
        )
        md_content = await _html_to_markdown(processed_html)

        if len(md_content.encode("utf-8")) > _CLIP_CONTENT_MAX:
            md_content = md_content[:_CLIP_CONTENT_MAX] + "\n\n...(truncated)"

        bookmark.clip_content = md_content
        bookmark.clip_markdown = md_content
        bookmark.local_images = local_images
        bookmark.clip_status = ClipStatus.done
        await bookmark.save_updated()

        _log_and_publish(bookmark)

    except Exception as e:
        logger.exception("Clip failed for bookmark %s", bookmark.id)
        bookmark.clip_status = ClipStatus.failed
        bookmark.clip_error = str(e)[:500]
        await bookmark.save_updated()
    finally:
        await _close_page_ref(page_ref)


async def _fetch_raw_html(url: str) -> str | None:
    """Fetch raw HTML via httpx (no JS execution). Used to preserve embeds."""
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except Exception:
        return None


def _log_and_publish(bookmark: Bookmark) -> None:
    """Log and publish SSE event for a completed clip."""
    logger.info("Clipped bookmark %s: %s", bookmark.id, bookmark.url)
    try:
        import asyncio
        from .events import publish_event
        asyncio.ensure_future(publish_event(
            str(bookmark.id),
            "bookmark:clipped",
            {"bookmark_id": str(bookmark.id), "status": "done"},
        ))
    except Exception:
        pass


# ── Site-specific extractors ───────────────────────────────

def _get_site_extractor(url: str):
    """Return a site-specific extractor function for the URL, or None."""
    from urllib.parse import urlparse
    domain = urlparse(url).hostname or ""

    if domain in ("zenn.dev", "www.zenn.dev") and "/scraps/" in url:
        return _extract_zenn_scrap

    return None


async def _extract_zenn_scrap(page, url: str) -> str | None:
    """Extract Zenn scrap thread as structured HTML with comment cards."""
    try:
        result = await page.evaluate("""() => {
            const items = document.querySelectorAll('[class*="ScrapThread_item"]');
            if (!items.length) return null;

            let html = '';
            items.forEach(item => {
                const article = item.querySelector('article');
                if (!article) return;

                // User info
                const avatarImg = article.querySelector('[class*="ThreadHeader"] img');
                const userName = article.querySelector('[class*="userName"]');
                const dateEl = article.querySelector('[class*="dateContainer"]');

                const avatar = avatarImg ? avatarImg.src : '';
                const name = userName ? userName.textContent.trim() : '';
                const date = dateEl ? dateEl.textContent.trim() : '';

                // Content (the znc div)
                const content = article.querySelector('[class*="content"] .znc');
                const contentHtml = content ? content.innerHTML : '';

                if (!contentHtml.trim()) return;

                html += '<div class="clip-comment-card">';
                html += '<div class="clip-comment-header">';
                if (avatar) html += '<img class="clip-avatar" src="' + avatar + '" alt="' + name + '" />';
                if (name) html += '<strong>' + name + '</strong>';
                if (date) html += '<span class="clip-date">' + date + '</span>';
                html += '</div>';
                html += '<div class="clip-comment-body">' + contentHtml + '</div>';
                html += '</div>';
            });

            return html || null;
        }""")
        return result
    except Exception:
        logger.warning("Zenn scrap extraction failed for %s", url, exc_info=True)
        return None


def _restore_embeds(extracted_html: str, original_html: str) -> str:
    """Restore Twitter and YouTube embeds that trafilatura stripped.

    Twitter: Extract <blockquote class="twitter-tweet"> from original HTML,
    convert to styled blockquote with tweet link, and insert into extracted HTML.

    YouTube: Extract iframe src or video URLs from original HTML,
    insert as embeddable iframe tags.

    Since trafilatura completely removes tweet content, we use surrounding text
    from the original HTML to find insertion positions in the extracted HTML.
    """
    import re

    # ── Twitter embeds ──────────────────────────────────────
    twitter_pattern = re.compile(
        r'<blockquote[^>]*class="twitter-tweet"[^>]*>(.*?)</blockquote>',
        re.DOTALL | re.IGNORECASE,
    )

    for match in twitter_pattern.finditer(original_html):
        block = match.group(1)

        # Extract tweet URL
        tweet_url_match = re.search(
            r'href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)',
            block,
        )
        tweet_url = tweet_url_match.group(1) if tweet_url_match else ""

        # Extract tweet text
        text_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        tweet_text = re.sub(r'<[^>]+>', '', text_match.group(1)).strip() if text_match else ""

        # Extract author
        author_match = re.search(r'(?:&mdash;|—)\s*(.+?)(?:<a|$)', block)
        author = re.sub(r'<[^>]+>', '', author_match.group(1)).strip() if author_match else ""

        if not tweet_text and not tweet_url:
            continue

        # Build embed HTML that markdownify converts to a clean blockquote
        embed = '<blockquote>'
        embed += f'<p><strong>🐦 {author or "Tweet"}</strong></p>'
        embed += f'<p>{tweet_text}</p>'
        if tweet_url:
            embed += f'<p><a href="{tweet_url}">ツイートを見る</a></p>'
        embed += '</blockquote>'

        # Find insertion point: get text AFTER the tweet in original HTML
        after_pos = match.end()
        after_text = original_html[after_pos:after_pos + 500]
        # Strip tags, take first meaningful text chunk
        after_plain = re.sub(r'<[^>]+>', ' ', after_text).strip()
        after_snippet = after_plain[:40].strip()

        inserted = False
        if after_snippet and len(after_snippet) > 5:
            import html as html_mod
            decoded_snippet = html_mod.unescape(after_snippet[:25])
            escaped = re.escape(decoded_snippet)
            snippet_match = re.search(escaped, extracted_html)
            if snippet_match:
                search_region = extracted_html[:snippet_match.start()]
                last_tag = search_region.rfind('<')
                insert_pos = last_tag if last_tag >= 0 else snippet_match.start()
                extracted_html = extracted_html[:insert_pos] + f'\n{embed}\n' + extracted_html[insert_pos:]
                inserted = True

        if not inserted:
            extracted_html += f'\n{embed}'

    # ── YouTube embeds ──────────────────────────────────────
    yt_iframes = re.findall(
        r'<iframe[^>]*src=["\']([^"\']*(?:youtube\.com/embed|youtu\.be)[^"\']*)["\'][^>]*>',
        original_html,
        re.IGNORECASE,
    )
    yt_watch = re.findall(
        r'https?://(?:www\.)?youtube\.com/watch\?v=([\w-]+)',
        original_html,
    )

    yt_ids: list[str] = []
    for iframe_src in yt_iframes:
        vid_match = re.search(r'embed/([\w-]+)', iframe_src)
        if vid_match and vid_match.group(1) not in yt_ids:
            yt_ids.append(vid_match.group(1))
    for vid in yt_watch:
        if vid not in yt_ids:
            yt_ids.append(vid)

    for vid in yt_ids:
        if vid in extracted_html:
            continue
        watch_url = f"https://www.youtube.com/watch?v={vid}"
        yt_embed = f'<p><a href="{watch_url}">▶️ YouTube: {watch_url}</a></p>'
        extracted_html += f'\n{yt_embed}'

    return extracted_html


def _sanitize_html(html: str) -> str:
    """Remove dangerous elements/attributes and UI decorations from HTML."""
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

    # Remove UI decoration images (copy buttons, icons, etc.) — typically small SVGs
    html = re.sub(
        r'<img[^>]+src=["\'][^"\']*(?:copy-icon|wrap-icon|toggle-|button-|icon[-_])[^"\']*["\'][^>]*/?>',
        '', html, flags=re.IGNORECASE,
    )

    # Remove <button> elements (copy buttons, action buttons from original site)
    html = re.sub(r'<button[^>]*>.*?</button>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove <svg> elements (inline icons)
    html = re.sub(r'<svg[^>]*>.*?</svg>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Remove <input>, <select>, <textarea>, <form>
    html = re.sub(r'<(?:input|select|textarea|form)[^>]*(?:>.*?</(?:select|textarea|form)>|/?>)',
                  '', html, flags=re.DOTALL | re.IGNORECASE)

    return html


async def _fetch_page(
    url: str,
) -> tuple[str, str, BookmarkMetadata, bytes | None, object | None]:
    """Fetch page using Playwright.

    Returns (full_html, final_url, metadata, screenshot_bytes, page_ref).
    page_ref is a Playwright page object for site-specific extractors to use.
    The caller must call page_ref.__close__() when done (handled by clip_bookmark).
    Note: The browser/context are kept alive via _page_cleanup stored on the ref.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().__aenter__()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=_USER_AGENT,
        viewport={"width": 1280, "height": 800},
        locale="ja-JP",
    )
    page = await context.new_page()

    try:
        await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
    except Exception:
        # Some pages never reach networkidle; try domcontentloaded
        await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)

    final_url = page.url
    meta = await _extract_page_metadata(page, final_url)
    screenshot = await page.screenshot(type="jpeg", quality=80)
    html = await page.content()

    # Store cleanup references on page for later disposal
    page._clip_cleanup = (context, browser, pw)  # type: ignore[attr-defined]

    return html, final_url, meta, screenshot, page


async def _close_page_ref(page_ref: object | None) -> None:
    """Clean up Playwright resources from _fetch_page."""
    if page_ref is None:
        return
    try:
        cleanup = getattr(page_ref, '_clip_cleanup', None)
        if cleanup:
            context, browser, pw = cleanup
            await context.close()
            await browser.close()
            await pw.__aexit__(None, None, None)
    except Exception:
        pass


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

    # Convert <quote> → <blockquote>
    body = body.replace('<quote>', '<blockquote>').replace('</quote>', '</blockquote>')

    # Remove remaining XML-only tags
    body = re.sub(r'</?(?:main|comments)[^>]*>', '', body)

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
