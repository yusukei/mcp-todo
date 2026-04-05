"""全文検索サービス群のテスト

tantivy がテスト環境で未インストールのため、以下をテスト:
- フォールバックパス (TANTIVY_AVAILABLE=False 時の動作)
- _sanitize_query() 等の純粋関数
- シングルトン管理 (get_instance / set_instance)
- index_task / deindex_task のフォールバック
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ──────────────────────────────────────────────
# search.py (タスク検索)
# ──────────────────────────────────────────────

class TestSanitizeQuery:
    """_sanitize_query のテスト"""

    def test_allowed_fields_preserved(self):
        from app.services.search import _sanitize_query
        assert "title:" in _sanitize_query("title:hello")
        assert "description:" in _sanitize_query("description:world")
        assert "tags:" in _sanitize_query("tags:python")
        assert "status:" in _sanitize_query("status:done")

    def test_disallowed_fields_removed(self):
        from app.services.search import _sanitize_query
        result = _sanitize_query("secret:password")
        assert "secret:" not in result

    def test_leading_wildcard_removed(self):
        from app.services.search import _sanitize_query
        result = _sanitize_query("*hello")
        assert not result.startswith("*")

    def test_empty_query_returns_empty(self):
        from app.services.search import _sanitize_query
        assert _sanitize_query("") == ""
        assert _sanitize_query("   ") == ""

    def test_normal_text_preserved(self):
        from app.services.search import _sanitize_query
        assert _sanitize_query("hello world") == "hello world"

    def test_mixed_fields_and_text(self):
        from app.services.search import _sanitize_query
        result = _sanitize_query("title:bug fix urgent")
        assert "title:" in result
        assert "fix" in result

    def test_comments_field_allowed(self):
        from app.services.search import _sanitize_query
        assert "comments:" in _sanitize_query("comments:feedback")


class TestSearchResult:
    """SearchResult データクラスのテスト"""

    def test_defaults(self):
        from app.services.search import SearchResult
        sr = SearchResult()
        assert sr.results == []
        assert sr.total == 0

    def test_with_values(self):
        from app.services.search import SearchResult
        sr = SearchResult(results=[{"task_id": "1", "score": 1.5}], total=1)
        assert len(sr.results) == 1
        assert sr.total == 1


class TestSearchServiceSingleton:
    """SearchService シングルトン管理のテスト"""

    def test_get_instance_returns_none_initially(self):
        from app.services.search import SearchService
        original = SearchService._instance
        SearchService._instance = None
        try:
            assert SearchService.get_instance() is None
        finally:
            SearchService._instance = original

    def test_set_and_get_instance(self):
        from app.services.search import SearchService
        original = SearchService._instance
        try:
            mock_service = MagicMock()
            SearchService.set_instance(mock_service)
            assert SearchService.get_instance() is mock_service
        finally:
            SearchService._instance = original


class TestSearchIndexerSingleton:
    """SearchIndexer シングルトン管理のテスト"""

    def test_get_instance_returns_none_initially(self):
        from app.services.search import SearchIndexer
        original = SearchIndexer._instance
        SearchIndexer._instance = None
        try:
            assert SearchIndexer.get_instance() is None
        finally:
            SearchIndexer._instance = original


class TestIndexTaskFallback:
    """index_task / deindex_task のフォールバックテスト"""

    async def test_index_task_noop_when_no_indexer(self):
        from app.services.search import SearchIndexer, index_task
        original = SearchIndexer._instance
        SearchIndexer._instance = None
        try:
            # Should not raise
            await index_task(MagicMock(id="123"))
        finally:
            SearchIndexer._instance = original

    async def test_deindex_task_noop_when_no_indexer(self):
        from app.services.search import SearchIndexer, deindex_task
        original = SearchIndexer._instance
        SearchIndexer._instance = None
        try:
            await deindex_task("123")
        finally:
            SearchIndexer._instance = original

    async def test_index_task_catches_exception(self):
        from app.services.search import SearchIndexer, index_task
        original = SearchIndexer._instance
        mock_indexer = MagicMock()
        mock_indexer.upsert_task = AsyncMock(side_effect=RuntimeError("boom"))
        SearchIndexer._instance = mock_indexer
        try:
            # Should not raise, just log warning
            await index_task(MagicMock(id="123"))
        finally:
            SearchIndexer._instance = original

    async def test_deindex_task_catches_exception(self):
        from app.services.search import SearchIndexer, deindex_task
        original = SearchIndexer._instance
        mock_indexer = MagicMock()
        mock_indexer.delete_task = AsyncMock(side_effect=RuntimeError("boom"))
        SearchIndexer._instance = mock_indexer
        try:
            await deindex_task("123")
        finally:
            SearchIndexer._instance = original


class TestTantivyAvailableFlag:
    """TANTIVY_AVAILABLE フラグのテスト"""

    def test_search_tantivy_flag(self):
        from app.services.search import TANTIVY_AVAILABLE
        # tantivy が未インストールの環境では False
        assert TANTIVY_AVAILABLE is False

    def test_document_search_tantivy_flag(self):
        from app.services.document_search import TANTIVY_AVAILABLE
        assert TANTIVY_AVAILABLE is False

    def test_knowledge_search_tantivy_flag(self):
        from app.services.knowledge_search import TANTIVY_AVAILABLE
        assert TANTIVY_AVAILABLE is False

    def test_bookmark_search_tantivy_flag(self):
        from app.services.bookmark_search import TANTIVY_AVAILABLE
        assert TANTIVY_AVAILABLE is False

    def test_docsite_search_tantivy_flag(self):
        from app.services.docsite_search import TANTIVY_AVAILABLE
        assert TANTIVY_AVAILABLE is False


# ──────────────────────────────────────────────
# document_search.py
# ──────────────────────────────────────────────

class TestDocumentSearchSingleton:
    """DocumentSearchService シングルトン管理"""

    def test_get_instance_returns_none(self):
        from app.services.document_search import DocumentSearchService
        original = DocumentSearchService._instance
        DocumentSearchService._instance = None
        try:
            assert DocumentSearchService.get_instance() is None
        finally:
            DocumentSearchService._instance = original


class TestDocumentSearchSanitizeQuery:
    """document_search._sanitize_query のテスト"""

    def test_allowed_fields(self):
        from app.services.document_search import _sanitize_query
        assert "title:" in _sanitize_query("title:spec")
        assert "content:" in _sanitize_query("content:api")

    def test_disallowed_fields_removed(self):
        from app.services.document_search import _sanitize_query
        result = _sanitize_query("secret:data")
        assert "secret:" not in result

    def test_leading_wildcard_removed(self):
        from app.services.document_search import _sanitize_query
        result = _sanitize_query("*search")
        assert not result.startswith("*")


class TestDocumentIndexFallback:
    """index_document / deindex_document のフォールバック"""

    async def test_index_document_noop_when_no_indexer(self):
        from app.services.document_search import DocumentSearchIndexer, index_document
        original = DocumentSearchIndexer._instance
        DocumentSearchIndexer._instance = None
        try:
            await index_document(MagicMock(id="123"))
        finally:
            DocumentSearchIndexer._instance = original

    async def test_deindex_document_noop_when_no_indexer(self):
        from app.services.document_search import DocumentSearchIndexer, deindex_document
        original = DocumentSearchIndexer._instance
        DocumentSearchIndexer._instance = None
        try:
            await deindex_document("123")
        finally:
            DocumentSearchIndexer._instance = original


# ──────────────────────────────────────────────
# knowledge_search.py
# ──────────────────────────────────────────────

class TestKnowledgeSearchSingleton:
    """KnowledgeSearchService シングルトン管理"""

    def test_get_instance_returns_none(self):
        from app.services.knowledge_search import KnowledgeSearchService
        original = KnowledgeSearchService._instance
        KnowledgeSearchService._instance = None
        try:
            assert KnowledgeSearchService.get_instance() is None
        finally:
            KnowledgeSearchService._instance = original


class TestKnowledgeSearchSanitizeQuery:
    """knowledge_search._sanitize_query のテスト"""

    def test_allowed_fields(self):
        from app.services.knowledge_search import _sanitize_query
        assert "title:" in _sanitize_query("title:recipe")

    def test_disallowed_fields_removed(self):
        from app.services.knowledge_search import _sanitize_query
        result = _sanitize_query("internal:data")
        assert "internal:" not in result


class TestKnowledgeIndexFallback:
    """index_knowledge / deindex_knowledge のフォールバック"""

    async def test_index_knowledge_noop_when_no_indexer(self):
        from app.services.knowledge_search import KnowledgeSearchIndexer, index_knowledge
        original = KnowledgeSearchIndexer._instance
        KnowledgeSearchIndexer._instance = None
        try:
            await index_knowledge(MagicMock(id="123"))
        finally:
            KnowledgeSearchIndexer._instance = original

    async def test_deindex_knowledge_noop_when_no_indexer(self):
        from app.services.knowledge_search import KnowledgeSearchIndexer, deindex_knowledge
        original = KnowledgeSearchIndexer._instance
        KnowledgeSearchIndexer._instance = None
        try:
            await deindex_knowledge("123")
        finally:
            KnowledgeSearchIndexer._instance = original


# ──────────────────────────────────────────────
# bookmark_search.py
# ──────────────────────────────────────────────

class TestBookmarkSearchSingleton:
    """BookmarkSearchService シングルトン管理"""

    def test_get_instance_returns_none(self):
        from app.services.bookmark_search import BookmarkSearchService
        original = BookmarkSearchService._instance
        BookmarkSearchService._instance = None
        try:
            assert BookmarkSearchService.get_instance() is None
        finally:
            BookmarkSearchService._instance = original


class TestBookmarkSearchSanitizeQuery:
    """bookmark_search._sanitize_query のテスト"""

    def test_allowed_fields(self):
        from app.services.bookmark_search import _sanitize_query
        assert "title:" in _sanitize_query("title:python")
        assert "url:" in _sanitize_query("url:github")

    def test_disallowed_fields_removed(self):
        from app.services.bookmark_search import _sanitize_query
        result = _sanitize_query("admin:hack")
        assert "admin:" not in result


class TestBookmarkIndexFallback:
    """index_bookmark / deindex_bookmark のフォールバック"""

    async def test_index_bookmark_noop_when_no_indexer(self):
        from app.services.bookmark_search import BookmarkSearchIndexer, index_bookmark
        original = BookmarkSearchIndexer._instance
        BookmarkSearchIndexer._instance = None
        try:
            await index_bookmark(MagicMock(id="123"))
        finally:
            BookmarkSearchIndexer._instance = original

    async def test_deindex_bookmark_noop_when_no_indexer(self):
        from app.services.bookmark_search import BookmarkSearchIndexer, deindex_bookmark
        original = BookmarkSearchIndexer._instance
        BookmarkSearchIndexer._instance = None
        try:
            await deindex_bookmark("123")
        finally:
            BookmarkSearchIndexer._instance = original


# ──────────────────────────────────────────────
# docsite_search.py
# ──────────────────────────────────────────────

class TestDocsiteSearchSingleton:
    """DocSiteSearchService シングルトン管理"""

    def test_get_instance_returns_none(self):
        from app.services.docsite_search import DocSiteSearchService
        original = DocSiteSearchService._instance
        DocSiteSearchService._instance = None
        try:
            assert DocSiteSearchService.get_instance() is None
        finally:
            DocSiteSearchService._instance = original


class TestDocsiteSearchSanitizeQuery:
    """docsite_search._sanitize_query のテスト"""

    def test_allowed_fields(self):
        from app.services.docsite_search import _sanitize_query
        assert "title:" in _sanitize_query("title:api")

    def test_disallowed_fields_removed(self):
        from app.services.docsite_search import _sanitize_query
        result = _sanitize_query("secret:internal")
        assert "secret:" not in result
