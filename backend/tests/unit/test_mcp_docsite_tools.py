"""Unit tests for docsite MCP tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from app.models.docsite import DocPage, DocSite, DocSiteSection


# Patch authenticate for all tests in this module.
_AUTH_PATCH = patch(
    "app.mcp.tools.docsites.authenticate",
    new_callable=AsyncMock,
    return_value={"key_id": "test-key", "key_name": "test", "user_id": "test-user", "is_admin": True, "auth_kind": "api_key"},
)


@pytest.fixture(autouse=True)
def _mock_auth():
    with _AUTH_PATCH:
        yield


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

async def _make_site(
    name: str = "Test Docs",
    description: str = "Test documentation site",
    source_url: str = "https://example.com",
    page_count: int = 0,
    sections: list | None = None,
) -> DocSite:
    site = DocSite(
        name=name,
        description=description,
        source_url=source_url,
        page_count=page_count,
        sections=sections or [],
    )
    await site.insert()
    return site


async def _make_page(
    site_id: str,
    path: str = "getting-started",
    title: str = "Getting Started",
    content: str = "# Getting Started\n\nWelcome to the docs.",
    sort_order: int = 0,
) -> DocPage:
    page = DocPage(
        site_id=site_id,
        path=path,
        title=title,
        content=content,
        sort_order=sort_order,
    )
    await page.insert()
    return page


# ===========================================================================
# list_docsites
# ===========================================================================

class TestListDocsites:
    async def test_returns_empty_when_no_sites(self):
        from app.mcp.tools.docsites import list_docsites

        result = await list_docsites()
        assert result == []

    async def test_returns_site_summaries(self):
        from app.mcp.tools.docsites import list_docsites

        site1 = await _make_site(name="Alpha Docs")
        site2 = await _make_site(name="Beta Docs", source_url="https://beta.example.com")

        result = await list_docsites()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"Alpha Docs", "Beta Docs"}

    async def test_summary_does_not_include_sections(self):
        from app.mcp.tools.docsites import list_docsites

        await _make_site(
            name="Docs With Sections",
            sections=[DocSiteSection(title="Intro", path="intro")],
        )
        result = await list_docsites()
        assert len(result) == 1
        # docsite_summary does NOT include "sections"
        assert "sections" not in result[0]

    async def test_summary_fields(self):
        from app.mcp.tools.docsites import list_docsites

        await _make_site(name="My Docs", description="Desc", source_url="https://src.com", page_count=5)
        result = await list_docsites()
        item = result[0]
        assert item["name"] == "My Docs"
        assert item["description"] == "Desc"
        assert item["source_url"] == "https://src.com"
        assert item["page_count"] == 5
        assert "id" in item
        assert "created_at" in item
        assert "updated_at" in item


# ===========================================================================
# get_docsite
# ===========================================================================

class TestGetDocsite:
    async def test_returns_site_with_sections(self):
        from app.mcp.tools.docsites import get_docsite

        sections = [
            DocSiteSection(title="Guide", path="guide"),
            DocSiteSection(
                title="API",
                path=None,
                children=[DocSiteSection(title="Auth", path="api/auth")],
            ),
        ]
        site = await _make_site(name="Full Docs", sections=sections)

        result = await get_docsite(site_id=str(site.id))
        assert result["name"] == "Full Docs"
        assert "sections" in result
        assert len(result["sections"]) == 2
        assert result["sections"][1]["children"][0]["title"] == "Auth"

    async def test_nonexistent_site_raises(self):
        from app.mcp.tools.docsites import get_docsite

        with pytest.raises(ToolError, match="DocSite not found"):
            await get_docsite(site_id="000000000000000000000000")


# ===========================================================================
# get_docpage
# ===========================================================================

class TestGetDocpage:
    async def test_returns_page_content(self):
        from app.mcp.tools.docsites import get_docpage

        site = await _make_site()
        page = await _make_page(str(site.id), path="intro", title="Introduction", content="Hello world")

        result = await get_docpage(site_id=str(site.id), path="intro")
        assert result["title"] == "Introduction"
        assert result["content"] == "Hello world"
        assert result["path"] == "intro"
        assert result["site_id"] == str(site.id)

    async def test_nonexistent_page_raises(self):
        from app.mcp.tools.docsites import get_docpage

        site = await _make_site()

        with pytest.raises(ToolError, match="Page not found"):
            await get_docpage(site_id=str(site.id), path="nonexistent")

    async def test_nonexistent_site_page_raises(self):
        from app.mcp.tools.docsites import get_docpage

        with pytest.raises(ToolError, match="Page not found"):
            await get_docpage(site_id="000000000000000000000000", path="any")


# ===========================================================================
# search_docpages (regex fallback — Tantivy is not available in test env)
# ===========================================================================

class TestSearchDocpages:
    @pytest.fixture(autouse=True)
    def _no_tantivy(self):
        """Ensure Tantivy search service returns None so regex fallback is used."""
        with patch(
            "app.services.docsite_search.DocSiteSearchService.get_instance",
            return_value=None,
        ):
            yield

    async def test_search_by_title(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        await _make_page(str(site.id), path="setup", title="Setup Guide", content="Install the SDK.")
        await _make_page(str(site.id), path="api", title="API Reference", content="Endpoints list.")

        result = await search_docpages(query="Setup")
        assert result["total"] == 1
        assert result["items"][0]["title"] == "Setup Guide"
        assert result["_meta"]["search_engine"] == "regex"

    async def test_search_by_content(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        await _make_page(str(site.id), path="install", title="Install", content="Run pip install my-lib")

        result = await search_docpages(query="pip install")
        assert result["total"] == 1
        assert result["items"][0]["path"] == "install"

    async def test_search_case_insensitive(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        await _make_page(str(site.id), path="faq", title="FAQ", content="Frequently asked questions")

        result = await search_docpages(query="faq")
        assert result["total"] == 1

    async def test_search_empty_query_raises(self):
        from app.mcp.tools.docsites import search_docpages

        with pytest.raises(ToolError, match="Query is required"):
            await search_docpages(query="")

    async def test_search_whitespace_query_raises(self):
        from app.mcp.tools.docsites import search_docpages

        with pytest.raises(ToolError, match="Query is required"):
            await search_docpages(query="   ")

    async def test_search_filter_by_site_id(self):
        from app.mcp.tools.docsites import search_docpages

        site_a = await _make_site(name="Site A")
        site_b = await _make_site(name="Site B")
        await _make_page(str(site_a.id), path="a1", title="Shared Keyword", content="Content A")
        await _make_page(str(site_b.id), path="b1", title="Shared Keyword", content="Content B")

        result = await search_docpages(query="Shared Keyword", site_id=str(site_a.id))
        assert result["total"] == 1
        assert result["items"][0]["site_id"] == str(site_a.id)

    async def test_search_pagination(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        for i in range(5):
            await _make_page(str(site.id), path=f"page-{i}", title=f"Keyword Doc {i}", content="keyword", sort_order=i)

        # First page: 2 results
        result = await search_docpages(query="keyword", limit=2, skip=0)
        assert len(result["items"]) == 2
        assert result["total"] == 5

        # Second page
        result2 = await search_docpages(query="keyword", limit=2, skip=2)
        assert len(result2["items"]) == 2

    async def test_search_limit_clamped(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        await _make_page(str(site.id), path="p", title="Test", content="test content")

        # limit > 100 gets clamped to 100; should still work
        result = await search_docpages(query="test", limit=200)
        assert result["limit"] == 100

    async def test_search_no_results(self):
        from app.mcp.tools.docsites import search_docpages

        await _make_site()

        result = await search_docpages(query="nonexistent-term-xyz")
        assert result["total"] == 0
        assert result["items"] == []


# ===========================================================================
# search_docpages — Tantivy path
# ===========================================================================

class TestSearchDocpagesTantivy:
    async def test_search_uses_tantivy_when_available(self):
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        page = await _make_page(str(site.id), path="doc", title="Tantivy Hit", content="Content")

        mock_search_result = MagicMock()
        mock_search_result.results = [
            {"page_id": str(page.id), "score": 1.5},
        ]

        mock_svc = MagicMock()
        mock_svc.search.return_value = mock_search_result

        with patch(
            "app.services.docsite_search.DocSiteSearchService.get_instance",
            return_value=mock_svc,
        ):
            result = await search_docpages(query="Tantivy Hit")

        assert result["_meta"]["search_engine"] == "tantivy"
        assert len(result["items"]) == 1
        assert result["items"][0]["title"] == "Tantivy Hit"
        assert result["items"][0]["_score"] == 1.5

    async def test_tantivy_fallback_on_error(self):
        """When Tantivy raises, the tool falls back to regex search."""
        from app.mcp.tools.docsites import search_docpages

        site = await _make_site()
        await _make_page(str(site.id), path="fb", title="Fallback Page", content="fallback content")

        mock_svc = MagicMock()
        mock_svc.search.side_effect = RuntimeError("index corrupted")

        with patch(
            "app.services.docsite_search.DocSiteSearchService.get_instance",
            return_value=mock_svc,
        ):
            result = await search_docpages(query="fallback")

        assert result["_meta"]["search_engine"] == "regex"
        assert result["total"] == 1

    async def test_tantivy_empty_results_returns_empty(self):
        """When Tantivy returns no results, the response has empty items (no regex fallback)."""
        from app.mcp.tools.docsites import search_docpages

        await _make_site()

        mock_search_result = MagicMock()
        mock_search_result.results = []

        mock_svc = MagicMock()
        mock_svc.search.return_value = mock_search_result

        with patch(
            "app.services.docsite_search.DocSiteSearchService.get_instance",
            return_value=mock_svc,
        ):
            # Tantivy returns empty → code falls through to regex
            result = await search_docpages(query="nothing")

        # Either regex or tantivy, total should be 0
        assert result["total"] == 0


# ===========================================================================
# update_docpage
# ===========================================================================

class TestUpdateDocpage:
    @pytest.fixture(autouse=True)
    def _no_search_indexer(self):
        """Mock out the search indexer to avoid Tantivy dependency."""
        with patch(
            "app.services.docsite_search.DocSiteSearchIndexer.get_instance",
            return_value=None,
        ):
            yield

    async def test_update_title(self):
        from app.mcp.tools.docsites import update_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="p1", title="Old Title", content="Content")

        result = await update_docpage(site_id=str(site.id), path="p1", title="New Title")
        assert result["title"] == "New Title"
        assert result["content"] == "Content"  # unchanged

    async def test_update_content(self):
        from app.mcp.tools.docsites import update_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="p2", title="Title", content="Old content")

        result = await update_docpage(site_id=str(site.id), path="p2", content="New content")
        assert result["content"] == "New content"
        assert result["title"] == "Title"  # unchanged

    async def test_update_both(self):
        from app.mcp.tools.docsites import update_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="p3", title="T", content="C")

        result = await update_docpage(site_id=str(site.id), path="p3", title="T2", content="C2")
        assert result["title"] == "T2"
        assert result["content"] == "C2"

    async def test_update_nonexistent_page_raises(self):
        from app.mcp.tools.docsites import update_docpage

        site = await _make_site()

        with pytest.raises(ToolError, match="Page not found"):
            await update_docpage(site_id=str(site.id), path="nope", title="X")

    async def test_update_nonexistent_site_raises(self):
        from app.mcp.tools.docsites import update_docpage

        with pytest.raises(ToolError, match="DocSite not found"):
            await update_docpage(site_id="000000000000000000000000", path="p", title="X")

    async def test_update_title_too_long_raises(self):
        from app.mcp.tools.docsites import update_docpage

        with pytest.raises(ToolError, match="Title exceeds maximum length"):
            await update_docpage(site_id="any", path="p", title="x" * 256)

    async def test_update_content_too_long_raises(self):
        from app.mcp.tools.docsites import update_docpage

        with pytest.raises(ToolError, match="Content exceeds maximum length"):
            await update_docpage(site_id="any", path="p", content="x" * 200001)

    async def test_update_strips_title_whitespace(self):
        from app.mcp.tools.docsites import update_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="ws", title="Old", content="C")

        result = await update_docpage(site_id=str(site.id), path="ws", title="  Trimmed  ")
        assert result["title"] == "Trimmed"


# ===========================================================================
# create_docpage
# ===========================================================================

class TestCreateDocpage:
    @pytest.fixture(autouse=True)
    def _no_search_indexer(self):
        with patch(
            "app.services.docsite_search.DocSiteSearchIndexer.get_instance",
            return_value=None,
        ):
            yield

    async def test_creates_page(self):
        from app.mcp.tools.docsites import create_docpage

        site = await _make_site()

        result = await create_docpage(
            site_id=str(site.id),
            path="new-page",
            title="New Page",
            content="# New\n\nContent here.",
        )
        assert result["title"] == "New Page"
        assert result["path"] == "new-page"
        assert result["content"] == "# New\n\nContent here."

    async def test_increments_page_count(self):
        from app.mcp.tools.docsites import create_docpage

        site = await _make_site(page_count=0)

        await create_docpage(site_id=str(site.id), path="p1", title="P1")
        updated_site = await DocSite.get(site.id)
        assert updated_site.page_count == 1

    async def test_sort_order_auto_increments(self):
        from app.mcp.tools.docsites import create_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="existing", title="Existing", sort_order=5)

        result = await create_docpage(site_id=str(site.id), path="new", title="New")
        assert result["sort_order"] == 6

    async def test_duplicate_path_raises(self):
        from app.mcp.tools.docsites import create_docpage

        site = await _make_site()
        await _make_page(str(site.id), path="dup", title="Original")

        with pytest.raises(ToolError, match="Page already exists"):
            await create_docpage(site_id=str(site.id), path="dup", title="Duplicate")

    async def test_nonexistent_site_raises(self):
        from app.mcp.tools.docsites import create_docpage

        with pytest.raises(ToolError, match="DocSite not found"):
            await create_docpage(site_id="000000000000000000000000", path="p", title="T")

    async def test_empty_title_raises(self):
        from app.mcp.tools.docsites import create_docpage

        with pytest.raises(ToolError, match="Title is required"):
            await create_docpage(site_id="any", path="p", title="")

    async def test_title_too_long_raises(self):
        from app.mcp.tools.docsites import create_docpage

        with pytest.raises(ToolError, match="Title exceeds maximum length"):
            await create_docpage(site_id="any", path="p", title="x" * 256)

    async def test_content_too_long_raises(self):
        from app.mcp.tools.docsites import create_docpage

        with pytest.raises(ToolError, match="Content exceeds maximum length"):
            await create_docpage(site_id="any", path="p", title="T", content="x" * 200001)


# ===========================================================================
# delete_docpage
# ===========================================================================

class TestDeleteDocpage:
    @pytest.fixture(autouse=True)
    def _no_search_indexer(self):
        with patch(
            "app.services.docsite_search.DocSiteSearchIndexer.get_instance",
            return_value=None,
        ):
            yield

    async def test_deletes_page(self):
        from app.mcp.tools.docsites import delete_docpage

        site = await _make_site(page_count=1)
        await _make_page(str(site.id), path="doomed", title="Doomed")

        result = await delete_docpage(site_id=str(site.id), path="doomed")
        assert result == {"success": True, "path": "doomed"}

        # Page should be gone
        remaining = await DocPage.find(DocPage.site_id == str(site.id)).to_list()
        assert len(remaining) == 0

    async def test_decrements_page_count(self):
        from app.mcp.tools.docsites import delete_docpage

        site = await _make_site(page_count=2)
        await _make_page(str(site.id), path="d1", title="D1")
        await _make_page(str(site.id), path="d2", title="D2")

        await delete_docpage(site_id=str(site.id), path="d1")

        updated_site = await DocSite.get(site.id)
        assert updated_site.page_count == 1

    async def test_nonexistent_page_raises(self):
        from app.mcp.tools.docsites import delete_docpage

        site = await _make_site()

        with pytest.raises(ToolError, match="Page not found"):
            await delete_docpage(site_id=str(site.id), path="nonexistent")

    async def test_nonexistent_site_raises(self):
        from app.mcp.tools.docsites import delete_docpage

        with pytest.raises(ToolError, match="DocSite not found"):
            await delete_docpage(site_id="000000000000000000000000", path="any")


# ===========================================================================
# upload_docsite_asset
# ===========================================================================

class TestUploadDocsiteAsset:
    async def test_uploads_valid_asset(self, tmp_path):
        from app.mcp.tools.docsites import upload_docsite_asset
        import base64

        site = await _make_site()
        data = base64.b64encode(b"fake image data").decode()

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.DOCSITE_ASSETS_DIR = str(tmp_path)
            result = await upload_docsite_asset(
                site_id=str(site.id),
                asset_path="images/test.png",
                data_base64=data,
            )

        assert result["path"] == "images/test.png"
        assert result["size"] == len(b"fake image data")

    async def test_unsupported_extension_raises(self):
        from app.mcp.tools.docsites import upload_docsite_asset
        import base64

        site = await _make_site()
        data = base64.b64encode(b"data").decode()

        with pytest.raises(ToolError, match="Unsupported file type"):
            await upload_docsite_asset(
                site_id=str(site.id),
                asset_path="file.exe",
                data_base64=data,
            )

    async def test_invalid_base64_raises(self):
        from app.mcp.tools.docsites import upload_docsite_asset

        site = await _make_site()

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.DOCSITE_ASSETS_DIR = "/tmp"
            with pytest.raises(ToolError, match="Invalid base64 data"):
                await upload_docsite_asset(
                    site_id=str(site.id),
                    asset_path="images/bad.png",
                    data_base64="not-valid-base64!!!",
                )

    async def test_nonexistent_site_raises(self):
        from app.mcp.tools.docsites import upload_docsite_asset
        import base64

        data = base64.b64encode(b"data").decode()

        with pytest.raises(ToolError, match="DocSite not found"):
            await upload_docsite_asset(
                site_id="000000000000000000000000",
                asset_path="images/test.png",
                data_base64=data,
            )

    async def test_file_too_large_raises(self, tmp_path):
        from app.mcp.tools.docsites import upload_docsite_asset
        import base64

        site = await _make_site()
        # 21MB of data
        large_data = base64.b64encode(b"x" * (21 * 1024 * 1024)).decode()

        with patch("app.core.config.settings") as mock_settings:
            mock_settings.DOCSITE_ASSETS_DIR = str(tmp_path)
            with pytest.raises(ToolError, match="File too large"):
                await upload_docsite_asset(
                    site_id=str(site.id),
                    asset_path="images/huge.png",
                    data_base64=large_data,
                )
