"""MCP tool unit tests for bookmark and bookmark collection tools.

Covers: create_bookmark, get_bookmark, update_bookmark, delete_bookmark,
        list_bookmarks, search_bookmarks, batch_bookmark_action, clip_bookmark,
        create_bookmark_collection, list_bookmark_collections,
        update_bookmark_collection, delete_bookmark_collection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.bookmark import Bookmark, BookmarkCollection, ClipStatus

# ── Shared mock key_info ────────────────────────────────────────
_MOCK_KEY_INFO = {"key_id": "test-key", "key_name": "test", "user_id": "test-user", "is_admin": True, "auth_kind": "api_key"}

_AUTH_PATH = "app.mcp.tools.bookmarks.authenticate"
_CHECK_PATH = "app.mcp.tools.bookmarks.check_project_access"
_GET_COMMON_PATH = "app.mcp.tools.bookmarks._get_common_project_id"
_CLIP_ENQUEUE_PATH = "app.mcp.tools.bookmarks.clip_queue"
_CLEANUP_PATH = "app.mcp.tools.bookmarks.cleanup_bookmark_assets"
_INDEX_PATH = "app.mcp.tools.bookmarks.index_bookmark"


# ── Fixtures ────────────────────────────────────────────────────


@pytest.fixture
def mock_auth():
    with patch(_AUTH_PATH, new_callable=AsyncMock, return_value=_MOCK_KEY_INFO) as m:
        yield m


@pytest.fixture
def mock_check():
    with patch(_CHECK_PATH) as m:
        yield m


@pytest.fixture
def mock_resolve(test_project):
    """Mock _get_common_project_id to return the test project's ID."""
    async def _get_common(key_info):
        return str(test_project.id)

    with patch(_GET_COMMON_PATH, side_effect=_get_common) as m:
        yield m


@pytest.fixture
def mock_clip_queue():
    mock_q = MagicMock()
    mock_q.enqueue = AsyncMock()
    with patch(_CLIP_ENQUEUE_PATH, mock_q):
        yield mock_q


@pytest.fixture
def mock_cleanup():
    with patch(_CLEANUP_PATH, new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def mock_index():
    with patch(_INDEX_PATH, new_callable=AsyncMock) as m:
        yield m


@pytest.fixture
def all_mocks(mock_auth, mock_check, mock_resolve, mock_clip_queue, mock_cleanup, mock_index):
    """Bundle all common mocks for convenience."""
    return {
        "auth": mock_auth,
        "check": mock_check,
        "resolve": mock_resolve,
        "clip_queue": mock_clip_queue,
        "cleanup": mock_cleanup,
        "index": mock_index,
    }


# ── Helper to create a bookmark in DB ──────────────────────────


async def _make_bookmark(
    project_id: str,
    url: str = "https://example.com",
    title: str = "Example",
    tags: list[str] | None = None,
    collection_id: str | None = None,
    is_deleted: bool = False,
    is_starred: bool = False,
    clip_status: ClipStatus = ClipStatus.pending,
) -> Bookmark:
    bm = Bookmark(
        project_id=project_id,
        url=url,
        title=title,
        tags=tags or [],
        collection_id=collection_id,
        is_deleted=is_deleted,
        is_starred=is_starred,
        clip_status=clip_status,
    )
    await bm.insert()
    return bm


async def _make_collection(
    project_id: str,
    name: str = "Test Collection",
    is_deleted: bool = False,
) -> BookmarkCollection:
    c = BookmarkCollection(
        project_id=project_id,
        name=name,
        is_deleted=is_deleted,
    )
    await c.insert()
    return c


# ═══════════════════════════════════════════════════════════════
#  Bookmark Collection Tools
# ═══════════════════════════════════════════════════════════════


class TestCreateBookmarkCollection:
    async def test_creates_collection(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import create_bookmark_collection

        result = await create_bookmark_collection(
            name="My Reads",
            description="Stuff to read",
            icon="book",
            color="#ff0000",
        )

        assert result["name"] == "My Reads"
        assert result["description"] == "Stuff to read"
        assert result["icon"] == "book"
        assert result["color"] == "#ff0000"
        assert result["project_id"] == str(test_project.id)

        db_coll = await BookmarkCollection.get(result["id"])
        assert db_coll is not None
        assert db_coll.name == "My Reads"
        assert db_coll.created_by == "mcp:test"

    async def test_empty_name_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import create_bookmark_collection

        with pytest.raises(ToolError, match="Name is required"):
            await create_bookmark_collection(name="")

    async def test_whitespace_name_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import create_bookmark_collection

        with pytest.raises(ToolError, match="Name is required"):
            await create_bookmark_collection(name="   ")

    async def test_name_exceeds_255_chars_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import create_bookmark_collection

        with pytest.raises(ToolError, match="255 characters"):
            await create_bookmark_collection(
                name="A" * 256
            )

    async def test_creator_without_key_name(self, test_project, mock_check, mock_resolve, mock_clip_queue, mock_cleanup, mock_index):
        """When key_name is absent, created_by falls back to 'mcp'."""
        from app.mcp.tools.bookmarks import create_bookmark_collection

        with patch(_AUTH_PATH, new_callable=AsyncMock, return_value={"key_id": "k", "user_id": "test-user", "is_admin": True, "auth_kind": "api_key"}):
            result = await create_bookmark_collection(
                name="No key name"
            )

        db_coll = await BookmarkCollection.get(result["id"])
        assert db_coll.created_by == "mcp"


class TestListBookmarkCollections:
    async def test_list_empty(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmark_collections

        result = await list_bookmark_collections()
        assert result["items"] == []
        assert result["total"] == 0

    async def test_list_excludes_deleted(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmark_collections

        pid = str(test_project.id)
        await _make_collection(pid, name="Active")
        await _make_collection(pid, name="Deleted", is_deleted=True)

        result = await list_bookmark_collections()
        assert result["total"] == 1
        assert result["items"][0]["name"] == "Active"

    async def test_list_multiple(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmark_collections

        pid = str(test_project.id)
        await _make_collection(pid, name="Alpha")
        await _make_collection(pid, name="Beta")

        result = await list_bookmark_collections()
        assert result["total"] == 2


class TestUpdateBookmarkCollection:
    async def test_update_fields(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="Old Name")

        result = await update_bookmark_collection(
            collection_id=str(coll.id),
            name="New Name",
            description="Updated desc",
            icon="star",
            color="#00ff00",
        )

        assert result["name"] == "New Name"
        assert result["description"] == "Updated desc"
        assert result["icon"] == "star"
        assert result["color"] == "#00ff00"

    async def test_partial_update(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="Original")

        result = await update_bookmark_collection(
            collection_id=str(coll.id), name="Changed"
        )
        assert result["name"] == "Changed"
        # icon should remain default
        assert result["icon"] == "folder"

    async def test_update_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import update_bookmark_collection

        with pytest.raises(ToolError, match="Collection not found"):
            await update_bookmark_collection(
                collection_id="000000000000000000000000", name="X"
            )

    async def test_update_deleted_collection_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import update_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="Gone", is_deleted=True)

        with pytest.raises(ToolError, match="Collection not found"):
            await update_bookmark_collection(
                collection_id=str(coll.id), name="Revive?"
            )


class TestDeleteBookmarkCollection:
    async def test_delete_collection(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import delete_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="ToDelete")

        result = await delete_bookmark_collection(collection_id=str(coll.id))

        assert result["deleted"] is True
        assert result["id"] == str(coll.id)

        db_coll = await BookmarkCollection.get(coll.id)
        assert db_coll.is_deleted is True

    async def test_delete_unsets_bookmark_collection_id(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import delete_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="Parent")
        bm = await _make_bookmark(pid, collection_id=str(coll.id))

        await delete_bookmark_collection(collection_id=str(coll.id))

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.collection_id is None

    async def test_delete_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import delete_bookmark_collection

        with pytest.raises(ToolError, match="Collection not found"):
            await delete_bookmark_collection(
                collection_id="000000000000000000000000"
            )

    async def test_delete_already_deleted_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import delete_bookmark_collection

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="AlreadyGone", is_deleted=True)

        with pytest.raises(ToolError, match="Collection not found"):
            await delete_bookmark_collection(collection_id=str(coll.id))


# ═══════════════════════════════════════════════════════════════
#  Bookmark Tools
# ═══════════════════════════════════════════════════════════════


class TestCreateBookmark:
    async def test_creates_bookmark(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import create_bookmark

        result = await create_bookmark(
            url="https://example.com/article",
            title="Good Article",
            description="A nice read",
            tags=["python", "testing"],
        )

        assert result["url"] == "https://example.com/article"
        assert result["title"] == "Good Article"
        assert result["description"] == "A nice read"
        assert result["tags"] == ["python", "testing"]
        assert result["clip_status"] == "pending"

        db_bm = await Bookmark.get(result["id"])
        assert db_bm is not None
        assert db_bm.created_by == "mcp:test"

        all_mocks["clip_queue"].enqueue.assert_called_once_with(result["id"])

    async def test_url_becomes_title_when_title_empty(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import create_bookmark

        result = await create_bookmark(
            url="https://example.com/page",
            title="",
        )
        assert result["title"] == "https://example.com/page"

    async def test_tags_normalized(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import create_bookmark

        result = await create_bookmark(
            url="https://example.com",
            tags=["  Python ", "TESTING", ""],
        )
        assert result["tags"] == ["python", "testing"]

    async def test_empty_url_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import create_bookmark

        with pytest.raises(ToolError, match="URL is required"):
            await create_bookmark(url="")

    async def test_url_too_long_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import create_bookmark

        with pytest.raises(ToolError, match="2048 characters"):
            await create_bookmark(
                url="https://x.com/" + "a" * 2040
            )

    async def test_creates_with_collection_id(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import create_bookmark

        pid = str(test_project.id)
        coll = await _make_collection(pid)

        result = await create_bookmark(
            url="https://example.com",
            collection_id=str(coll.id),
        )
        assert result["collection_id"] == str(coll.id)


class TestGetBookmark:
    async def test_get_existing_bookmark(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import get_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, url="https://get-test.com", title="Get Test")

        result = await get_bookmark(bookmark_id=str(bm.id))

        assert result["id"] == str(bm.id)
        assert result["url"] == "https://get-test.com"
        assert result["title"] == "Get Test"

    async def test_get_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import get_bookmark

        with pytest.raises(ToolError, match="Bookmark not found"):
            await get_bookmark(bookmark_id="000000000000000000000000")

    async def test_get_deleted_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import get_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, is_deleted=True)

        with pytest.raises(ToolError, match="Bookmark not found"):
            await get_bookmark(bookmark_id=str(bm.id))


class TestUpdateBookmark:
    async def test_update_title_and_description(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, title="Old Title")

        result = await update_bookmark(
            bookmark_id=str(bm.id),
            title="New Title",
            description="New desc",
        )

        assert result["title"] == "New Title"
        assert result["description"] == "New desc"

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.title == "New Title"

    async def test_update_tags(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, tags=["old"])

        result = await update_bookmark(
            bookmark_id=str(bm.id), tags=["New", " Fresh "]
        )
        assert result["tags"] == ["new", "fresh"]

    async def test_update_starred(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid)

        result = await update_bookmark(bookmark_id=str(bm.id), is_starred=True)
        assert result["is_starred"] is True

    async def test_update_collection_id(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        coll = await _make_collection(pid)
        bm = await _make_bookmark(pid)

        result = await update_bookmark(
            bookmark_id=str(bm.id), collection_id=str(coll.id)
        )
        assert result["collection_id"] == str(coll.id)

    async def test_unset_collection_id_with_empty_string(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        coll = await _make_collection(pid)
        bm = await _make_bookmark(pid, collection_id=str(coll.id))

        result = await update_bookmark(bookmark_id=str(bm.id), collection_id="")
        assert result["collection_id"] is None

    async def test_update_calls_index(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid)

        await update_bookmark(bookmark_id=str(bm.id), title="Indexed")
        all_mocks["index"].assert_called_once()

    async def test_update_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import update_bookmark

        with pytest.raises(ToolError, match="Bookmark not found"):
            await update_bookmark(
                bookmark_id="000000000000000000000000", title="X"
            )

    async def test_update_deleted_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import update_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, is_deleted=True)

        with pytest.raises(ToolError, match="Bookmark not found"):
            await update_bookmark(bookmark_id=str(bm.id), title="X")


class TestDeleteBookmark:
    async def test_soft_deletes_bookmark(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import delete_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid)

        result = await delete_bookmark(bookmark_id=str(bm.id))

        assert result["deleted"] is True
        assert result["id"] == str(bm.id)

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.is_deleted is True

    async def test_delete_calls_cleanup(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import delete_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid)

        await delete_bookmark(bookmark_id=str(bm.id))
        all_mocks["cleanup"].assert_called_once_with(str(bm.id))

    async def test_delete_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import delete_bookmark

        with pytest.raises(ToolError, match="Bookmark not found"):
            await delete_bookmark(bookmark_id="000000000000000000000000")

    async def test_delete_already_deleted_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import delete_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, is_deleted=True)

        with pytest.raises(ToolError, match="Bookmark not found"):
            await delete_bookmark(bookmark_id=str(bm.id))


class TestListBookmarks:
    async def test_list_empty(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        result = await list_bookmarks()
        assert result["items"] == []
        assert result["total"] == 0

    async def test_list_excludes_deleted(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://active.com", title="Active")
        await _make_bookmark(pid, url="https://deleted.com", title="Deleted", is_deleted=True)

        result = await list_bookmarks()
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Active"

    async def test_filter_by_collection(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        coll = await _make_collection(pid, name="My Coll")
        await _make_bookmark(pid, url="https://a.com", title="In coll", collection_id=str(coll.id))
        await _make_bookmark(pid, url="https://b.com", title="No coll")

        result = await list_bookmarks(collection_id=str(coll.id))
        assert result["total"] == 1
        assert result["items"][0]["title"] == "In coll"

    async def test_filter_uncategorized(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        coll = await _make_collection(pid)
        await _make_bookmark(pid, url="https://a.com", title="Has coll", collection_id=str(coll.id))
        await _make_bookmark(pid, url="https://b.com", title="No coll")

        result = await list_bookmarks(collection_id="")
        assert result["total"] == 1
        assert result["items"][0]["title"] == "No coll"

    async def test_filter_by_tag(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://a.com", title="Python", tags=["python"])
        await _make_bookmark(pid, url="https://b.com", title="Rust", tags=["rust"])

        result = await list_bookmarks(tag="python")
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Python"

    async def test_filter_starred(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://a.com", title="Starred", is_starred=True)
        await _make_bookmark(pid, url="https://b.com", title="Normal")

        result = await list_bookmarks(starred=True)
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Starred"

    async def test_pagination(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        for i in range(5):
            await _make_bookmark(pid, url=f"https://example.com/{i}", title=f"BM {i}")

        result = await list_bookmarks(limit=2, skip=0)
        assert result["total"] == 5
        assert len(result["items"]) == 2
        assert result["limit"] == 2
        assert result["skip"] == 0

    async def test_limit_capped_at_200(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import list_bookmarks

        pid = str(test_project.id)
        result = await list_bookmarks(limit=500)
        assert result["limit"] == 200


class TestSearchBookmarks:
    async def test_search_fallback_regex(self, test_project, all_mocks):
        """With Tantivy unavailable, falls back to MongoDB regex search."""
        from app.mcp.tools.bookmarks import search_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://python.org", title="Python Docs")
        await _make_bookmark(pid, url="https://rust-lang.org", title="Rust Docs")

        with patch("app.services.bookmark_search.BookmarkSearchService.get_instance", return_value=None):
            result = await search_bookmarks(query="Python")

        assert result["total"] == 1
        assert result["items"][0]["title"] == "Python Docs"

    async def test_search_empty_query_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import search_bookmarks

        with pytest.raises(ToolError, match="Query is required"):
            await search_bookmarks(query="")

    async def test_search_whitespace_query_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import search_bookmarks

        with pytest.raises(ToolError, match="Query is required"):
            await search_bookmarks(query="   ")

    async def test_search_matches_url(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import search_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://fastapi.tiangolo.com", title="FastAPI")
        await _make_bookmark(pid, url="https://django.com", title="Django")

        with patch("app.services.bookmark_search.BookmarkSearchService.get_instance", return_value=None):
            result = await search_bookmarks(query="fastapi")

        assert result["total"] == 1

    async def test_search_matches_tags(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import search_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://a.com", title="A", tags=["specialtag"])
        await _make_bookmark(pid, url="https://b.com", title="B", tags=["other"])

        with patch("app.services.bookmark_search.BookmarkSearchService.get_instance", return_value=None):
            result = await search_bookmarks(query="specialtag")

        assert result["total"] == 1

    async def test_search_without_project_id(self, test_project, all_mocks):
        """Search across all accessible bookmarks when no project_id given."""
        from app.mcp.tools.bookmarks import search_bookmarks

        pid = str(test_project.id)
        await _make_bookmark(pid, url="https://a.com", title="Global Match")

        with patch("app.services.bookmark_search.BookmarkSearchService.get_instance", return_value=None):
            result = await search_bookmarks(query="Global")

        assert result["total"] == 1


class TestBatchBookmarkAction:
    async def test_batch_star(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        bm1 = await _make_bookmark(pid, url="https://a.com")
        bm2 = await _make_bookmark(pid, url="https://b.com")

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm1.id), str(bm2.id)],
            action="star",
        )
        assert result["affected"] == 2
        assert result["failed"] == []

        db1 = await Bookmark.get(bm1.id)
        db2 = await Bookmark.get(bm2.id)
        assert db1.is_starred is True
        assert db2.is_starred is True

    async def test_batch_unstar(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, is_starred=True)

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)], action="unstar"
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.is_starred is False

    async def test_batch_delete(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        bm = await _make_bookmark(pid)

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)], action="delete"
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.is_deleted is True
        all_mocks["cleanup"].assert_called_once()

    async def test_batch_set_collection(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        coll = await _make_collection(pid)
        bm = await _make_bookmark(pid)

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)],
            action="set_collection",
            collection_id=str(coll.id),
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.collection_id == str(coll.id)

    async def test_batch_clear_collection(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        coll = await _make_collection(pid)
        bm = await _make_bookmark(pid, collection_id=str(coll.id))

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)],
            action="set_collection",
            collection_id="",
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.collection_id is None

    async def test_batch_add_tags(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, tags=["existing"])

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)],
            action="add_tags",
            tags=["New", "existing"],
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert "existing" in db_bm.tags
        assert "new" in db_bm.tags

    async def test_batch_remove_tags(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, tags=["keep", "remove"])

        result = await batch_bookmark_action(
            bookmark_ids=[str(bm.id)],
            action="remove_tags",
            tags=["remove"],
        )
        assert result["affected"] == 1

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.tags == ["keep"]

    async def test_batch_invalid_action_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import batch_bookmark_action

        with pytest.raises(ToolError, match="Invalid action"):
            await batch_bookmark_action(
                bookmark_ids=["000000000000000000000000"], action="explode"
            )

    async def test_batch_exceeds_max_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import batch_bookmark_action

        ids = ["000000000000000000000000"] * 201
        with pytest.raises(ToolError, match="Maximum 200"):
            await batch_bookmark_action(bookmark_ids=ids, action="star")

    async def test_batch_set_collection_without_id_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import batch_bookmark_action

        with pytest.raises(ToolError, match="collection_id required"):
            await batch_bookmark_action(
                bookmark_ids=["000000000000000000000000"],
                action="set_collection",
            )

    async def test_batch_add_tags_without_tags_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import batch_bookmark_action

        with pytest.raises(ToolError, match="tags required"):
            await batch_bookmark_action(
                bookmark_ids=["000000000000000000000000"],
                action="add_tags",
            )

    async def test_batch_nonexistent_bookmarks_reported_as_failed(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import batch_bookmark_action

        result = await batch_bookmark_action(
            bookmark_ids=["000000000000000000000000"],
            action="star",
        )
        assert result["affected"] == 0
        assert len(result["failed"]) == 1
        assert result["failed"][0]["error"] == "Not found"


class TestClipBookmark:
    async def test_clip_enqueues_bookmark(self, test_project, all_mocks):
        from app.mcp.tools.bookmarks import clip_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, clip_status=ClipStatus.done)

        result = await clip_bookmark(bookmark_id=str(bm.id))

        assert result["status"] == "pending"
        assert result["bookmark_id"] == str(bm.id)

        db_bm = await Bookmark.get(bm.id)
        assert db_bm.clip_status == ClipStatus.pending
        assert db_bm.clip_error == ""

        all_mocks["clip_queue"].enqueue.assert_called_once_with(str(bm.id))

    async def test_clip_nonexistent_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import clip_bookmark

        with pytest.raises(ToolError, match="Bookmark not found"):
            await clip_bookmark(bookmark_id="000000000000000000000000")

    async def test_clip_deleted_raises(self, test_project, all_mocks):
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.bookmarks import clip_bookmark

        pid = str(test_project.id)
        bm = await _make_bookmark(pid, is_deleted=True)

        with pytest.raises(ToolError, match="Bookmark not found"):
            await clip_bookmark(bookmark_id=str(bm.id))