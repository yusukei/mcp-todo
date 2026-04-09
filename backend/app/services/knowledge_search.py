"""Tantivy 全文検索サービス（ナレッジ用）

ナレッジの title / content / tags を日本語形態素解析（Lindera）で
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
# KnowledgeSearchIndex
# ──────────────────────────────────────────────

class KnowledgeSearchIndex:
    """Tantivy インデックスの作成・管理（ナレッジ用）"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.schema = self._build_schema()
        self.schema_cleared = False
        try:
            self.index = tantivy.Index(self.schema, path=str(index_path))
        except ValueError as e:
            if "schema does not match" in str(e).lower():
                logger.warning("Knowledge schema mismatch. Clearing index at %s", index_path)
                for child in index_path.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                self.index = tantivy.Index(self.schema, path=str(index_path))
                self.schema_cleared = True
                logger.info("Knowledge index cleared due to schema change, rebuild required")
            else:
                raise
        self._register_tokenizers()

    def _build_schema(self) -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("knowledge_id", tokenizer_name="raw", stored=True)
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
# KnowledgeSearchIndexer
# ──────────────────────────────────────────────

class KnowledgeSearchIndexer(BatchCommitMixin):
    """ナレッジ全文検索インデックスの書き込みを管理"""

    _instance: "KnowledgeSearchIndexer | None" = None

    def __init__(self, search_index: KnowledgeSearchIndex):
        self._search_index = search_index
        self._writer = search_index.index.writer(heap_size=50_000_000)
        self._lock = threading.Lock()
        self._init_batch_state()

    @classmethod
    def get_instance(cls) -> "KnowledgeSearchIndexer | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "KnowledgeSearchIndexer") -> None:
        cls._instance = instance

    def _write_and_commit(self, knowledge_id: str, doc: tantivy.Document) -> None:
        with self._lock:
            self._writer.delete_documents("knowledge_id", knowledge_id)
            self._writer.add_document(doc)
            self._maybe_commit_locked()

    def _delete_and_commit(self, knowledge_id: str) -> None:
        with self._lock:
            self._writer.delete_documents("knowledge_id", knowledge_id)
            self._maybe_commit_locked()

    @staticmethod
    def _build_document(k: object) -> tantivy.Document:
        tags_text = " ".join(getattr(k, "tags", None) or [])
        return tantivy.Document(
            knowledge_id=str(getattr(k, "id", "")),
            title=getattr(k, "title", "") or "",
            content=getattr(k, "content", "") or "",
            tags=tags_text,
            category=str(getattr(k, "category", "")),
        )

    async def upsert_knowledge(self, k: object) -> None:
        knowledge_id = str(getattr(k, "id", ""))
        doc = self._build_document(k)
        await asyncio.to_thread(self._write_and_commit, knowledge_id, doc)

    async def delete_knowledge(self, knowledge_id: str) -> None:
        await asyncio.to_thread(self._delete_and_commit, knowledge_id)

    async def rebuild(self) -> int:
        from ..models.knowledge import Knowledge

        logger.info("Rebuilding knowledge search index...")

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

        async for k in Knowledge.find(Knowledge.is_deleted == False):  # noqa: E712
            batch.append(self._build_document(k))
            if len(batch) >= BATCH_SIZE:
                await asyncio.to_thread(_write_batch, batch)
                total += len(batch)
                batch = []

        if batch:
            await asyncio.to_thread(_write_batch, batch)
            total += len(batch)

        self._search_index.index.reload()
        logger.info("Knowledge search index rebuilt: %d documents", total)
        return total


# ──────────────────────────────────────────────
# KnowledgeSearchService
# ──────────────────────────────────────────────

_ALLOWED_FIELDS = frozenset({"title", "content", "tags", "category"})
_FIELD_PATTERN = re.compile(r"\b(\w+):")
_LEADING_WILDCARD = re.compile(r"(?:^|\s)\*")


def _sanitize_query(query_text: str) -> str:
    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(1) in _ALLOWED_FIELDS else ""
    sanitized = _FIELD_PATTERN.sub(_replace, query_text)
    sanitized = _LEADING_WILDCARD.sub(" ", sanitized)
    return sanitized.strip()


@dataclass
class KnowledgeSearchResult:
    results: list[dict] = field(default_factory=list)
    total: int = 0


async def index_knowledge(k: object) -> None:
    """ナレッジを検索インデックスに追加・更新（利用可能な場合のみ）

    In the multi-worker sidecar topology (``ENABLE_INDEXERS=False``)
    this publishes an ``index:tasks`` notification instead so the
    dedicated indexer container picks it up.
    """
    indexer = KnowledgeSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_knowledge(k)
        except Exception as e:
            logger.warning("Failed to index knowledge %s: %s", getattr(k, "id", "?"), e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_knowledge_upserted
        kid = str(getattr(k, "id", "") or "")
        if kid:
            await notify_knowledge_upserted(kid)


async def deindex_knowledge(knowledge_id: str) -> None:
    """ナレッジを検索インデックスから削除（利用可能な場合のみ）"""
    indexer = KnowledgeSearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.delete_knowledge(knowledge_id)
        except Exception as e:
            logger.warning("Failed to deindex knowledge %s: %s", knowledge_id, e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_knowledge_deleted
        if knowledge_id:
            await notify_knowledge_deleted(knowledge_id)


class KnowledgeSearchService:
    """ナレッジ全文検索クエリの実行"""

    _instance: "KnowledgeSearchService | None" = None

    def __init__(self, search_index: KnowledgeSearchIndex):
        self._search_index = search_index

    @classmethod
    def get_instance(cls) -> "KnowledgeSearchService | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "KnowledgeSearchService") -> None:
        cls._instance = instance

    def search(
        self,
        query_text: str,
        category: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> KnowledgeSearchResult:
        sanitized = _sanitize_query(query_text)
        if not sanitized:
            return KnowledgeSearchResult()

        filter_parts: list[str] = []
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
            knowledge_id = doc.get_first("knowledge_id")
            results.append({"knowledge_id": knowledge_id, "score": score})

        return KnowledgeSearchResult(results=results, total=hits.count)  # type: ignore[attr-defined]
