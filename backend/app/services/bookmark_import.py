"""Raindrop.io CSV import service for bookmarks."""

from __future__ import annotations

import csv
import html
import io
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

from ..models.bookmark import Bookmark, BookmarkMetadata, ClipStatus

logger = logging.getLogger(__name__)

MAX_ROWS = 10_000
MAX_ERRORS = 50

# Tracking params to strip during URL normalization
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "ref", "ref_src", "ref_url",
})


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication."""
    parsed = urlparse(url)
    # Lowercase hostname
    hostname = (parsed.hostname or "").lower()
    # Strip tracking params
    params = parse_qs(parsed.query, keep_blank_values=False)
    filtered = {k: v for k, v in params.items() if k.lower() not in _TRACKING_PARAMS}
    query = urlencode(filtered, doseq=True)
    # Reconstruct without fragment, strip trailing slash
    path = parsed.path.rstrip("/") or "/"
    normalized = urlunparse((parsed.scheme, hostname, path, "", query, ""))
    return normalized


def _validate_url(url: str) -> bool:
    """Check URL scheme is http or https."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False


def _parse_tags(tags_str: str) -> list[str]:
    """Parse comma-separated tags, strip and lowercase."""
    if not tags_str:
        return []
    return [t.strip().lower() for t in tags_str.split(",") if t.strip()]


def _parse_datetime(dt_str: str) -> datetime:
    """Parse ISO 8601 datetime string."""
    if not dt_str:
        return datetime.now(UTC)
    try:
        # Handle both 'Z' suffix and timezone offset
        dt_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(dt_str)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def parse_raindrop_csv(
    file_content: str,
    project_id: str,
    created_by: str,
    collection_id: str | None = None,
) -> tuple[list[Bookmark], list[dict]]:
    """Parse Raindrop.io CSV and return (bookmarks, errors).

    Returns:
        (bookmarks, errors) where bookmarks is a list of Bookmark documents
        ready for insertion, and errors is a list of {row, error} dicts.
    """
    reader = csv.DictReader(io.StringIO(file_content))
    bookmarks: list[Bookmark] = []
    errors: list[dict] = []
    seen_urls: set[str] = set()

    for row_num, row in enumerate(reader, start=2):  # row 1 is header
        if row_num > MAX_ROWS + 1:
            errors.append({"row": row_num, "error": f"Row limit exceeded ({MAX_ROWS})"})
            break

        url = (row.get("url") or "").strip()
        if not url:
            if len(errors) < MAX_ERRORS:
                errors.append({"row": row_num, "error": "Missing URL"})
            continue

        if not _validate_url(url):
            if len(errors) < MAX_ERRORS:
                errors.append({"row": row_num, "error": f"Invalid URL scheme: {url[:50]}"})
            continue

        # Dedup within CSV
        normalized = normalize_url(url)
        if normalized in seen_urls:
            continue
        seen_urls.add(normalized)

        # Parse fields
        title = html.escape(row.get("title", "").strip()) or url
        note = row.get("note", "").strip()
        excerpt = row.get("excerpt", "").strip()
        description = html.escape(note or excerpt)
        tags = _parse_tags(row.get("tags", ""))
        created_at = _parse_datetime(row.get("created", ""))
        cover = row.get("cover", "").strip()
        is_starred = row.get("favorite", "").lower() == "true"

        bm = Bookmark(
            project_id=project_id,
            url=url,
            title=title,
            description=description,
            tags=tags,
            collection_id=collection_id,
            metadata=BookmarkMetadata(og_image_url=cover),
            clip_status=ClipStatus.pending,
            is_starred=is_starred,
            created_by=created_by,
            created_at=created_at,
            updated_at=created_at,
        )
        bookmarks.append(bm)

    return bookmarks, errors


async def import_bookmarks(
    file_content: str,
    project_id: str,
    created_by: str,
    collection_id: str | None = None,
) -> dict:
    """Parse CSV and insert bookmarks, skipping duplicates.

    Returns:
        {imported, skipped_duplicate, skipped_invalid, errors, total_pending}
    """
    bookmarks, errors = parse_raindrop_csv(
        file_content, project_id, created_by, collection_id,
    )

    if not bookmarks:
        return {
            "imported": 0,
            "skipped_duplicate": 0,
            "skipped_invalid": len(errors),
            "errors": errors,
            "total_pending": 0,
        }

    # Check existing URLs in DB
    urls = [bm.url for bm in bookmarks]
    normalized_map = {normalize_url(u): u for u in urls}

    existing = await Bookmark.find(
        {"project_id": project_id, "url": {"$in": urls}, "is_deleted": False},
    ).to_list()
    existing_normalized = {normalize_url(bm.url) for bm in existing}

    # Filter out duplicates
    to_insert = [
        bm for bm in bookmarks
        if normalize_url(bm.url) not in existing_normalized
    ]
    skipped_duplicate = len(bookmarks) - len(to_insert)

    # Batch insert
    BATCH_SIZE = 200
    imported = 0
    imported_ids: list[str] = []
    for i in range(0, len(to_insert), BATCH_SIZE):
        batch = to_insert[i:i + BATCH_SIZE]
        result = await Bookmark.insert_many(batch)
        imported += len(batch)
        imported_ids.extend(str(oid) for oid in result.inserted_ids)

    logger.info(
        "Imported %d bookmarks (skipped %d duplicates, %d invalid) for project %s",
        imported, skipped_duplicate, len(errors), project_id,
    )

    return {
        "imported": imported,
        "skipped_duplicate": skipped_duplicate,
        "skipped_invalid": len(errors),
        "errors": errors[:MAX_ERRORS],
        "total_pending": imported,
        "imported_ids": imported_ids,
    }
