"""Twitter/X-specific clipping pipeline.

Bypasses Playwright entirely and uses FxTwitter / oEmbed / syndication APIs
to fetch tweet text, author, date, and media. Produces a tweet marker that
the frontend renders via react-tweet.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from pathlib import Path

import httpx

from ..core.config import settings
from ..models.bookmark import Bookmark, BookmarkMetadata, ClipStatus
from .clip_constants import IMAGE_MAX_BYTES, IMAGE_MIN_BYTES, content_type_to_ext

logger = logging.getLogger(__name__)


_TWITTER_URL_RE = re.compile(
    r"https?://(?:twitter\.com|x\.com)/(\w+)/status/(\d+)"
)


def is_twitter_url(url: str) -> bool:
    """True when the URL points at a tweet on twitter.com or x.com."""
    return bool(_TWITTER_URL_RE.search(url))


def extract_tweet_id(url: str) -> tuple[str, str] | None:
    """Extract (username, tweet_id) from a Twitter/X URL."""
    m = _TWITTER_URL_RE.search(url)
    return (m.group(1), m.group(2)) if m else None


async def clip_twitter(bookmark: Bookmark, log_and_publish_fn) -> None:
    """Clip a Twitter/X tweet using FxTwitter API (fallback: oEmbed).

    Produces a <!--tweet:URL|author|date|text--> marker that the frontend
    renders as a full tweet embed via react-tweet, matching the existing
    embedded tweet format used for tweets found within articles.

    All HTTP traffic for a single clip operation goes through one shared
    ``httpx.AsyncClient`` so TCP / TLS connections to api.fxtwitter.com,
    publish.twitter.com, cdn.syndication.twimg.com, and pbs.twimg.com can
    be reused across the FxTwitter call, fallbacks, and image downloads.
    """
    info = extract_tweet_id(bookmark.url)
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

    # Single shared client for the entire clip operation. ``follow_redirects``
    # is needed for syndication / image hosts; the FxTwitter and oEmbed
    # endpoints don't redirect, so enabling it globally is safe.
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # ── Primary: FxTwitter API ──
        try:
            resp = await client.get(
                f"https://api.fxtwitter.com/{username}/status/{tweet_id}"
            )
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
                    from datetime import UTC, datetime

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
                resp = await client.get(
                    "https://publish.twitter.com/oembed",
                    params={"url": canonical_url},
                )
                if resp.status_code == 200:
                    oembed = resp.json()
                    author_name = oembed.get("author_name", "")
                    # Parse text from blockquote HTML
                    html_block = oembed.get("html", "")
                    paragraphs = re.findall(
                        r"<p[^>]*>(.*?)</p>", html_block, re.DOTALL
                    )
                    tweet_text = "\n".join(
                        re.sub(r"<[^>]+>", "", p).strip() for p in paragraphs
                    ).strip()
                    # Extract date from last <a> in blockquote
                    date_match = re.search(
                        r"<a[^>]*>([^<]*\d{4}[^<]*)</a>\s*</blockquote>", html_block
                    )
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
            syndication_url = (
                f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=0"
            )
            try:
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
                        avatar_url = synd_user["profile_image_url_https"].replace(
                            "_normal", "_400x400"
                        )
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
                resp = await client.get(photo_url)
                if (
                    resp.status_code == 200
                    and IMAGE_MIN_BYTES <= len(resp.content) <= IMAGE_MAX_BYTES
                ):
                    file_hash = hashlib.sha256(resp.content).hexdigest()[:16]
                    ext = content_type_to_ext(resp.headers.get("content-type", "")) or ".jpg"
                    filename = f"{file_hash}{ext}"
                    filepath = asset_dir / filename
                    await asyncio.to_thread(filepath.write_bytes, resp.content)
                    local_images[photo_url] = filename
            except Exception:
                logger.debug("Failed to download tweet image: %s", photo_url)

    # ── Build clip content as tweet marker ──
    text_escaped = tweet_text.replace("|", "｜").replace("\n", " ")
    author_escaped = f"{author_name} ({author_handle})".replace("|", "｜")
    date_escaped = date_str.replace("|", "｜")
    marker = (
        f"<!--tweet:{canonical_url}|{author_escaped}|{date_escaped}|{text_escaped}-->"
    )

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

    log_and_publish_fn(bookmark)
