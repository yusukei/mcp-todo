"""Tantivy 全文検索サービス

タスクの title / description / tags / comments を日本語形態素解析（Lindera）で
インデックスし、高精度な全文検索を提供する。

- SearchIndex: スキーマ定義・インデックス管理
- SearchIndexer: 書き込み（upsert / delete / rebuild）
- SearchService: 検索クエリ実行
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
from typing import TYPE_CHECKING, Any

try:
    import tantivy  # type: ignore[import-not-found]
    TANTIVY_AVAILABLE = True
except ImportError:
    tantivy = None  # type: ignore[assignment]
    TANTIVY_AVAILABLE = False

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# SearchIndex
# ──────────────────────────────────────────────

class SearchIndex:
    """Tantivy インデックスの作成・管理"""

    def __init__(self, index_path: Path):
        self.index_path = index_path
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.schema = self._build_schema()
        self.schema_cleared = False
        try:
            self.index = tantivy.Index(self.schema, path=str(index_path))
        except ValueError as e:
            if "schema does not match" in str(e).lower():
                logger.warning("Schema mismatch detected. Clearing index at %s", index_path)
                for child in index_path.iterdir():
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        shutil.rmtree(child)
                self.index = tantivy.Index(self.schema, path=str(index_path))
                self.schema_cleared = True
                logger.info("Index cleared due to schema change, rebuild required")
            else:
                raise
        self._register_tokenizers()

    def _build_schema(self) -> tantivy.Schema:
        builder = tantivy.SchemaBuilder()
        # 識別用（stored=True で検索結果から task_id を取得）
        builder.add_text_field("task_id", tokenizer_name="raw", stored=True)
        # 検索対象（stored=False — 表示データは MongoDB から取得）
        builder.add_text_field("title", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("description", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("tags", tokenizer_name="lang_ja", stored=False)
        builder.add_text_field("comments", tokenizer_name="lang_ja", stored=False)
        # フィルタ用
        builder.add_text_field("project_id", tokenizer_name="raw", stored=False)
        builder.add_text_field("status", tokenizer_name="raw", stored=False)
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
# SearchIndexer
# ──────────────────────────────────────────────

class SearchIndexer(BatchCommitMixin):
    """全文検索インデックスの書き込みを管理"""

    _instance: "SearchIndexer | None" = None

    def __init__(self, search_index: SearchIndex):
        self._search_index = search_index
        self._writer = search_index.index.writer(heap_size=50_000_000)
        self._lock = threading.Lock()
        self._init_batch_state()

    @classmethod
    def get_instance(cls) -> "SearchIndexer | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "SearchIndexer") -> None:
        cls._instance = instance

    def _write_and_commit(self, task_id: str, doc: tantivy.Document) -> None:
        """ロック内で upsert + commit"""
        with self._lock:
            self._writer.delete_documents("task_id", task_id)
            self._writer.add_document(doc)
            self._maybe_commit_locked()

    def _delete_and_commit(self, task_id: str) -> None:
        """ロック内で delete + commit"""
        with self._lock:
            self._writer.delete_documents("task_id", task_id)
            self._maybe_commit_locked()

    @staticmethod
    def _build_document(task: object) -> tantivy.Document:
        """Task オブジェクトからインデックスドキュメントを構築"""
        comments_text = " ".join(
            getattr(c, "content", "") for c in (getattr(task, "comments", None) or [])
        )
        tags_text = " ".join(getattr(task, "tags", None) or [])
        return tantivy.Document(
            task_id=str(getattr(task, "id", "")),
            title=getattr(task, "title", "") or "",
            description=getattr(task, "description", "") or "",
            tags=tags_text,
            comments=comments_text,
            project_id=getattr(task, "project_id", "") or "",
            status=str(getattr(task, "status", "")),
        )

    async def upsert_task(self, task: object) -> None:
        """タスクをインデックスに追加・更新"""
        task_id = str(getattr(task, "id", ""))
        doc = self._build_document(task)
        await asyncio.to_thread(self._write_and_commit, task_id, doc)

    async def delete_task(self, task_id: str) -> None:
        """タスクをインデックスから削除"""
        await asyncio.to_thread(self._delete_and_commit, task_id)

    async def rebuild(self) -> int:
        """MongoDB の全タスクからインデックスを再構築"""
        from ..models import Task

        logger.info("Rebuilding search index...")

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

        async for task in Task.find(Task.is_deleted == False):  # noqa: E712
            batch.append(self._build_document(task))
            if len(batch) >= BATCH_SIZE:
                await asyncio.to_thread(_write_batch, batch)
                total += len(batch)
                batch = []

        if batch:
            await asyncio.to_thread(_write_batch, batch)
            total += len(batch)

        self._search_index.index.reload()
        logger.info("Search index rebuilt: %d documents", total)
        return total


# ──────────────────────────────────────────────
# SearchService
# ──────────────────────────────────────────────

# ユーザーが使用可能なフィールド指定のホワイトリスト
_ALLOWED_FIELDS = frozenset({"title", "description", "tags", "comments", "status"})
_FIELD_PATTERN = re.compile(r"\b(\w+):")
_LEADING_WILDCARD = re.compile(r"(?:^|\s)\*")


def _sanitize_query(query_text: str) -> str:
    """ユーザ入力のクエリをサニタイズ"""
    # 許可されていないフィールド指定を除去
    def _replace(m: re.Match) -> str:
        return m.group(0) if m.group(1) in _ALLOWED_FIELDS else ""
    sanitized = _FIELD_PATTERN.sub(_replace, query_text)
    # 先頭ワイルドカードを除去
    sanitized = _LEADING_WILDCARD.sub(" ", sanitized)
    return sanitized.strip()


@dataclass
class SearchResult:
    """検索結果"""
    results: list[dict] = field(default_factory=list)
    total: int = 0


async def index_task(task: object) -> None:
    """タスクを検索インデックスに追加・更新（利用可能な場合のみ）

    In the single-process deployment this calls the in-process
    Tantivy writer directly. In the multi-worker sidecar topology
    (``ENABLE_INDEXERS=False``) the writer lives in a separate
    container, so we publish an ``index:tasks`` notification
    instead — see ``services.index_notifications``.
    """
    indexer = SearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.upsert_task(task)
        except Exception as e:
            logger.warning("Failed to index task %s: %s", getattr(task, "id", "?"), e)
        return
    # No in-process indexer. If the deployment has an out-of-
    # process indexer (the sidecar container), publish a hint so
    # the sidecar picks it up and re-reads from Mongo.
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_task_upserted
        task_id = str(getattr(task, "id", "") or "")
        if task_id:
            await notify_task_upserted(task_id)


async def deindex_task(task_id: str) -> None:
    """タスクを検索インデックスから削除（利用可能な場合のみ）"""
    indexer = SearchIndexer.get_instance()
    if indexer:
        try:
            await indexer.delete_task(task_id)
        except Exception as e:
            logger.warning("Failed to deindex task %s: %s", task_id, e)
        return
    from ..core.config import settings as _settings
    if not _settings.ENABLE_INDEXERS:
        from .index_notifications import notify_task_deleted
        if task_id:
            await notify_task_deleted(task_id)


class SearchService:
    """全文検索クエリの実行"""

    _instance: "SearchService | None" = None

    def __init__(self, search_index: SearchIndex):
        self._search_index = search_index

    @classmethod
    def get_instance(cls) -> "SearchService | None":
        return cls._instance

    @classmethod
    def set_instance(cls, instance: "SearchService") -> None:
        cls._instance = instance

    def search(
        self,
        query_text: str,
        project_ids: list[str] | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> SearchResult:
        """全文検索を実行

        Args:
            query_text: ユーザ入力の検索テキスト
            project_ids: フィルタ対象のプロジェクトID（None=全プロジェクト）
            status: ステータスフィルタ
            limit: 最大取得件数
            offset: オフセット

        Returns:
            SearchResult: task_id とスコアのリスト
        """
        sanitized = _sanitize_query(query_text)
        if not sanitized:
            return SearchResult()

        # プロジェクトフィルタ
        filter_parts: list[str] = []
        if project_ids:
            clause = " OR ".join(f'project_id:"{pid}"' for pid in project_ids)
            filter_parts.append(f"({clause})")
        if status:
            filter_parts.append(f'status:"{status}"')

        if filter_parts:
            combined = f"({sanitized}) AND {' AND '.join(filter_parts)}"
        else:
            combined = sanitized

        self._search_index.index.reload()
        searcher = self._search_index.index.searcher()
        query = self._search_index.index.parse_query(
            combined,
            ["title", "description", "tags", "comments"],
            conjunction_by_default=True,
        )

        hits = searcher.search(query, limit=limit, offset=offset)

        results: list[dict] = []
        for score, doc_address in hits.hits:
            doc = searcher.doc(doc_address)
            task_id = doc.get_first("task_id")
            results.append({"task_id": task_id, "score": score})

        return SearchResult(results=results, total=hits.count)  # type: ignore[attr-defined]
