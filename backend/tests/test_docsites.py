"""Tests for DocSite API endpoints and sidebar parser."""

import pytest
import pytest_asyncio

from app.models.docsite import DocPage, DocSite, DocSiteSection
from app.services.docsite_import import parse_sidebar


# ── Sidebar parser tests ─────────────────────────────────────


class TestParseSidebar:
    def test_simple_links(self):
        text = "- [Page One](document/page-one.md)\n- [Page Two](document/page-two.md)\n"
        result = parse_sidebar(text)
        assert len(result) == 2
        assert result[0].title == "Page One"
        assert result[0].path == "document/page-one"
        assert result[1].title == "Page Two"
        assert result[1].path == "document/page-two"

    def test_nested_sections(self):
        text = (
            "- **Section A**\n"
            "  - [Child 1](a/child1.md)\n"
            "  - [Child 2](a/child2.md)\n"
            "- **Section B**\n"
            "  - [Child 3](b/child3.md)\n"
        )
        result = parse_sidebar(text)
        assert len(result) == 2
        assert result[0].title == "Section A"
        assert result[0].path is None
        assert len(result[0].children) == 2
        assert result[0].children[0].title == "Child 1"
        assert result[0].children[0].path == "a/child1"

    def test_deeply_nested(self):
        text = (
            "- **Level 1**\n"
            "  - **Level 2**\n"
            "    - [Deep](deep/page.md)\n"
        )
        result = parse_sidebar(text)
        assert len(result) == 1
        assert result[0].title == "Level 1"
        assert len(result[0].children) == 1
        assert result[0].children[0].title == "Level 2"
        assert len(result[0].children[0].children) == 1
        assert result[0].children[0].children[0].path == "deep/page"

    def test_back_link_skipped(self):
        text = "[← Back](/)\n\n- [Page](page.md)\n"
        result = parse_sidebar(text)
        assert len(result) == 1
        assert result[0].title == "Page"

    def test_plain_text_items(self):
        text = "- **Group**\n  - Plain Text\n  - [Link](link.md)\n"
        result = parse_sidebar(text)
        assert len(result) == 1
        assert len(result[0].children) == 2
        assert result[0].children[0].title == "Plain Text"
        assert result[0].children[0].path is None
        assert result[0].children[1].path == "link"

    def test_empty_input(self):
        assert parse_sidebar("") == []
        assert parse_sidebar("\n\n") == []

    def test_pico_discover_sidebar(self):
        """Test with real PICO discover sidebar content."""
        text = (
            "[← Back](/)\n\n"
            "- [PICO OS 6概要](document/discover/pico-os-6-overview.md)\n"
            "- [VR、MR、ARについて](document/discover/vr-xr-mr.md)\n"
            "- [6 DoFと3 DoF](document/discover/6-dof-and-3-dof.md)\n"
        )
        result = parse_sidebar(text)
        assert len(result) == 3
        assert result[0].title == "PICO OS 6概要"
        assert result[0].path == "document/discover/pico-os-6-overview"


# ── DocSite API tests ────────────────────────────────────────


@pytest_asyncio.fixture
async def sample_docsite():
    site = DocSite(
        name="Test Docs",
        description="Test documentation",
        source_url="https://example.com",
        page_count=2,
        sections=[
            DocSiteSection(
                title="Section 1",
                path=None,
                children=[
                    DocSiteSection(title="Page A", path="section1/page-a"),
                    DocSiteSection(title="Page B", path="section1/page-b"),
                ],
            ),
        ],
    )
    await site.insert()

    page_a = DocPage(
        site_id=str(site.id),
        path="section1/page-a",
        title="Page A",
        content="# Page A\n\nThis is page A content.",
        sort_order=0,
    )
    page_b = DocPage(
        site_id=str(site.id),
        path="section1/page-b",
        title="Page B",
        content="# Page B\n\nThis is page B content.",
        sort_order=1,
    )
    await page_a.insert()
    await page_b.insert()
    return site


@pytest.mark.asyncio
async def test_list_docsites(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get("/api/v1/docsites", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test Docs"
    assert data[0]["page_count"] == 2
    # Summary should not include sections tree
    assert "sections" not in data[0]


@pytest.mark.asyncio
async def test_get_docsite(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(f"/api/v1/docsites/{sample_docsite.id}", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Test Docs"
    assert len(data["sections"]) == 1
    assert data["sections"][0]["title"] == "Section 1"
    assert len(data["sections"][0]["children"]) == 2


@pytest.mark.asyncio
async def test_get_docsite_not_found(client, admin_headers, admin_user):
    resp = await client.get("/api/v1/docsites/000000000000000000000000", headers=admin_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_pages(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(f"/api/v1/docsites/{sample_docsite.id}/pages", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["path"] == "section1/page-a"
    # Should not include content
    assert "content" not in data[0]


@pytest.mark.asyncio
async def test_get_page(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(
        f"/api/v1/docsites/{sample_docsite.id}/pages/section1/page-a",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Page A"
    assert "page A content" in data["content"]


@pytest.mark.asyncio
async def test_get_page_not_found(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(
        f"/api/v1/docsites/{sample_docsite.id}/pages/nonexistent/path",
        headers=admin_headers,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_search_pages_regex(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(
        f"/api/v1/docsites/{sample_docsite.id}/search",
        params={"q": "Page A"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any("Page A" in item["title"] for item in data["items"])


@pytest.mark.asyncio
async def test_search_pages_empty_query(client, admin_headers, admin_user, sample_docsite):
    resp = await client.get(
        f"/api/v1/docsites/{sample_docsite.id}/search",
        params={"q": ""},
        headers=admin_headers,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete_docsite_admin_only(client, user_headers, regular_user, sample_docsite):
    resp = await client.delete(
        f"/api/v1/docsites/{sample_docsite.id}",
        headers=user_headers,
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_delete_docsite(client, admin_headers, admin_user, sample_docsite):
    resp = await client.delete(
        f"/api/v1/docsites/{sample_docsite.id}",
        headers=admin_headers,
    )
    assert resp.status_code == 204

    # Verify site and pages are deleted
    assert await DocSite.get(sample_docsite.id) is None
    pages = await DocPage.find(DocPage.site_id == str(sample_docsite.id)).to_list()
    assert len(pages) == 0


@pytest.mark.asyncio
async def test_unauthenticated_access(client, sample_docsite):
    resp = await client.get("/api/v1/docsites")
    assert resp.status_code == 401
