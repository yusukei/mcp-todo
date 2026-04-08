"""Unit tests for project document MCP tools."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.document import DocumentCategory, DocumentVersion, ProjectDocument
from app.models.project import Project, ProjectMember, ProjectStatus


# Patch authenticate for all tests in this module.
_AUTH_PATCH = patch(
    "app.mcp.tools.documents.authenticate",
    new_callable=AsyncMock,
    return_value={"key_id": "test-key", "key_name": "test", "user_id": "test-user", "is_admin": True, "auth_kind": "api_key"},
)


@pytest.fixture(autouse=True)
def _mock_auth():
    with _AUTH_PATCH:
        yield


async def _make_project(name: str = "test-project") -> Project:
    p = Project(
        name=name,
        description="Test project",
        color="#6366f1",
        status=ProjectStatus.active,
        members=[],
        created_by=None,
    )
    await p.insert()
    return p


async def _make_document(
    project_id: str,
    title: str = "Test Document",
    content: str = "Some content",
    tags: list[str] | None = None,
    category: DocumentCategory = DocumentCategory.spec,
    is_deleted: bool = False,
) -> ProjectDocument:
    d = ProjectDocument(
        project_id=project_id,
        title=title,
        content=content,
        tags=tags or [],
        category=category,
        created_by="test",
        is_deleted=is_deleted,
    )
    await d.insert()
    return d


class TestCreateDocument:
    async def test_creates_document(self):
        from app.mcp.tools.documents import create_document

        p = await _make_project()
        result = await create_document(
            project_id=str(p.id),
            title="Auth Flow Spec",
            content="# Authentication\nOAuth2 + JWT",
            tags=["auth", "oauth"],
            category="spec",
        )

        assert result["title"] == "Auth Flow Spec"
        assert result["content"] == "# Authentication\nOAuth2 + JWT"
        assert result["tags"] == ["auth", "oauth"]
        assert result["category"] == "spec"
        assert result["project_id"] == str(p.id)
        assert "id" in result

    async def test_normalizes_tags(self):
        from app.mcp.tools.documents import create_document

        p = await _make_project()
        result = await create_document(
            project_id=str(p.id),
            title="Test",
            content="Content",
            tags=["  Auth  ", "OAUTH", ""],
        )
        assert result["tags"] == ["auth", "oauth"]

    async def test_rejects_empty_title(self):
        from app.mcp.tools.documents import create_document
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        with pytest.raises(ToolError, match="Title is required"):
            await create_document(project_id=str(p.id), title="", content="test")

    async def test_rejects_long_title(self):
        from app.mcp.tools.documents import create_document
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        with pytest.raises(ToolError, match="255"):
            await create_document(project_id=str(p.id), title="x" * 256, content="test")

    async def test_rejects_long_content(self):
        from app.mcp.tools.documents import create_document
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        with pytest.raises(ToolError, match="100000"):
            await create_document(project_id=str(p.id), title="Test", content="x" * 100001)

    async def test_rejects_invalid_category(self):
        from app.mcp.tools.documents import create_document
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        with pytest.raises(ToolError, match="Invalid category"):
            await create_document(project_id=str(p.id), title="Test", category="invalid")

    async def test_resolves_project_by_name(self):
        from app.mcp.tools.documents import create_document

        p = await _make_project("my-cool-project")
        result = await create_document(
            project_id="my-cool-project",
            title="Resolved by name",
        )
        assert result["project_id"] == str(p.id)


class TestGetDocument:
    async def test_gets_document(self):
        from app.mcp.tools.documents import get_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="My Doc")
        result = await get_document(document_id=str(d.id))
        assert result["title"] == "My Doc"

    async def test_not_found(self):
        from app.mcp.tools.documents import get_document
        from fastmcp.exceptions import ToolError

        with pytest.raises(ToolError, match="not found"):
            await get_document(document_id="000000000000000000000000")

    async def test_deleted_not_found(self):
        from app.mcp.tools.documents import get_document
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        d = await _make_document(str(p.id), is_deleted=True)
        with pytest.raises(ToolError, match="not found"):
            await get_document(document_id=str(d.id))


class TestUpdateDocument:
    async def test_updates_fields(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="Old Title", content="Old content")
        result = await update_document(
            document_id=str(d.id),
            title="New Title",
            content="New content",
            category="design",
        )
        assert result["title"] == "New Title"
        assert result["content"] == "New content"
        assert result["category"] == "design"

    async def test_partial_update(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="Original", content="Keep this")
        result = await update_document(document_id=str(d.id), title="Changed")
        assert result["title"] == "Changed"
        assert result["content"] == "Keep this"

    async def test_updates_tags(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id), tags=["old"])
        result = await update_document(document_id=str(d.id), tags=["new", "TAGS"])
        assert result["tags"] == ["new", "tags"]


class TestDeleteDocument:
    async def test_soft_deletes(self):
        from app.mcp.tools.documents import delete_document

        p = await _make_project()
        d = await _make_document(str(p.id))
        result = await delete_document(document_id=str(d.id))
        assert result["success"] is True

        reloaded = await ProjectDocument.get(d.id)
        assert reloaded.is_deleted is True


class TestListDocuments:
    async def test_lists_project_documents(self):
        from app.mcp.tools.documents import list_documents

        p = await _make_project()
        await _make_document(str(p.id), title="Doc 1")
        await _make_document(str(p.id), title="Doc 2")

        result = await list_documents(project_id=str(p.id))
        assert result["total"] == 2
        assert len(result["items"]) == 2

    async def test_filters_by_category(self):
        from app.mcp.tools.documents import list_documents

        p = await _make_project()
        await _make_document(str(p.id), title="Spec Doc", category=DocumentCategory.spec)
        await _make_document(str(p.id), title="API Doc", category=DocumentCategory.api)

        result = await list_documents(project_id=str(p.id), category="spec")
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Spec Doc"

    async def test_filters_by_tag(self):
        from app.mcp.tools.documents import list_documents

        p = await _make_project()
        await _make_document(str(p.id), title="Tagged", tags=["auth"])
        await _make_document(str(p.id), title="Untagged")

        result = await list_documents(project_id=str(p.id), tag="auth")
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Tagged"

    async def test_excludes_deleted(self):
        from app.mcp.tools.documents import list_documents

        p = await _make_project()
        await _make_document(str(p.id), title="Active")
        await _make_document(str(p.id), title="Deleted", is_deleted=True)

        result = await list_documents(project_id=str(p.id))
        assert result["total"] == 1

    async def test_different_projects_isolated(self):
        from app.mcp.tools.documents import list_documents

        p1 = await _make_project("project-1")
        p2 = await _make_project("project-2")
        await _make_document(str(p1.id), title="P1 Doc")
        await _make_document(str(p2.id), title="P2 Doc")

        result = await list_documents(project_id=str(p1.id))
        assert result["total"] == 1
        assert result["items"][0]["title"] == "P1 Doc"


class TestSearchDocuments:
    async def test_regex_fallback_search(self):
        from app.mcp.tools.documents import search_documents

        p = await _make_project()
        await _make_document(str(p.id), title="認証フロー仕様", content="OAuth2 + JWT認証")
        await _make_document(str(p.id), title="DB設計", content="MongoDB schema")

        result = await search_documents(query="認証", project_id=str(p.id))
        assert result["total"] == 1
        assert result["items"][0]["title"] == "認証フロー仕様"
        assert result["_meta"]["search_engine"] == "regex"

    async def test_project_id_required(self):
        """Cross-project search is no longer supported — project_id is required."""
        from app.mcp.tools.documents import search_documents

        with pytest.raises(TypeError, match="project_id"):
            await search_documents(query="認証")

    async def test_rejects_empty_query(self):
        from app.mcp.tools.documents import search_documents
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        with pytest.raises(ToolError, match="Query is required"):
            await search_documents(query="", project_id=str(p.id))


class TestUpdateDocumentVersioning:
    async def test_creates_version_on_update(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="Original", content="Original content")

        result = await update_document(
            document_id=str(d.id),
            title="Updated",
            content="Updated content",
            change_summary="Revised spec",
        )

        assert result["title"] == "Updated"
        assert result["version"] == 2

        # Check version was created
        versions = await DocumentVersion.find(
            DocumentVersion.document_id == str(d.id),
        ).to_list()
        assert len(versions) == 1
        assert versions[0].version == 1
        assert versions[0].title == "Original"
        assert versions[0].content == "Original content"
        assert versions[0].change_summary == "Revised spec"

    async def test_version_increments_on_multiple_updates(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="v1")

        await update_document(document_id=str(d.id), title="v2")
        result = await update_document(document_id=str(d.id), title="v3")

        assert result["version"] == 3

        versions = await DocumentVersion.find(
            DocumentVersion.document_id == str(d.id),
        ).sort("-version").to_list()
        assert len(versions) == 2
        assert versions[0].version == 2
        assert versions[0].title == "v2"
        assert versions[1].version == 1
        assert versions[1].title == "v1"

    async def test_stores_task_id_in_version(self):
        from app.mcp.tools.documents import update_document

        p = await _make_project()
        d = await _make_document(str(p.id))

        await update_document(
            document_id=str(d.id),
            title="Changed",
            task_id="abc123",
            change_summary="Updated per task",
        )

        versions = await DocumentVersion.find(
            DocumentVersion.document_id == str(d.id),
        ).to_list()
        assert len(versions) == 1
        assert versions[0].task_id == "abc123"
        assert versions[0].change_summary == "Updated per task"


class TestGetDocumentHistory:
    async def test_returns_version_summaries(self):
        from app.mcp.tools.documents import get_document_history, update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="Original")
        await update_document(document_id=str(d.id), title="Updated")

        result = await get_document_history(document_id=str(d.id))
        assert result["current_version"] == 2
        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["version"] == 1
        assert result["items"][0]["title"] == "Original"
        # Summary should not include content
        assert "content" not in result["items"][0]

    async def test_empty_history_for_new_document(self):
        from app.mcp.tools.documents import get_document_history

        p = await _make_project()
        d = await _make_document(str(p.id))

        result = await get_document_history(document_id=str(d.id))
        assert result["total"] == 0
        assert result["items"] == []


class TestGetDocumentVersion:
    async def test_retrieves_specific_version(self):
        from app.mcp.tools.documents import get_document_version, update_document

        p = await _make_project()
        d = await _make_document(str(p.id), title="v1", content="Content v1")
        await update_document(document_id=str(d.id), title="v2", content="Content v2")

        result = await get_document_version(document_id=str(d.id), version=1)
        assert result["version"] == 1
        assert result["title"] == "v1"
        assert result["content"] == "Content v1"

    async def test_not_found_for_invalid_version(self):
        from app.mcp.tools.documents import get_document_version
        from fastmcp.exceptions import ToolError

        p = await _make_project()
        d = await _make_document(str(p.id))

        with pytest.raises(ToolError, match="Version 99 not found"):
            await get_document_version(document_id=str(d.id), version=99)
