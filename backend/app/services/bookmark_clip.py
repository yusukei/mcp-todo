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

    # Twitter/X: bypass Playwright entirely
    if _is_twitter_url(bookmark.url):
        try:
            await _clip_twitter(bookmark)
            return
        except Exception as e:
            logger.exception("Twitter clip failed for bookmark %s", bookmark.id)
            bookmark.clip_status = ClipStatus.failed
            bookmark.clip_error = str(e)[:500]
            await bookmark.save_updated()
            return

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

        # Default: trafilatura extraction → always Markdown
        import re as _re
        source_html = raw_html or html

        # Replace Twitter blockquotes with placeholders that trafilatura will preserve.
        # This keeps tweet URLs at their original position in the article.
        _tweet_placeholders: dict[str, str] = {}
        _tweet_counter = [0]

        def _replace_tweet(m: _re.Match) -> str:
            block = m.group(1)
            url_match = _re.search(
                r'href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)',
                block,
            )
            if not url_match:
                return ''
            url = _re.sub(r'\?.*$', '', url_match.group(1))

            # Extract tweet text, author, date from the original blockquote
            text_parts = _re.findall(r'<p[^>]*>(.*?)</p>', block, _re.DOTALL)
            tweet_text = '\n'.join(
                _re.sub(r'<[^>]+>', '', p).strip() for p in text_parts
            ).strip()
            author_match = _re.search(r'(?:&mdash;|—)\s*(.+?)(?:<a|$)', block)
            author = _re.sub(r'<[^>]+>', '', author_match.group(1)).strip() if author_match else ''
            date_match = _re.search(r'<a[^>]*>([^<]*\d{4}[^<]*)</a>\s*$', block.strip())
            date_str = date_match.group(1).strip() if date_match else ''

            _tweet_counter[0] += 1
            placeholder = f'TWEETPLACEHOLDER{_tweet_counter[0]}'
            _tweet_placeholders[placeholder] = {
                'url': url,
                'text': tweet_text,
                'author': author,
                'date': date_str,
            }
            return f'<p>{placeholder}</p>'

        source_html = _re.sub(
            r'<blockquote[^>]*class="twitter-tweet"[^>]*>(.*?)</blockquote>',
            _replace_tweet, source_html, flags=_re.DOTALL | _re.IGNORECASE,
        )

        # Extract YouTube video IDs and their surrounding text (for position matching later)
        _yt_embeds: list[dict[str, str]] = []
        _yt_seen: set[str] = set()

        # Match <figure> or <iframe> containing YouTube
        for pattern in [
            r'<figure[^>]*>.*?(?:youtube\.com/embed/|youtu\.be/)([\w-]+).*?</figure>',
            r'<iframe[^>]*(?:youtube\.com/embed/|youtu\.be/)([\w-]+)[^>]*>.*?</iframe>',
        ]:
            for m in _re.finditer(pattern, source_html, flags=_re.DOTALL | _re.IGNORECASE):
                vid = m.group(1)
                if vid in _yt_seen:
                    continue
                _yt_seen.add(vid)
                # Find text AFTER the embed for position matching
                after_raw = source_html[m.end():m.end() + 2000]
                after_raw = _re.sub(r'<script[^>]*>.*?</script>', '', after_raw, flags=_re.DOTALL | _re.IGNORECASE)
                after_raw = _re.sub(r'<style[^>]*>.*?</style>', '', after_raw, flags=_re.DOTALL | _re.IGNORECASE)
                after_plain = _re.sub(r'<[^>]+>', '\n', after_raw)
                after_lines = [l.strip() for l in after_plain.split('\n') if len(l.strip()) > 8]
                after_snippet = after_lines[0][:30] if after_lines else ''
                _yt_embeds.append({'vid': vid, 'after': after_snippet})

        extracted_html = await _extract_content(source_html, page_url)
        if not extracted_html:
            bookmark.clip_status = ClipStatus.failed
            bookmark.clip_error = "No article content could be extracted"
            await bookmark.save_updated()
            return

        processed_html, local_images = await _process_images(
            extracted_html, page_url, str(bookmark.id), asset_dir,
        )
        md_content = await _html_to_markdown(processed_html)

        # Replace placeholders with tweet embed markers (at correct positions)
        # Format: <!--tweet:URL|author|date|text--> detected by frontend
        for placeholder, info in _tweet_placeholders.items():
            # Escape pipe chars in text
            text = info['text'].replace('|', '｜').replace('\n', ' ')
            author = info['author'].replace('|', '｜')
            date = info['date'].replace('|', '｜')
            marker = f'<!--tweet:{info["url"]}|{author}|{date}|{text}-->'
            md_content = md_content.replace(placeholder, marker)

        # Insert YouTube markers at correct positions using text AFTER the embed
        import html as _html_mod
        for yt in _yt_embeds:
            marker = f'\n\n<!--youtube:{yt["vid"]}-->\n\n'
            snippet = yt.get('after', '')
            if snippet and len(snippet) > 5:
                decoded = _html_mod.unescape(snippet)
                escaped = _re.escape(decoded[:20])
                match = _re.search(escaped, md_content)
                if match:
                    # Insert BEFORE the matched text (find start of its line)
                    before_region = md_content[:match.start()]
                    last_newline = before_region.rfind('\n')
                    insert_pos = last_newline + 1 if last_newline >= 0 else 0
                    md_content = md_content[:insert_pos] + marker + md_content[insert_pos:]
                    continue
            # Fallback: append
            md_content += marker

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


# ── Twitter/X extractor ──────────────────────────────────────

import re as _re_module

_TWITTER_URL_RE = _re_module.compile(
    r'https?://(?:twitter\.com|x\.com)/(\w+)/status/(\d+)',
)


def _is_twitter_url(url: str) -> bool:
    return bool(_TWITTER_URL_RE.search(url))


def _extract_tweet_id(url: str) -> tuple[str, str] | None:
    """Extract (username, tweet_id) from a Twitter/X URL."""
    m = _TWITTER_URL_RE.search(url)
    return (m.group(1), m.group(2)) if m else None


async def _clip_twitter(bookmark: Bookmark) -> None:
    """Clip a Twitter/X tweet using FxTwitter API (fallback: oEmbed).

    Produces a <!--tweet:URL|author|date|text--> marker that the frontend
    renders as a full tweet embed via react-tweet, matching the existing
    embedded tweet format used for tweets found within articles.
    """
    info = _extract_tweet_id(bookmark.url)
    if not info:
        raise ValueError(f"Could not parse tweet ID from {bookmark.url}")

    username, tweet_id = info
    canonical_url = f"https://x.com/{username}/status/{tweet_id}"

    tweet_text = ""
    author_name = ""
    author_handle = ""
    date_str = ""
    photos: list[str] = []
    avatar_url = ""

    # ── Primary: FxTwitter API ──
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"https://api.fxtwitter.com/{username}/status/{tweet_id}")
            if resp.status_code == 200:
                data = resp.json().get("tweet", {})
                tweet_text = data.get("text", "")
                author = data.get("author", {})
                author_name = author.get("name", "")
                author_handle = f"@{author.get('screen_name', username)}"
                avatar_url = author.get("avatar_url", "")
                # Format date
                ts = data.get("created_timestamp")
                if ts:
                    from datetime import datetime, UTC
                    dt = datetime.fromtimestamp(ts, tz=UTC)
                    date_str = dt.strftime("%Y-%m-%d %H:%M")
                # Collect photos
                media = data.get("media", {})
                if media:
                    for photo in media.get("photos", []):
                        if photo.get("url"):
                            photos.append(photo["url"])
                    # Video thumbnail
                    for video in media.get("videos", []):
                        if video.get("thumbnail_url"):
                            photos.append(video["thumbnail_url"])
    except Exception:
        logger.warning("FxTwitter API failed for %s, trying oEmbed", bookmark.url)

    # ── Fallback: oEmbed API ──
    if not tweet_text:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    "https://publish.twitter.com/oembed",
                    params={"url": canonical_url},
                )
                if resp.status_code == 200:
                    oembed = resp.json()
                    author_name = oembed.get("author_name", "")
                    # Parse text from blockquote HTML
                    html_block = oembed.get("html", "")
                    import re
                    # Extract text between <p> tags inside the blockquote
                    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html_block, re.DOTALL)
                    tweet_text = "\n".join(
                        re.sub(r'<[^>]+>', '', p).strip() for p in paragraphs
                    ).strip()
                    # Extract date from last <a> in blockquote
                    date_match = re.search(r'<a[^>]*>([^<]*\d{4}[^<]*)</a>\s*</blockquote>', html_block)
                    if date_match:
                        date_str = date_match.group(1).strip()
        except Exception:
            logger.warning("oEmbed API also failed for %s", bookmark.url)

    if not tweet_text:
        raise ValueError("Could not fetch tweet content from any source")

    # ── Update bookmark metadata ──
    if not bookmark.metadata.meta_title:
        bookmark.metadata = BookmarkMetadata(
            meta_title=f"{author_name} ({author_handle})",
            meta_description=tweet_text[:200],
            og_image_url=photos[0] if photos else "",
            site_name="X (Twitter)",
            author=author_name,
        )
    if bookmark.title == bookmark.url:
        bookmark.title = f"{author_name}: {tweet_text[:80]}"

    # ── Download thumbnail (first photo, avatar, or syndication screenshot) ──
    asset_dir = Path(settings.BOOKMARK_ASSETS_DIR) / str(bookmark.id)
    asset_dir.mkdir(parents=True, exist_ok=True)

    # If no photos from FxTwitter, try Twitter syndication API for a tweet screenshot
    if not photos and not avatar_url:
        syndication_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=0"
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(syndication_url)
                if resp.status_code == 200:
                    synd_data = resp.json()
                    # Extract media from syndication response
                    for media_item in synd_data.get("mediaDetails", []):
                        media_url = media_item.get("media_url_https", "")
                        if media_url:
                            photos.append(media_url)
                    # Extract user avatar
                    synd_user = synd_data.get("user", {})
                    if synd_user.get("profile_image_url_https"):
                        avatar_url = synd_user["profile_image_url_https"].replace("_normal", "_400x400")
                    # Fill in missing author info
                    if not author_name and synd_user.get("name"):
                        author_name = synd_user["name"]
                    if author_handle == f"@{username}" and synd_user.get("screen_name"):
                        author_handle = f"@{synd_user['screen_name']}"
        except Exception:
            logger.debug("Syndication API failed for tweet %s", tweet_id)

    thumb_source = photos[0] if photos else avatar_url
    if thumb_source:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(thumb_source)
                if resp.status_code == 200 and len(resp.content) > 1024:
                    thumb_path = asset_dir / "thumb.jpg"
                    await asyncio.to_thread(thumb_path.write_bytes, resp.content)
                    bookmark.thumbnail_path = "thumb.jpg"
        except Exception:
            logger.debug("Failed to download tweet thumbnail for %s", bookmark.id)

    # ── Download media images ──
    local_images: dict[str, str] = {}
    for photo_url in photos:
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(photo_url)
                if resp.status_code == 200 and _IMAGE_MIN_BYTES <= len(resp.content) <= _IMAGE_MAX_BYTES:
                    file_hash = hashlib.sha256(resp.content).hexdigest()[:16]
                    ext = _content_type_to_ext(resp.headers.get("content-type", "")) or ".jpg"
                    filename = f"{file_hash}{ext}"
                    filepath = asset_dir / filename
                    await asyncio.to_thread(filepath.write_bytes, resp.content)
                    local_images[photo_url] = filename
        except Exception:
            logger.debug("Failed to download tweet image: %s", photo_url)

    # ── Build clip content as tweet marker ──
    # Use the same <!--tweet:...--> format as embedded tweets in articles
    text_escaped = tweet_text.replace('|', '｜').replace('\n', ' ')
    author_escaped = f"{author_name} ({author_handle})".replace('|', '｜')
    date_escaped = date_str.replace('|', '｜')
    marker = f'<!--tweet:{canonical_url}|{author_escaped}|{date_escaped}|{text_escaped}-->'

    # Add media images below the marker
    media_md = ""
    for photo_url in photos:
        local = local_images.get(photo_url)
        if local:
            img_url = f"/api/v1/bookmark-assets/{bookmark.id}/{local}"
        else:
            img_url = photo_url
        media_md += f"\n\n![tweet image]({img_url})"

    bookmark.clip_content = marker + media_md
    bookmark.clip_markdown = bookmark.clip_content
    bookmark.local_images = local_images
    bookmark.clip_status = ClipStatus.done
    await bookmark.save_updated()

    _log_and_publish(bookmark)


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
    The caller must call _close_page_ref(page_ref) when done.
    Note: The browser/context are kept alive via _page_cleanup stored on the ref.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().__aenter__()
    browser = None
    context = None
    page = None
    try:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=_USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="ja-JP",
        )
        page = await context.new_page()

        # Store cleanup references early so they can be freed on any failure
        page._clip_cleanup = (context, browser, pw)  # type: ignore[attr-defined]

        try:
            await page.goto(url, wait_until="networkidle", timeout=_TIMEOUT_MS)
        except Exception:
            # Some pages never reach networkidle; try domcontentloaded
            await page.goto(url, wait_until="domcontentloaded", timeout=_TIMEOUT_MS)

        final_url = page.url
        meta = await _extract_page_metadata(page, final_url)
        screenshot = await page.screenshot(type="jpeg", quality=80)
        html = await page.content()

        return html, final_url, meta, screenshot, page
    except Exception:
        # Clean up resources on failure before propagating
        if page and hasattr(page, '_clip_cleanup'):
            await _close_page_ref(page)
        else:
            # page wasn't created or cleanup not set — close manually
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            try:
                await pw.__aexit__(None, None, None)
            except Exception:
                pass
        raise


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
