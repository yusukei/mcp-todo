"""Tantivy 全文検索サービス（プロジェクトドキュメント用）

プロジェクトドキュメントの title / content / tags を日本語形態素解析（Lindera）で
インデックスし、高精度な全文検索を提供する。
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
# DocumentSearchIndex
# ──────────────────────────────────────────────

class DocumentSearchIndex:
    """Tantivy インデックスの作成・管理（プロジェクトドキュメント用）"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.schema = self._build_schema()
        self.schema_cleared = False
        try:
            self.index = tantivy.Index(self.schema, path=str(index_path))
        except ValueError as e:
            if "schema does not match" in str(e).lower():
                logger.warning("Document schema mismatch. Clearing index at %s", index_path)
                for child in index_path.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                self.index = tantivy.Index(self.schema, path=str(index_path))
                self.schema_cleared = True
                logger.info("Document index cleared due to schema change, rebuild required")
            else:
                raise
        self._register_tokenizers()

    def _build_schema(self) -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("document_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("project_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("title", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("content", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("tags", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("category", tokenizer_name="raw", stored=False)
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
# DocumentSearchIndexer
# ──────────────────────────────────────────────

class DocumentSearchIndexer(BatchCommitMixin):
    """プロジェクトドキュメント全文検索インデックスの書き込みを管理"""

    _instance: "DocumentSearchIndexer | None" = None

    def __init__(self, search_index: DocumentSearchIndex):
        self._search_index = search_index
        self._writer = search_index.index.writer(heap_size=50_000_000)
        self._lock = threading.Lock()
        self._init_batch_state()

    @classmethod
    def get_instance(cls) -> "DocumentSearchIndexer | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "DocumentSearchIndexer") -> None:
        cls._instance = instance

    def _write_and_commit(self, document_id: str, doc: tantivy.Document) -> None:
        with self._lock:
            self._writer.delete_documents("document_id", document_id)
            self._writer.add_document(doc)
            self._maybe_commit_locked()

    def _delete_and_commit(self, document_id: str) -> None:
        with self._lock:
            self._writer.delete_documents("document_id", document_id)
            self._maybe_commit_locked()

    @staticmethod
    def _build_document(d: object) -> tantivy.Document:
        tags_text = " ".join(getattr(d, "tags", None) or [])
        return tantivy.Document(
            document_id=str(getattr(d, "id", "")),
            project_id=getattr(d, "project_id", "") or "",
            title=getattr(d, "title", "") or "",
            content=getattr(d, "content", "") or "",
            tags=tags_text,
            category=str(getattr(d, "category", "")),
        )

    async def upsert_document(self, d: object) -> None:
        document_id = str(getattr(d, "id", ""))
        doc = self._build_document(d)
        await asyncio.to_thread(self._write_and_commit, document_id, doc)

    async def delete_document(self, document_id: str) -> None:
        await asyncio.to_thread(self._delete_and_commit, document_id)

    async def rebuild(self) -> int:
        from ..models.document import ProjectDocument

        logger.info("Rebuilding document search index...")

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

        async for d in ProjectDocument.find(ProjectDocument.is_deleted == False):  # noqa: E712
            batch.append(self._build_document(d))
            if len(batch) >= BATCH_SIZE:
                await asyncio.to_thread(_write_batch, batch)
                total += len(batch)
                batch = []

        if batch:
            await asyncio.to_thread(_write_batch, batch)
            total += len(batch)

        self._search_index.index.reload()
        logger.info("Document search index rebuilt: %d documents", total)
        return total


# ──────────────────────────────────────────────
# DocumentSearchService
# ──────────────────────────────────────────────

_ALLOWED_FIELDS = frozenset({"title", "content", "tags", "category", "project_id"})
_FIELD_PATTERN = re.compile(r"\b(\w+):")
_LEADING_WILDCARD = re.compile(r"(?:^|\s)\*")


def _sanitize_query(query_text: str) -> str:
    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(1) in _ALLOWED_FIELDS else ""
    sanitized = _FIELD_PATTERN.sub(_replace, query_text)
    sanitized = _LEADING_WILDCARD.sub(" ", sanitized)
    return sanitized.strip()


@dataclass
class DocumentSearchResult:
    results: list[dict] = field(default_factory=list)
    total: int = 0


async def index_document(d: object) -> None:
    """ドキュメントを検索インデックスに追加・更新（利用可能な場合のみ）

    In the multi-worker sidecar topology (``ENABLE_INDEXERS=False``)
    this publishes an ``index:tasks`` notification instead so the
    dedicated indexer container picks it up.
    """
    indexer = DocumentSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_document(d)
        except Exception as e:
            logger.warning("Failed to index document %s: %s", getattr(d, "id", "?"), e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_document_upserted
        did = str(getattr(d, "id", "") or "")
        if did:
            await notify_document_upserted(did)


async def deindex_document(document_id: str) -> None:
    """ドキュメントを検索インデックスから削除（利用可能な場合のみ）"""
    indexer = DocumentSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.delete_document(document_id)
        except Exception as e:
            logger.warning("Failed to deindex document %s: %s", document_id, e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_document_deleted
        if document_id:
            await notify_document_deleted(document_id)


class DocumentSearchService:
    """プロジェクトドキュメント全文検索クエリの実行"""

    _instance: "DocumentSearchService | None" = None

    def __init__(self, search_index: DocumentSearchIndex):
        self._search_index = search_index

    @classmethod
    def get_instance(cls) -> "DocumentSearchService | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "DocumentSearchService") -> None:
        cls._instance = instance

    def search(
        self,
        query_text: str,
        project_id: str | None = None,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DocumentSearchResult:
        sanitized = _sanitize_query(query_text)
        if not sanitized:
            return DocumentSearchResult()

        filter_parts: list[str] = []
        if project_id:
            filter_parts.append(f'project_id:"{project_id}"')
        if category:
            filter_parts.append(f'category:"{category}"')

        if filter_parts:
            combined = f"({sanitized}) AND {' AND '.join(filter_parts)}"
        else:
            combined = sanitized

        self._search_index.index.reload()
        searcher = self._search_index.index.searcher()
        query = self._search_index.index.parse_query(
            combined,
            ["title", "content", "tags"],
            conjunction_by_default=True,
        )

        hits = searcher.search(query, limit=limit, offset=offset)

        results: list[dict] = []
        for score, doc_address in hits.hits:
            doc = searcher.doc(doc_address)
            document_id = doc.get_first("document_id")
            results.append({"document_id": document_id, "score": score})

        return DocumentSearchResult(results=results, total=hits.count)  # type: ignore[attr-defined]
