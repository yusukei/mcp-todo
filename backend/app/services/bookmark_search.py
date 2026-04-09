"""Tantivy 全文検索サービス（ブックマーク用）

ブックマークの title / description / url / tags / clip_content を
日本語形態素解析（Lindera）でインデックスし、高精度な全文検索を提供する。
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading

from ._search_batch import BatchCommitMixin
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tantivy  # type: ignore[import-not-found]
    TANTIVY_AVAILABLE = True
except ImportError:
    tantivy = None  # type: ignore[assignment]
    TANTIVY_AVAILABLE = False

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# BookmarkSearchIndex
# ──────────────────────────────────────────────

class BookmarkSearchIndex:
    """Tantivy インデックスの作成・管理（ブックマーク用）"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.schema = self._build_schema()
        self.schema_cleared = False
        try:
            self.index = tantivy.Index(self.schema, path=str(index_path))
        except ValueError as e:
            if "schema does not match" in str(e).lower():
                logger.warning("Bookmark schema mismatch. Clearing index at %s", index_path)
                for child in index_path.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                self.index = tantivy.Index(self.schema, path=str(index_path))
                self.schema_cleared = True
                logger.info("Bookmark index cleared due to schema change, rebuild required")
            else:
                raise
        self._register_tokenizers()

    def _build_schema(self) -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("bookmark_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("project_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("title", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("description", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("url", tokenizer_name="raw", stored=False)
        builder.add_text_field("tags", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("clip_content", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("collection_id", tokenizer_name="raw", stored=False)
        return builder.build()

    def _register_tokenizers(self) -> None:
        analyzer = tantivy.TextAnalyzerBuilder(
            tantivy.Tokenizer.lindera()  # type: ignore[attr-defined]
        ).filter(
            tantivy.Filter.lowercase()
        ).build()
        self.index.register_tokenizer("lang_ja", analyzer)

    def doc_count(self) -> int:
        """Return the number of documents currently in the index.

        Returns -1 if the count cannot be determined (e.g. tantivy API
        change). Callers should treat -1 as "unknown" and force a rebuild.
        """
        try:
            self.index.reload()
            return int(self.index.searcher().num_docs)
        except Exception:
            return -1

    def is_empty(self) -> bool:
        """True when the index has zero documents (or count is unknown)."""
        return self.doc_count() <= 0

# ──────────────────────────────────────────────
# BookmarkSearchIndexer
# ──────────────────────────────────────────────

class BookmarkSearchIndexer(BatchCommitMixin):
    """ブックマーク全文検索インデックスの書き込みを管理"""

    _instance: "BookmarkSearchIndexer | None" = None

    def __init__(self, search_index: BookmarkSearchIndex):
        self._search_index = search_index
        self._writer = search_index.index.writer(heap_size=50_000_000)
        self._lock = threading.Lock()
        self._init_batch_state()

    @classmethod
    def get_instance(cls) -> "BookmarkSearchIndexer | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "BookmarkSearchIndexer") -> None:
        cls._instance = instance

    def _write_and_commit(self, bookmark_id: str, doc: tantivy.Document) -> None:
        with self._lock:
            self._writer.delete_documents("bookmark_id", bookmark_id)
            self._writer.add_document(doc)
            self._maybe_commit_locked()

    def _delete_and_commit(self, bookmark_id: str) -> None:
        with self._lock:
            self._writer.delete_documents("bookmark_id", bookmark_id)
            self._maybe_commit_locked()

    @staticmethod
    def _build_document(b: object) -> tantivy.Document:
        tags_text = " ".join(getattr(b, "tags", None) or [])
        return tantivy.Document(
            bookmark_id=str(getattr(b, "id", "")),
            project_id=getattr(b, "project_id", "") or "",
            title=getattr(b, "title", "") or "",
            description=getattr(b, "description", "") or "",
            url=getattr(b, "url", "") or "",
            tags=tags_text,
            clip_content=getattr(b, "clip_markdown", "") or getattr(b, "clip_content", "") or "",
            collection_id=getattr(b, "collection_id", "") or "",
        )

    async def upsert_bookmark(self, b: object) -> None:
        bookmark_id = str(getattr(b, "id", ""))
        doc = self._build_document(b)
        await asyncio.to_thread(self._write_and_commit, bookmark_id, doc)

    async def delete_bookmark(self, bookmark_id: str) -> None:
        await asyncio.to_thread(self._delete_and_commit, bookmark_id)

    async def rebuild(self) -> int:
        from ..models.bookmark import Bookmark

        logger.info("Rebuilding bookmark search index...")

        def _delete_all() -> None:
            with self._lock:
                self._writer.delete_all_documents()
                self._writer.commit()

        def _write_batch(docs: list[tantivy.Document]) -> None:
            with self._lock:
                for doc in docs:
                    self._writer.add_document(doc)
                self._writer.commit()

        await asyncio.to_thread(_delete_all)

        BATCH_SIZE = 500
        batch: list[tantivy.Document] = []
        total = 0

        async for b in Bookmark.find(Bookmark.is_deleted == False):  # noqa: E712
            batch.append(self._build_document(b))
            if len(batch) >= BATCH_SIZE:
                await asyncio.to_thread(_write_batch, batch)
                total += len(batch)
                batch = []

        if batch:
            await asyncio.to_thread(_write_batch, batch)
            total += len(batch)

        self._search_index.index.reload()
        logger.info("Bookmark search index rebuilt: %d bookmarks", total)
        return total


# ──────────────────────────────────────────────
# BookmarkSearchService
# ──────────────────────────────────────────────

_ALLOWED_FIELDS = frozenset({"title", "description", "tags", "url", "clip_content", "project_id", "collection_id"})
_FIELD_PATTERN = re.compile(r"\b(\w+):")
_LEADING_WILDCARD = re.compile(r"(?:^|\s)\*")


def _sanitize_query(query_text: str) -> str:
    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(1) in _ALLOWED_FIELDS else ""
    sanitized = _FIELD_PATTERN.sub(_replace, query_text)
    sanitized = _LEADING_WILDCARD.sub(" ", sanitized)
    return sanitized.strip()


@dataclass
class BookmarkSearchResult:
    results: list[dict] = field(default_factory=list)
    total: int = 0


async def index_bookmark(b: object) -> None:
    """ブックマークを検索インデックスに追加・更新

    In the multi-worker sidecar topology (``ENABLE_INDEXERS=False``)
    this publishes an ``index:tasks`` notification instead so the
    dedicated indexer container picks it up.
    """
    indexer = BookmarkSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_bookmark(b)
        except Exception as e:
            logger.warning("Failed to index bookmark %s: %s", getattr(b, "id", "?"), e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_bookmark_upserted
        bid = str(getattr(b, "id", "") or "")
        if bid:
            await notify_bookmark_upserted(bid)


async def deindex_bookmark(bookmark_id: str) -> None:
    """ブックマークを検索インデックスから削除"""
    indexer = BookmarkSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.delete_bookmark(bookmark_id)
        except Exception as e:
            logger.warning("Failed to deindex bookmark %s: %s", bookmark_id, e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_bookmark_deleted
        if bookmark_id:
            await notify_bookmark_deleted(bookmark_id)


class BookmarkSearchService:
    """ブックマーク全文検索クエリの実行"""

    _instance: "BookmarkSearchService | None" = None

    def __init__(self, search_index: BookmarkSearchIndex):
        self._search_index = search_index

    @classmethod
    def get_instance(cls) -> "BookmarkSearchService | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "BookmarkSearchService") -> None:
        cls._instance = instance

    def search(
        self,
        query_text: str,
        project_id: str | None = None,
        collection_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> BookmarkSearchResult:
        sanitized = _sanitize_query(query_text)
        if not sanitized:
            return BookmarkSearchResult()

        filter_parts: list[str] = []
        if project_id:
            filter_parts.append(f'project_id:"{project_id}"')
        if collection_id:
            filter_parts.append(f'collection_id:"{collection_id}"')

        if filter_parts:
            combined = f"({sanitized}) AND {' AND '.join(filter_parts)}"
        else:
            combined = sanitized

        self._search_index.index.reload()
        searcher = self._search_index.index.searcher()
        query = self._search_index.index.parse_query(
            combined,
            ["title", "description", "tags", "clip_content"],
            conjunction_by_default=True,
        )

        hits = searcher.search(query, limit=limit, offset=offset)

        results: list[dict] = []
        for score, doc_address in hits.hits:
            doc = searcher.doc(doc_address)
            bookmark_id = doc.get_first("bookmark_id")
            results.append({"bookmark_id": bookmark_id, "score": score})

        return BookmarkSearchResult(results=results, total=hits.count)  # type: ignore[attr-defined]
