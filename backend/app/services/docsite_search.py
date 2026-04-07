"""Tantivy full-text search for DocSite pages.

Indexes DocPage title and content with Japanese morphological analysis (Lindera).
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tantivy  # type: ignore[import-not-found]
    TANTIVY_AVAILABLE = True
except ImportError:
    tantivy = None  # type: ignore[assignment]
    TANTIVY_AVAILABLE = False

logger = logging.getLogger(__name__)


class DocSiteSearchIndex:
    """Tantivy index for DocPage documents."""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.schema = self._build_schema()
        self.schema_cleared = False
        try:
            self.index = tantivy.Index(self.schema, path=str(index_path))
        except ValueError as e:
            if "schema does not match" in str(e).lower():
                logger.warning("DocSite schema mismatch. Clearing index at %s", index_path)
                for child in index_path.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                self.index = tantivy.Index(self.schema, path=str(index_path))
                self.schema_cleared = True
            else:
                raise
        self._register_tokenizers()

    def _build_schema(self) -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        builder.add_text_field("page_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("site_id", tokenizer_name="raw", stored=True)
        builder.add_text_field("title", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("content", tokenizer_name="lang_ja", stored=False)
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

class DocSiteSearchIndexer:
    """Manages writing to the DocSite search index."""

    _instance: DocSiteSearchIndexer | None = None

    def __init__(self, search_index: DocSiteSearchIndex):
        self._search_index = search_index
        self._writer = search_index.index.writer(heap_size=50_000_000)
        self._lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> DocSiteSearchIndexer | None:
        return cls._instance

    @classmethod
    def set_instance(cls, instance: DocSiteSearchIndexer) -> None:
        cls._instance = instance

    def _write_and_commit(self, page_id: str, doc: tantivy.Document) -> None:
        with self._lock:
            self._writer.delete_documents("page_id", page_id)
            self._writer.add_document(doc)
            self._writer.commit()

    def _delete_and_commit(self, page_id: str) -> None:
        with self._lock:
            self._writer.delete_documents("page_id", page_id)
            self._writer.commit()

    @staticmethod
    def _build_document(p: object) -> tantivy.Document:
        return tantivy.Document(
            page_id=str(getattr(p, "id", "")),
            site_id=getattr(p, "site_id", "") or "",
            title=getattr(p, "title", "") or "",
            content=getattr(p, "content", "") or "",
        )

    async def upsert_page(self, p: object) -> None:
        page_id = str(getattr(p, "id", ""))
        doc = self._build_document(p)
        await asyncio.to_thread(self._write_and_commit, page_id, doc)

    async def delete_page(self, page_id: str) -> None:
        await asyncio.to_thread(self._delete_and_commit, page_id)

    async def delete_site(self, site_id: str) -> None:
        """Delete all pages for a site from the index."""
        def _delete() -> None:
            with self._lock:
                self._writer.delete_documents("site_id", site_id)
                self._writer.commit()
        await asyncio.to_thread(_delete)

    async def rebuild(self) -> int:
        from ..models.docsite import DocPage

        logger.info("Rebuilding docsite search index...")

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

        async for p in DocPage.find_all():
            batch.append(self._build_document(p))
            if len(batch) >= BATCH_SIZE:
                await asyncio.to_thread(_write_batch, batch)
                total += len(batch)
                batch = []

        if batch:
            await asyncio.to_thread(_write_batch, batch)
            total += len(batch)

        self._search_index.index.reload()
        logger.info("DocSite search index rebuilt: %d pages", total)
        return total


_ALLOWED_FIELDS = frozenset({"title", "content", "site_id"})
_FIELD_PATTERN = re.compile(r"\b(\w+):")
_LEADING_WILDCARD = re.compile(r"(?:^|\s)\*")


def _sanitize_query(query_text: str) -> str:
    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(1) in _ALLOWED_FIELDS else ""
    sanitized = _FIELD_PATTERN.sub(_replace, query_text)
    sanitized = _LEADING_WILDCARD.sub(" ", sanitized)
    return sanitized.strip()


@dataclass
class DocSiteSearchResult:
    results: list[dict] = field(default_factory=list)
    total: int = 0


class DocSiteSearchService:
    """Executes search queries on the DocSite index."""

    _instance: DocSiteSearchService | None = None

    def __init__(self, search_index: DocSiteSearchIndex):
        self._search_index = search_index

    @classmethod
    def get_instance(cls) -> DocSiteSearchService | None:
        return cls._instance

    @classmethod
    def set_instance(cls, instance: DocSiteSearchService) -> None:
        cls._instance = instance

    def search(
        self,
        query_text: str,
        site_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DocSiteSearchResult:
        sanitized = _sanitize_query(query_text)
        if not sanitized:
            return DocSiteSearchResult()

        if site_id:
            combined = f'({sanitized}) AND site_id:"{site_id}"'
        else:
            combined = sanitized

        self._search_index.index.reload()
        searcher = self._search_index.index.searcher()
        query = self._search_index.index.parse_query(
            combined,
            ["title", "content"],
            conjunction_by_default=True,
        )

        hits = searcher.search(query, limit=limit, offset=offset)

        results: list[dict] = []
        for score, doc_address in hits.hits:
            doc = searcher.doc(doc_address)
            page_id = doc.get_first("page_id")
            results.append({"page_id": page_id, "score": score})

        return DocSiteSearchResult(results=results, total=hits.count)  # type: ignore[attr-defined]
