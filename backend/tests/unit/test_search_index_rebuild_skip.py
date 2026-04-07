"""Unit tests for the differential search-index rebuild logic.

These tests cover:
- ``SearchIndex.doc_count()`` / ``is_empty()`` helpers
- ``_should_rebuild()`` decision helper in ``app.main``

Both are exercised with stub objects to avoid needing a real Tantivy install
in CI.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ── doc_count / is_empty helpers ─────────────────────────────


def _make_stub_index(num_docs: int | None, *, raise_exc: Exception | None = None):
    """Build a stub object that mimics ``SearchIndex`` for the helper methods."""

    class StubTantivyIndex:
        def reload(self):
            if raise_exc is not None:
                raise raise_exc

        def searcher(self):
            return SimpleNamespace(num_docs=num_docs)

    # Subclass the real SearchIndex so we can call its helper methods without
    # constructing the actual Tantivy index. We bypass __init__ entirely.
    from app.services.search import SearchIndex

    obj = SearchIndex.__new__(SearchIndex)
    obj.index = StubTantivyIndex()
    obj.schema_cleared = False
    return obj


class TestDocCount:
    def test_returns_num_docs(self):
        idx = _make_stub_index(42)
        assert idx.doc_count() == 42

    def test_zero_when_empty(self):
        idx = _make_stub_index(0)
        assert idx.doc_count() == 0
        assert idx.is_empty() is True

    def test_returns_minus_one_on_exception(self):
        idx = _make_stub_index(None, raise_exc=RuntimeError("tantivy down"))
        assert idx.doc_count() == -1
        # Unknown -> treat as empty (force rebuild)
        assert idx.is_empty() is True

    def test_is_empty_false_for_populated(self):
        idx = _make_stub_index(1)
        assert idx.is_empty() is False


# ── _should_rebuild ──────────────────────────────────────────


@pytest.fixture
def should_rebuild():
    from app.main import _should_rebuild

    return _should_rebuild


class TestShouldRebuild:
    def test_force_reindex_env_triggers(self, should_rebuild, monkeypatch):
        monkeypatch.setenv("FORCE_REINDEX", "1")
        idx = SimpleNamespace(schema_cleared=False, is_empty=lambda: False, doc_count=lambda: 100)
        assert should_rebuild(idx) is True

    @pytest.mark.parametrize("val", ["true", "TRUE", "yes", "1"])
    def test_force_reindex_truthy_values(self, should_rebuild, monkeypatch, val):
        monkeypatch.setenv("FORCE_REINDEX", val)
        idx = SimpleNamespace(schema_cleared=False, is_empty=lambda: False, doc_count=lambda: 5)
        assert should_rebuild(idx) is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no"])
    def test_force_reindex_falsy_values(self, should_rebuild, monkeypatch, val):
        monkeypatch.setenv("FORCE_REINDEX", val)
        idx = SimpleNamespace(schema_cleared=False, is_empty=lambda: False, doc_count=lambda: 5)
        assert should_rebuild(idx) is False

    def test_schema_cleared_triggers(self, should_rebuild, monkeypatch):
        monkeypatch.delenv("FORCE_REINDEX", raising=False)
        idx = SimpleNamespace(schema_cleared=True, is_empty=lambda: False, doc_count=lambda: 10)
        assert should_rebuild(idx) is True

    def test_empty_index_triggers(self, should_rebuild, monkeypatch):
        monkeypatch.delenv("FORCE_REINDEX", raising=False)
        idx = SimpleNamespace(schema_cleared=False, is_empty=lambda: True, doc_count=lambda: 0)
        assert should_rebuild(idx) is True

    def test_populated_skips(self, should_rebuild, monkeypatch):
        monkeypatch.delenv("FORCE_REINDEX", raising=False)
        idx = SimpleNamespace(schema_cleared=False, is_empty=lambda: False, doc_count=lambda: 100)
        assert should_rebuild(idx) is False


# ── All five SearchIndex classes expose the helpers ──────────


def test_all_search_indexes_have_helpers():
    from app.services.search import SearchIndex
    from app.services.knowledge_search import KnowledgeSearchIndex
    from app.services.document_search import DocumentSearchIndex
    from app.services.docsite_search import DocSiteSearchIndex
    from app.services.bookmark_search import BookmarkSearchIndex

    for cls in (
        SearchIndex,
        KnowledgeSearchIndex,
        DocumentSearchIndex,
        DocSiteSearchIndex,
        BookmarkSearchIndex,
    ):
        assert hasattr(cls, "doc_count"), f"{cls.__name__} missing doc_count"
        assert hasattr(cls, "is_empty"), f"{cls.__name__} missing is_empty"
