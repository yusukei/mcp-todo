"""Unit tests for knowledge MCP tools."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.knowledge import Knowledge, KnowledgeCategory


# Patch authenticate for all tests in this module.
_AUTH_PATCH = patch(
    "app.mcp.tools.knowledge.authenticate",
    new_callable=AsyncMock,
    return_value={"key_id": "test-key", "key_name": "test", "project_scopes": []},
)


@pytest.fixture(autouse=True)
def _mock_auth():
    with _AUTH_PATCH:
        yield


async def _make_knowledge(
    title: str = "Test Knowledge",
    content: str = "Some content",
    tags: list[str] | None = None,
    category: KnowledgeCategory = KnowledgeCategory.reference,
    source: str | None = None,
    is_deleted: bool = False,
) -> Knowledge:
    k = Knowledge(
        title=title,
        content=content,
        tags=tags or [],
        category=category,
        source=source,
        created_by="test",
        is_deleted=is_deleted,
    )
    await k.insert()
    return k


class TestCreateKnowledge:
    async def test_creates_entry(self):
        from app.mcp.tools.knowledge import create_knowledge

        result = await create_knowledge(
            title="FastMCP Integration",
            content="How to integrate FastMCP with FastAPI",
            tags=["fastmcp", "fastapi"],
            category="recipe",
            source="https://example.com",
        )

        assert result["title"] == "FastMCP Integration"
        assert result["content"] == "How to integrate FastMCP with FastAPI"
        assert result["tags"] == ["fastmcp", "fastapi"]
        assert result["category"] == "recipe"
        assert result["source"] == "https://example.com"
        assert "id" in result

    async def test_normalizes_tags(self):
        from app.mcp.tools.knowledge import create_knowledge

        result = await create_knowledge(
            title="Test",
            content="Content",
            tags=["  FastAPI  ", "PYTHON", ""],
        )

        assert result["tags"] == ["fastapi", "python"]

    async def test_rejects_empty_title(self):
        from app.mcp.tools.knowledge import create_knowledge
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="Title is required"):
            await create_knowledge(title="", content="test")

    async def test_rejects_invalid_category(self):
        from app.mcp.tools.knowledge import create_knowledge
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="Invalid category"):
            await create_knowledge(title="Test", content="test", category="invalid")


class TestGetKnowledge:
    async def test_returns_entry(self):
        from app.mcp.tools.knowledge import get_knowledge

        k = await _make_knowledge(title="Test Entry")
        result = await get_knowledge(knowledge_id=str(k.id))

        assert result["title"] == "Test Entry"
        assert result["id"] == str(k.id)

    async def test_not_found_for_deleted(self):
        from app.mcp.tools.knowledge import get_knowledge
        from fastmcp.exceptions import ToolError

        k = await _make_knowledge(is_deleted=True)
        with pytest.raises(ToolError, match="not found"):
            await get_knowledge(knowledge_id=str(k.id))


class TestUpdateKnowledge:
    async def test_updates_fields(self):
        from app.mcp.tools.knowledge import update_knowledge

        k = await _make_knowledge(title="Old Title", tags=["old"])
        result = await update_knowledge(
            knowledge_id=str(k.id),
            title="New Title",
            tags=["new", "updated"],
            category="tip",
        )

        assert result["title"] == "New Title"
        assert result["tags"] == ["new", "updated"]
        assert result["category"] == "tip"

    async def test_partial_update(self):
        from app.mcp.tools.knowledge import update_knowledge

        k = await _make_knowledge(title="Keep This", content="Original")
        result = await update_knowledge(
            knowledge_id=str(k.id),
            content="Updated content",
        )

        assert result["title"] == "Keep This"
        assert result["content"] == "Updated content"


class TestDeleteKnowledge:
    async def test_soft_deletes(self):
        from app.mcp.tools.knowledge import delete_knowledge, get_knowledge
        from fastmcp.exceptions import ToolError

        k = await _make_knowledge()
        result = await delete_knowledge(knowledge_id=str(k.id))

        assert result["success"] is True

        with pytest.raises(ToolError, match="not found"):
            await get_knowledge(knowledge_id=str(k.id))


class TestListKnowledge:
    async def test_lists_all(self):
        from app.mcp.tools.knowledge import list_knowledge

        await _make_knowledge(title="Entry 1")
        await _make_knowledge(title="Entry 2")

        result = await list_knowledge()

        assert result["total"] == 2
        assert len(result["items"]) == 2

    async def test_filters_by_category(self):
        from app.mcp.tools.knowledge import list_knowledge

        await _make_knowledge(title="Recipe", category=KnowledgeCategory.recipe)
        await _make_knowledge(title="Tip", category=KnowledgeCategory.tip)

        result = await list_knowledge(category="recipe")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Recipe"

    async def test_filters_by_tag(self):
        from app.mcp.tools.knowledge import list_knowledge

        await _make_knowledge(title="Tagged", tags=["python"])
        await _make_knowledge(title="Other", tags=["rust"])

        result = await list_knowledge(tag="python")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Tagged"

    async def test_excludes_deleted(self):
        from app.mcp.tools.knowledge import list_knowledge

        await _make_knowledge(title="Active")
        await _make_knowledge(title="Deleted", is_deleted=True)

        result = await list_knowledge()

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active"

    async def test_pagination(self):
        from app.mcp.tools.knowledge import list_knowledge

        for i in range(5):
            await _make_knowledge(title=f"Entry {i}")

        result = await list_knowledge(limit=2, skip=2)

        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["limit"] == 2
        assert result["skip"] == 2


class TestSearchKnowledge:
    async def test_regex_fallback(self):
        """Without Tantivy, search falls back to MongoDB $regex."""
        from app.mcp.tools.knowledge import search_knowledge

        await _make_knowledge(title="FastAPI Integration Guide", content="How to use FastAPI")
        await _make_knowledge(title="Unrelated", content="Something else")

        result = await search_knowledge(query="FastAPI")

        assert result["total"] >= 1
        titles = [item["title"] for item in result["items"]]
        assert "FastAPI Integration Guide" in titles
        assert result["_meta"]["search_engine"] == "regex"

    async def test_search_with_category_filter(self):
        from app.mcp.tools.knowledge import search_knowledge

        await _make_knowledge(title="FastAPI Recipe", category=KnowledgeCategory.recipe)
        await _make_knowledge(title="FastAPI Tip", category=KnowledgeCategory.tip)

        result = await search_knowledge(query="FastAPI", category="recipe")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "FastAPI Recipe"

    async def test_search_with_tag_filter(self):
        from app.mcp.tools.knowledge import search_knowledge

        await _make_knowledge(title="Python FastAPI", tags=["python", "fastapi"])
        await _make_knowledge(title="Rust Tantivy", tags=["rust", "tantivy"])

        result = await search_knowledge(query="FastAPI", tag="python")

        # Should find the one with matching query AND tag
        titles = [item["title"] for item in result["items"]]
        assert "Python FastAPI" in titles

    async def test_rejects_empty_query(self):
        from app.mcp.tools.knowledge import search_knowledge
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="Query is required"):
            await search_knowledge(query="")
