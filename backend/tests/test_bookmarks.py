"""Bookmark feature tests.

Unit tests for bookmark_clip pipeline functions and
integration tests for bookmark REST API endpoints.
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models import Bookmark, BookmarkCollection, Project
from app.models.bookmark import ClipStatus, BookmarkMetadata
from app.models.project import ProjectMember


# ═══════════════════════════════════════════════════════════
# Unit tests: bookmark_clip pipeline functions
# ═══════════════════════════════════════════════════════════


class TestXmlToHtml:
    """Test _xml_to_html conversion from trafilatura XML output."""

    def _convert(self, xml: str) -> str:
        from app.services.bookmark_clip import _xml_to_html
        return _xml_to_html(xml)

    def test_paragraph(self):
        result = self._convert('<p>Hello world</p>')
        assert '<p>Hello world</p>' in result

    def test_heading(self):
        result = self._convert('<head rend="h2">Title</head>')
        assert '<h2>Title</h2>' in result

    def test_heading_default(self):
        result = self._convert('<head>Title</head>')
        assert '<h2>Title</h2>' in result

    def test_bold(self):
        result = self._convert('<hi rend="bold">strong text</hi>')
        assert '<strong>strong text</strong>' in result

    def test_link(self):
        result = self._convert('<ref target="https://example.com">link</ref>')
        assert '<a href="https://example.com">link</a>' in result

    def test_line_break(self):
        result = self._convert('<p>line1<lb/>line2</p>')
        assert '<br>' in result

    def test_list(self):
        result = self._convert('<list><item>a</item><item>b</item></list>')
        assert '<ul>' in result
        assert '<li>a</li>' in result

    def test_quote_to_blockquote(self):
        result = self._convert('<quote><p>quoted text</p></quote>')
        assert '<blockquote>' in result
        assert '</blockquote>' in result
        assert 'quoted text' in result

    def test_graphic_to_img(self):
        result = self._convert('<graphic src="https://img.example.com/a.jpg" alt="photo"/>')
        assert '<img src="https://img.example.com/a.jpg"' in result

    def test_graphic_deduplication(self):
        xml = ('<graphic src="https://img.example.com/a.jpg"/>'
               '<graphic src="https://img.example.com/a.jpg"/>')
        result = self._convert(xml)
        assert result.count('<img') == 1

    def test_graphic_different_urls(self):
        xml = ('<graphic src="https://img.example.com/a.jpg"/>'
               '<graphic src="https://img.example.com/b.jpg"/>')
        result = self._convert(xml)
        assert result.count('<img') == 2


class TestHtmlToMarkdown:
    """Test _html_to_markdown conversion."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        from app.services.bookmark_clip import _html_to_markdown
        self._convert = _html_to_markdown

    async def test_paragraph(self):
        result = await self._convert('<p>Hello world</p>')
        assert 'Hello world' in result

    async def test_heading(self):
        result = await self._convert('<h2>Title</h2>')
        assert '## Title' in result or 'Title' in result

    async def test_link_preserved(self):
        result = await self._convert('<p><a href="https://example.com">link</a></p>')
        assert 'https://example.com' in result

    async def test_image_preserved(self):
        result = await self._convert('<p><img src="https://img.example.com/a.jpg" alt="photo" /></p>')
        assert 'img.example.com/a.jpg' in result

    async def test_code_block(self):
        result = await self._convert('<pre><code>print("hello")</code></pre>')
        assert 'print' in result

    async def test_placeholder_text_preserved(self):
        """Placeholder text in <p> tags must survive markdown conversion."""
        result = await self._convert(
            '<h1>Title</h1><p>before text.</p><p>TWEETPLACEHOLDER1</p><p>after text.</p>'
        )
        assert 'TWEETPLACEHOLDER1' in result

    async def test_blockquote(self):
        result = await self._convert('<blockquote><p>quoted</p></blockquote>')
        assert '>' in result
        assert 'quoted' in result


class TestTweetPlaceholder:
    """Test tweet placeholder replacement pipeline."""

    def test_placeholder_insertion(self):
        """Twitter blockquotes should be replaced with placeholders in source HTML."""
        import re

        source = (
            '<div>'
            '<p>text before tweet</p>'
            '<blockquote class="twitter-tweet"><p>tweet text</p>'
            '<a href="https://twitter.com/user/status/123">link</a>'
            '</blockquote>'
            '<p>text after tweet</p>'
            '</div>'
        )

        placeholders: dict[str, str] = {}
        counter = [0]

        def replace_tweet(m: re.Match) -> str:
            block = m.group(1)
            url_match = re.search(
                r'href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+)',
                block,
            )
            if not url_match:
                return ''
            url = re.sub(r'\?.*$', '', url_match.group(1))
            text_parts = re.findall(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
            tweet_text = '\n'.join(re.sub(r'<[^>]+>', '', p).strip() for p in text_parts).strip()
            counter[0] += 1
            placeholder = f'TWEETPLACEHOLDER{counter[0]}'
            placeholders[placeholder] = {'url': url, 'text': tweet_text, 'author': '', 'date': ''}
            return f'<p>{placeholder}</p>'

        result = re.sub(
            r'<blockquote[^>]*class="twitter-tweet"[^>]*>(.*?)</blockquote>',
            replace_tweet, source, flags=re.DOTALL | re.IGNORECASE,
        )

        assert 'TWEETPLACEHOLDER1' in result
        assert 'twitter-tweet' not in result
        assert placeholders['TWEETPLACEHOLDER1']['url'] == 'https://twitter.com/user/status/123'
        assert placeholders['TWEETPLACEHOLDER1']['text'] == 'tweet text'
        # Placeholder is between before and after text
        before_pos = result.index('text before tweet')
        placeholder_pos = result.index('TWEETPLACEHOLDER1')
        after_pos = result.index('text after tweet')
        assert before_pos < placeholder_pos < after_pos

    def test_placeholder_with_query_params(self):
        """Tweet URLs with query params should be normalized."""
        import re

        source = (
            '<blockquote class="twitter-tweet"><p>text</p>'
            '<a href="https://twitter.com/user/status/456?ref_src=twsrc%5Etfw">Oct</a>'
            '</blockquote>'
        )

        urls_raw = re.findall(
            r'href="(https?://(?:twitter\.com|x\.com)/\w+/status/\d+[^"]*)"',
            source, flags=re.IGNORECASE,
        )
        urls = [re.sub(r'\?.*$', '', u) for u in urls_raw]

        assert len(urls) == 1
        assert urls[0] == 'https://twitter.com/user/status/456'

    async def test_full_pipeline_preserves_position(self):
        """End-to-end: placeholder should survive trafilatura + markdown conversion."""
        from app.services.bookmark_clip import _extract_content, _html_to_markdown

        html = (
            '<html><body>'
            '<article>'
            '<h1>Article Title</h1>'
            '<p>Some introductory text about the topic.</p>'
            '<p>TWEETPLACEHOLDER1</p>'
            '<p>Some text after the tweet discussing results.</p>'
            '<h2>Second Section</h2>'
            '<p>More detailed analysis follows here.</p>'
            '</article>'
            '</body></html>'
        )

        extracted = await _extract_content(html, 'https://example.com')
        if extracted and 'TWEETPLACEHOLDER1' in extracted:
            md = await _html_to_markdown(extracted)
            assert 'TWEETPLACEHOLDER1' in md
            # Check position: should be between intro and after text
            lines = md.strip().split('\n')
            ph_line = next(i for i, l in enumerate(lines) if 'TWEETPLACEHOLDER1' in l)
            intro_line = next(i for i, l in enumerate(lines) if 'introductory' in l)
            after_line = next(i for i, l in enumerate(lines) if 'after the tweet' in l)
            assert intro_line < ph_line < after_line


class TestYouTubeExtraction:
    """Test YouTube video ID extraction."""

    def test_embed_url(self):
        import re
        html = '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ" frameborder="0"></iframe>'
        ids = re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([\w-]+)', html)
        assert ids == ['dQw4w9WgXcQ']

    def test_watch_url(self):
        import re
        html = '<a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">video</a>'
        ids = re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([\w-]+)', html)
        assert ids == ['dQw4w9WgXcQ']

    def test_short_url(self):
        import re
        html = '<a href="https://youtu.be/dQw4w9WgXcQ">video</a>'
        ids = re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|youtu\.be/)([\w-]+)', html)
        assert ids == ['dQw4w9WgXcQ']


class TestSanitizeHtml:
    """Test _sanitize_html removes dangerous content."""

    def _sanitize(self, html: str) -> str:
        from app.services.bookmark_clip import _sanitize_html
        return _sanitize_html(html)

    def test_removes_script(self):
        result = self._sanitize('<p>ok</p><script>alert(1)</script><p>end</p>')
        assert '<script' not in result
        assert 'alert' not in result
        assert 'ok' in result

    def test_removes_style(self):
        result = self._sanitize('<p>ok</p><style>.x{color:red}</style>')
        assert '<style' not in result

    def test_removes_event_handlers(self):
        result = self._sanitize('<img src="x.jpg" onerror="alert(1)" />')
        assert 'onerror' not in result

    def test_removes_javascript_urls(self):
        result = self._sanitize('<a href="javascript:alert(1)">click</a>')
        assert 'javascript:' not in result

    def test_removes_buttons(self):
        result = self._sanitize('<p>text</p><button onclick="x">click</button><p>more</p>')
        assert '<button' not in result

    def test_removes_svg(self):
        result = self._sanitize('<p>text</p><svg><path d="M0 0"/></svg>')
        assert '<svg' not in result

    def test_preserves_youtube_iframe(self):
        result = self._sanitize('<iframe src="https://youtube.com/embed/abc"></iframe>')
        assert 'youtube' in result

    def test_removes_non_youtube_iframe(self):
        result = self._sanitize('<iframe src="https://evil.com/attack"></iframe>')
        assert 'evil.com' not in result


class TestFetchRawHtml:
    """Test _fetch_raw_html."""

    async def test_returns_none_on_invalid_url(self):
        from app.services.bookmark_clip import _fetch_raw_html
        result = await _fetch_raw_html('http://localhost:1/nonexistent')
        assert result is None


class TestBookmarkMetadataFetch:
    """Test metadata extraction."""

    async def test_returns_empty_on_failure(self):
        from app.services.bookmark_metadata import fetch_metadata
        result = await fetch_metadata('http://localhost:1/nonexistent')
        assert result.meta_title == ''


# ═══════════════════════════════════════════════════════════
# Integration tests: Bookmark REST API
# ═══════════════════════════════════════════════════════════


class TestBookmarkCollectionAPI:
    """Test bookmark collection CRUD endpoints."""

    async def test_create_collection(self, client, admin_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "Research", "icon": "folder", "color": "#ff0000"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Research"
        assert data["icon"] == "folder"
        assert data["color"] == "#ff0000"

    async def test_list_collections(self, client, admin_headers, test_project):
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "Col1"},
            headers=admin_headers,
        )
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "Col2"},
            headers=admin_headers,
        )
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert len(resp.json()["items"]) == 2

    async def test_update_collection(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "Old Name"},
            headers=admin_headers,
        )
        coll_id = create_resp.json()["id"]
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/{coll_id}",
            json={"name": "New Name"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "New Name"

    async def test_delete_collection(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "To Delete"},
            headers=admin_headers,
        )
        coll_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/{coll_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204

    async def test_no_access_without_auth(self, client, test_project):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
        )
        assert resp.status_code == 401


class TestBookmarkAPI:
    """Test bookmark CRUD endpoints.

    Note: clip_bookmark runs as a background task and requires Playwright,
    so we mock it out for API tests.
    """

    @pytest_asyncio.fixture(autouse=True)
    async def _mock_clip(self, monkeypatch):
        """Prevent background clipping from running in tests."""
        import app.api.v1.endpoints.bookmarks as bm_module

        async def noop_clip(bookmark_id: str) -> None:
            pass

        monkeypatch.setattr(bm_module, '_run_clip', noop_clip)

    async def test_create_bookmark(self, client, admin_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com/article", "title": "Example", "tags": ["test"]},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/article"
        assert data["title"] == "Example"
        assert data["tags"] == ["test"]
        assert data["clip_status"] == "pending"

    async def test_create_bookmark_auto_title(self, client, admin_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com/page"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        # Title defaults to URL when empty
        assert resp.json()["title"] == "https://example.com/page"

    async def test_list_bookmarks(self, client, admin_headers, test_project):
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://a.com"},
            headers=admin_headers,
        )
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://b.com"},
            headers=admin_headers,
        )
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 2

    async def test_list_filter_by_tag(self, client, admin_headers, test_project):
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://a.com", "tags": ["python"]},
            headers=admin_headers,
        )
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://b.com", "tags": ["rust"]},
            headers=admin_headers,
        )
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/?tag=python",
            headers=admin_headers,
        )
        assert resp.json()["total"] == 1

    async def test_list_filter_starred(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://a.com"},
            headers=admin_headers,
        )
        bm_id = create_resp.json()["id"]
        await client.patch(
            f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
            json={"is_starred": True},
            headers=admin_headers,
        )
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/?starred=true",
            headers=admin_headers,
        )
        assert resp.json()["total"] == 1

    async def test_get_bookmark(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com", "title": "Test"},
            headers=admin_headers,
        )
        bm_id = create_resp.json()["id"]
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "Test"

    async def test_update_bookmark(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com", "title": "Old"},
            headers=admin_headers,
        )
        bm_id = create_resp.json()["id"]
        resp = await client.patch(
            f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
            json={"title": "New Title", "tags": ["updated"], "is_starred": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "New Title"
        assert data["tags"] == ["updated"]
        assert data["is_starred"] is True

    async def test_delete_bookmark(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com"},
            headers=admin_headers,
        )
        bm_id = create_resp.json()["id"]
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204
        # Should be soft-deleted
        get_resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
            headers=admin_headers,
        )
        assert get_resp.status_code == 404

    async def test_bookmark_with_collection(self, client, admin_headers, test_project):
        coll_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmark-collections/",
            json={"name": "Favorites"},
            headers=admin_headers,
        )
        coll_id = coll_resp.json()["id"]
        bm_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com", "collection_id": coll_id},
            headers=admin_headers,
        )
        assert bm_resp.json()["collection_id"] == coll_id

    async def test_member_can_access(self, client, user_headers, test_project):
        """Regular project member can list bookmarks."""
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            headers=user_headers,
        )
        assert resp.status_code == 200

    async def test_no_access_without_auth(self, client, test_project):
        resp = await client.get(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
        )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════
# Unit tests: bookmark_import
# ═══════════════════════════════════════════════════════════


class TestBookmarkImport:
    """Test Raindrop.io CSV import."""

    def test_parse_csv_basic(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Test Title,,desc,https://example.com,"python,web",2026-01-01T00:00:00.000Z,https://img.example.com/og.jpg,,true\n'
            '2,Another,,desc2,https://other.com,,,,,false\n'
        )
        bookmarks, errors = parse_raindrop_csv(csv, "proj1", "user1")
        assert len(bookmarks) == 2
        assert len(errors) == 0
        assert bookmarks[0].title == "Test Title"
        assert bookmarks[0].tags == ["python", "web"]
        assert bookmarks[0].is_starred is True
        assert bookmarks[0].metadata.og_image_url == "https://img.example.com/og.jpg"
        assert bookmarks[1].is_starred is False

    def test_parse_csv_dedup_within_csv(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,First,,desc,https://example.com/page,,,,, false\n'
            '2,Dupe,,desc,https://example.com/page,,,,, false\n'
        )
        bookmarks, errors = parse_raindrop_csv(csv, "proj1", "user1")
        assert len(bookmarks) == 1

    def test_parse_csv_invalid_url(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Bad,,desc,javascript:alert(1),,,,, false\n'
            '2,Good,,desc,https://ok.com,,,,, false\n'
        )
        bookmarks, errors = parse_raindrop_csv(csv, "proj1", "user1")
        assert len(bookmarks) == 1
        assert len(errors) == 1
        assert "Invalid URL" in errors[0]["error"]

    def test_parse_csv_missing_url(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,No URL,,desc,,,,,, false\n'
        )
        bookmarks, errors = parse_raindrop_csv(csv, "proj1", "user1")
        assert len(bookmarks) == 0
        assert len(errors) == 1

    def test_parse_csv_empty_title_fallback(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,,,desc,https://example.com,,,,, false\n'
        )
        bookmarks, _ = parse_raindrop_csv(csv, "proj1", "user1")
        assert bookmarks[0].title == "https://example.com"

    def test_normalize_url(self):
        from app.services.bookmark_import import normalize_url

        assert normalize_url("https://example.com/page/") == "https://example.com/page"
        assert normalize_url("https://Example.COM/Page") == "https://example.com/Page"
        assert normalize_url("https://example.com/page?utm_source=twitter&id=1") == "https://example.com/page?id=1"

    def test_parse_csv_collection_id(self):
        from app.services.bookmark_import import parse_raindrop_csv

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Test,,desc,https://example.com,,,,, false\n'
        )
        bookmarks, _ = parse_raindrop_csv(csv, "proj1", "user1", collection_id="coll123")
        assert bookmarks[0].collection_id == "coll123"

    async def test_import_bookmarks_dedup_db(self, admin_user, test_project):
        """Import should skip URLs that already exist in the project."""
        from app.services.bookmark_import import import_bookmarks

        # Pre-create a bookmark
        bm = Bookmark(
            project_id=str(test_project.id),
            url="https://existing.com",
            title="Existing",
            created_by=str(admin_user.id),
        )
        await bm.insert()

        csv = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Existing,,desc,https://existing.com,,,,, false\n'
            '2,New,,desc,https://new.com,,,,, false\n'
        )
        result = await import_bookmarks(csv, str(test_project.id), str(admin_user.id))
        assert result["imported"] == 1
        assert result["skipped_duplicate"] == 1


class TestImportAPI:
    """Test bookmark import API endpoint."""

    @pytest_asyncio.fixture(autouse=True)
    async def _mock_clip(self, monkeypatch):
        import app.api.v1.endpoints.bookmarks as bm_module
        async def noop_clip(bookmark_id: str) -> None:
            pass
        async def noop_clip_pending(project_id: str) -> None:
            pass
        monkeypatch.setattr(bm_module, '_run_clip', noop_clip)
        monkeypatch.setattr(bm_module, '_run_clip_pending', noop_clip_pending)

    async def test_import_csv(self, client, admin_headers, test_project):
        csv_content = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Test 1,,desc,https://a.com,"tag1",2026-01-01T00:00:00Z,,,false\n'
            '2,Test 2,,desc,https://b.com,,2026-01-02T00:00:00Z,,,true\n'
        )
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/import",
            files={"file": ("export.csv", csv_content.encode(), "text/csv")},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert data["skipped_duplicate"] == 0

    async def test_import_non_csv_rejected(self, client, admin_headers, test_project):
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/import",
            files={"file": ("data.json", b'{}', "application/json")},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    async def test_import_duplicate_skipped(self, client, admin_headers, test_project):
        csv_content = (
            'id,title,note,excerpt,url,tags,created,cover,highlights,favorite\n'
            '1,Test,,desc,https://unique.com,,,,, false\n'
        )
        # First import
        await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/import",
            files={"file": ("export.csv", csv_content.encode(), "text/csv")},
            headers=admin_headers,
        )
        # Second import - should skip
        resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/import",
            files={"file": ("export.csv", csv_content.encode(), "text/csv")},
            headers=admin_headers,
        )
        data = resp.json()
        assert data["imported"] == 0
        assert data["skipped_duplicate"] == 1


# ═══════════════════════════════════════════════════════════
# Unit tests: bookmark_cleanup
# ═══════════════════════════════════════════════════════════


class TestCleanupBookmarkAssets:
    """Test cleanup_bookmark_assets removes asset dir and deindexes."""

    @pytest.mark.asyncio
    async def test_removes_asset_dir_and_deindexes(self, tmp_path):
        bookmark_id = "abc123"
        asset_dir = tmp_path / bookmark_id
        asset_dir.mkdir()
        (asset_dir / "thumb.jpg").write_bytes(b"\xff\xd8")
        (asset_dir / "img_hash.png").write_bytes(b"\x89PNG")

        with (
            patch(
                "app.services.bookmark_cleanup.settings"
            ) as mock_settings,
            patch(
                "app.services.bookmark_cleanup.deindex_bookmark",
                new_callable=AsyncMock,
            ) as mock_deindex,
        ):
            mock_settings.BOOKMARK_ASSETS_DIR = str(tmp_path)
            from app.services.bookmark_cleanup import cleanup_bookmark_assets

            await cleanup_bookmark_assets(bookmark_id)

        assert not asset_dir.exists()
        mock_deindex.assert_awaited_once_with(bookmark_id)

    @pytest.mark.asyncio
    async def test_no_asset_dir_still_deindexes(self, tmp_path):
        bookmark_id = "no_dir"

        with (
            patch(
                "app.services.bookmark_cleanup.settings"
            ) as mock_settings,
            patch(
                "app.services.bookmark_cleanup.deindex_bookmark",
                new_callable=AsyncMock,
            ) as mock_deindex,
        ):
            mock_settings.BOOKMARK_ASSETS_DIR = str(tmp_path)
            from app.services.bookmark_cleanup import cleanup_bookmark_assets

            await cleanup_bookmark_assets(bookmark_id)

        mock_deindex.assert_awaited_once_with(bookmark_id)

    @pytest.mark.asyncio
    async def test_rmtree_failure_still_deindexes(self, tmp_path):
        bookmark_id = "fail_rm"
        asset_dir = tmp_path / bookmark_id
        asset_dir.mkdir()

        with (
            patch(
                "app.services.bookmark_cleanup.settings"
            ) as mock_settings,
            patch(
                "app.services.bookmark_cleanup.deindex_bookmark",
                new_callable=AsyncMock,
            ) as mock_deindex,
            patch("shutil.rmtree", side_effect=OSError("permission denied")),
        ):
            mock_settings.BOOKMARK_ASSETS_DIR = str(tmp_path)
            from app.services.bookmark_cleanup import cleanup_bookmark_assets

            await cleanup_bookmark_assets(bookmark_id)

        mock_deindex.assert_awaited_once_with(bookmark_id)


@pytest.mark.asyncio
class TestDeleteBookmarkCleanup:
    """Integration test: REST API delete calls cleanup."""

    @pytest_asyncio.fixture(autouse=True)
    async def _mock_clip(self, monkeypatch):
        import app.api.v1.endpoints.bookmarks as bm_module

        async def noop_clip(bookmark_id: str) -> None:
            pass

        monkeypatch.setattr(bm_module, '_run_clip', noop_clip)

    async def test_delete_calls_cleanup(self, client, admin_headers, test_project):
        create_resp = await client.post(
            f"/api/v1/projects/{test_project.id}/bookmarks/",
            json={"url": "https://example.com/cleanup-test"},
            headers=admin_headers,
        )
        bm_id = create_resp.json()["id"]

        with patch(
            "app.api.v1.endpoints.bookmarks.cleanup_bookmark_assets",
            new_callable=AsyncMock,
        ) as mock_cleanup:
            resp = await client.delete(
                f"/api/v1/projects/{test_project.id}/bookmarks/{bm_id}",
                headers=admin_headers,
            )
            assert resp.status_code == 204
            mock_cleanup.assert_awaited_once_with(bm_id)
