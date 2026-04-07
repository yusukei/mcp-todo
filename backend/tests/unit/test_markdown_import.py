"""Unit tests for the Markdown import frontmatter parser."""

from app.services.markdown_import import parse_markdown_file


class TestParseMarkdownFile:
    def test_no_frontmatter_uses_filename(self):
        result = parse_markdown_file("design.md", "# Hello\n\nbody")
        assert result.title == "design"
        assert result.content == "# Hello\n\nbody"
        assert result.tags == []
        assert result.category is None

    def test_strips_md_and_markdown_extensions(self):
        assert parse_markdown_file("foo.md", "x").title == "foo"
        assert parse_markdown_file("bar.markdown", "x").title == "bar"
        assert parse_markdown_file("baz.MD", "x").title == "baz"

    def test_empty_filename_falls_back_to_untitled(self):
        result = parse_markdown_file(".md", "x")
        assert result.title == "Untitled"

    def test_path_traversal_in_filename_is_sanitized(self):
        result = parse_markdown_file("../../etc/passwd.md", "x")
        assert result.title == "passwd"

    def test_inline_list_tags(self):
        text = "---\ntitle: Auth\ntags: [auth, security]\n---\n\nbody"
        result = parse_markdown_file("ignored.md", text)
        assert result.title == "Auth"
        assert result.tags == ["auth", "security"]
        assert result.content == "body"

    def test_block_list_tags(self):
        text = "---\ntitle: Auth\ntags:\n  - auth\n  - security\n---\n\nbody"
        result = parse_markdown_file("ignored.md", text)
        assert result.tags == ["auth", "security"]

    def test_comma_string_tags(self):
        text = "---\ntags: a, b, c\n---\nbody"
        result = parse_markdown_file("x.md", text)
        assert result.tags == ["a", "b", "c"]

    def test_quoted_title_value(self):
        text = '---\ntitle: "My: Document"\n---\nbody'
        result = parse_markdown_file("x.md", text)
        assert result.title == "My: Document"

    def test_category_extracted(self):
        text = "---\ncategory: design\n---\nbody"
        result = parse_markdown_file("x.md", text)
        assert result.category == "design"

    def test_missing_close_marker_falls_back_to_full_body(self):
        text = "---\ntitle: Foo\n\nbody without close"
        result = parse_markdown_file("name.md", text)
        # No closing fence → entire input is treated as content
        assert result.title == "name"
        assert result.content == text

    def test_blank_line_in_frontmatter_skipped(self):
        text = "---\ntitle: Foo\n\ncategory: spec\n---\nbody"
        result = parse_markdown_file("x.md", text)
        assert result.title == "Foo"
        assert result.category == "spec"

    def test_frontmatter_title_overrides_filename(self):
        result = parse_markdown_file(
            "fallback.md", "---\ntitle: Real Title\n---\ncontent"
        )
        assert result.title == "Real Title"

    def test_tags_lowercased(self):
        text = "---\ntags: [Auth, SECURITY]\n---\nbody"
        result = parse_markdown_file("x.md", text)
        assert result.tags == ["auth", "security"]

    def test_body_preserves_internal_blank_lines(self):
        text = "---\ntitle: T\n---\n\npara1\n\npara2"
        result = parse_markdown_file("x.md", text)
        assert "para1\n\npara2" in result.content

    def test_crlf_line_endings(self):
        text = "---\r\ntitle: Win\r\ntags: [a]\r\n---\r\nbody line"
        result = parse_markdown_file("x.md", text)
        assert result.title == "Win"
        assert result.tags == ["a"]
        assert "body line" in result.content
