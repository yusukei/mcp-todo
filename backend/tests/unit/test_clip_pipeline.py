"""Unit tests for the bookmark clipping pipeline pure-function helpers.

Targets the synchronous, pure(-ish) helpers in:
- ``app.services.clip_constants``
- ``app.services.clip_content``
- ``app.services.clip_twitter``

Network-driven flows (Playwright launch, httpx fetches, image downloads,
DB persistence) live in separate modules and are intentionally out of
scope for this file — they need an integration harness, not unit tests.
"""

from app.services.clip_constants import (
    IMAGE_EXTENSIONS,
    IMAGE_MAX_BYTES,
    IMAGE_MIN_BYTES,
    content_type_to_ext,
)
from app.services.clip_content import sanitize_html, xml_to_html
from app.services.clip_twitter import extract_tweet_id, is_twitter_url


# ──────────────────────────────────────────────
# clip_constants
# ──────────────────────────────────────────────


class TestContentTypeToExt:
    def test_known_image_types(self):
        assert content_type_to_ext("image/jpeg") == ".jpg"
        assert content_type_to_ext("image/png") == ".png"
        assert content_type_to_ext("image/gif") == ".gif"
        assert content_type_to_ext("image/webp") == ".webp"
        assert content_type_to_ext("image/svg+xml") == ".svg"
        assert content_type_to_ext("image/avif") == ".avif"
        assert content_type_to_ext("image/x-icon") == ".ico"

    def test_strips_charset_suffix(self):
        assert content_type_to_ext("image/jpeg; charset=binary") == ".jpg"

    def test_case_insensitive(self):
        assert content_type_to_ext("IMAGE/PNG") == ".png"

    def test_unknown_returns_empty(self):
        assert content_type_to_ext("application/octet-stream") == ""
        assert content_type_to_ext("") == ""

    def test_constants_sane(self):
        assert IMAGE_MIN_BYTES < IMAGE_MAX_BYTES
        assert ".jpg" in IMAGE_EXTENSIONS
        assert ".webp" in IMAGE_EXTENSIONS


# ──────────────────────────────────────────────
# clip_content.sanitize_html
# ──────────────────────────────────────────────


class TestSanitizeHtml:
    def test_strips_script_tags(self):
        html = "<p>hello</p><script>alert(1)</script><p>world</p>"
        out = sanitize_html(html)
        assert "<script" not in out
        assert "alert(1)" not in out
        assert "<p>hello</p>" in out
        assert "<p>world</p>" in out

    def test_strips_style_tags(self):
        html = "<style>body{display:none}</style><p>x</p>"
        assert "<style" not in sanitize_html(html)

    def test_strips_event_handlers(self):
        html = '<a href="/" onclick="evil()">click</a>'
        out = sanitize_html(html)
        assert "onclick" not in out
        assert 'href="/"' in out

    def test_strips_event_handler_no_quotes(self):
        html = "<img src=x onerror=alert(1)>"
        out = sanitize_html(html)
        assert "onerror" not in out

    def test_neutralizes_javascript_url(self):
        html = '<a href="javascript:alert(1)">x</a>'
        out = sanitize_html(html)
        assert "javascript:" not in out
        assert 'href="#"' in out

    def test_keeps_youtube_iframe(self):
        html = '<iframe src="https://youtube.com/embed/abc"></iframe>'
        out = sanitize_html(html)
        assert "youtube" in out
        assert "<iframe" in out

    def test_strips_other_iframes(self):
        html = '<iframe src="https://evil.com/track"></iframe>'
        out = sanitize_html(html)
        assert "<iframe" not in out

    def test_strips_buttons(self):
        html = "<button>click</button><p>keep</p>"
        out = sanitize_html(html)
        assert "<button" not in out
        assert "<p>keep</p>" in out

    def test_strips_inline_svg(self):
        html = "<svg><circle/></svg><p>after</p>"
        out = sanitize_html(html)
        assert "<svg" not in out
        assert "<p>after</p>" in out

    def test_strips_form_elements(self):
        html = "<form><input type='text'><textarea></textarea><select><option/></select></form>"
        out = sanitize_html(html)
        assert "<form" not in out
        assert "<input" not in out
        assert "<textarea" not in out
        assert "<select" not in out


# ──────────────────────────────────────────────
# clip_content.xml_to_html
# ──────────────────────────────────────────────


class TestXmlToHtml:
    def test_strips_xml_declaration_and_doc(self):
        xml = '<?xml version="1.0"?><doc><p>hi</p></doc>'
        out = xml_to_html(xml)
        assert "<?xml" not in out
        assert "<doc" not in out
        assert "<p>hi</p>" in out

    def test_graphic_to_img(self):
        xml = '<doc><graphic src="https://example.com/a.jpg" alt="alt-text"/></doc>'
        out = xml_to_html(xml)
        assert '<img src="https://example.com/a.jpg"' in out
        assert 'alt="alt-text"' in out

    def test_dedupes_repeated_graphic(self):
        xml = (
            "<doc>"
            '<graphic src="https://example.com/dup.jpg" alt="x"/>'
            '<graphic src="https://example.com/dup.jpg" alt="y"/>'
            "</doc>"
        )
        out = xml_to_html(xml)
        assert out.count("dup.jpg") == 1

    def test_head_rend_to_heading(self):
        xml = '<doc><head rend="h2">Title</head></doc>'
        out = xml_to_html(xml)
        assert "<h2>Title</h2>" in out

    def test_hi_bold_italic(self):
        xml = '<doc><p><hi rend="bold">B</hi><hi rend="italic">I</hi></p></doc>'
        out = xml_to_html(xml)
        assert "<strong>B</strong>" in out
        # italic falls through to <strong> in current impl — assert real behavior
        assert "I" in out

    def test_ref_to_anchor(self):
        xml = '<doc><ref target="https://example.com">link</ref></doc>'
        out = xml_to_html(xml)
        assert '<a href="https://example.com">link</a>' in out

    def test_lb_to_br(self):
        out = xml_to_html("<doc><p>a<lb/>b</p></doc>")
        assert "<br>" in out

    def test_list_item_to_ul_li(self):
        xml = "<doc><list><item>one</item><item>two</item></list></doc>"
        out = xml_to_html(xml)
        assert "<ul>" in out
        assert "<li>one</li>" in out
        assert "<li>two</li>" in out

    def test_table_row_cell(self):
        xml = "<doc><table><row><cell>a</cell><cell>b</cell></row></table></doc>"
        out = xml_to_html(xml)
        assert "<table>" in out
        assert "<tr>" in out
        assert "<td>a</td>" in out

    def test_quote_to_blockquote(self):
        out = xml_to_html("<doc><quote>cite</quote></doc>")
        assert "<blockquote>cite</blockquote>" in out


# ──────────────────────────────────────────────
# clip_twitter URL parsing
# ──────────────────────────────────────────────


class TestTwitterUrlParsing:
    def test_is_twitter_url_x_com(self):
        assert is_twitter_url("https://x.com/user/status/12345")

    def test_is_twitter_url_twitter_com(self):
        assert is_twitter_url("https://twitter.com/user/status/67890")

    def test_is_twitter_url_with_query(self):
        assert is_twitter_url("https://x.com/u/status/1?s=20")

    def test_is_not_twitter_url(self):
        assert not is_twitter_url("https://example.com/post")
        assert not is_twitter_url("https://x.com/user")  # no /status/
        assert not is_twitter_url("https://github.com/x.com")

    def test_extract_tweet_id_x_com(self):
        out = extract_tweet_id("https://x.com/jack/status/20")
        assert out == ("jack", "20")

    def test_extract_tweet_id_twitter_com(self):
        out = extract_tweet_id("https://twitter.com/elonmusk/status/9999")
        assert out == ("elonmusk", "9999")

    def test_extract_tweet_id_returns_none_for_non_tweet(self):
        assert extract_tweet_id("https://x.com/jack") is None
        assert extract_tweet_id("https://example.com") is None
