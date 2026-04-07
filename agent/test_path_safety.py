"""Tests for path traversal protection in agent file handlers."""

import asyncio
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from main import (
    _resolve_safe_path,
    handle_list_dir,
    handle_read_file,
    handle_write_file,
)


# ── _resolve_safe_path unit tests ────────────────────────────


class TestResolveSafePath:
    def test_relative_path_inside_cwd(self, tmp_path):
        (tmp_path / "file.txt").write_text("ok")
        resolved = _resolve_safe_path("file.txt", str(tmp_path))
        assert resolved == os.path.realpath(tmp_path / "file.txt")

    def test_nested_relative_path(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "f.txt").write_text("ok")
        resolved = _resolve_safe_path("sub/f.txt", str(tmp_path))
        assert resolved == os.path.realpath(sub / "f.txt")

    def test_absolute_path_inside_cwd(self, tmp_path):
        target = tmp_path / "abs.txt"
        target.write_text("ok")
        resolved = _resolve_safe_path(str(target), str(tmp_path))
        assert resolved == os.path.realpath(target)

    def test_dot_path_resolves_to_cwd(self, tmp_path):
        resolved = _resolve_safe_path(".", str(tmp_path))
        assert resolved == os.path.realpath(tmp_path)

    def test_dotdot_traversal_rejected(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        with pytest.raises(ValueError, match="traversal"):
            _resolve_safe_path("../outside.txt", str(sub))

    def test_deep_dotdot_traversal_rejected(self, tmp_path):
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        with pytest.raises(ValueError, match="traversal"):
            _resolve_safe_path("../../../etc/passwd", str(sub))

    def test_absolute_outside_cwd_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="traversal"):
            if sys.platform == "win32":
                _resolve_safe_path(r"C:\Windows\System32\drivers\etc\hosts", str(tmp_path))
            else:
                _resolve_safe_path("/etc/passwd", str(tmp_path))

    def test_missing_cwd_rejected(self):
        with pytest.raises(ValueError, match="cwd is required"):
            _resolve_safe_path("foo.txt", None)

    def test_empty_cwd_rejected(self):
        with pytest.raises(ValueError, match="cwd is required"):
            _resolve_safe_path("foo.txt", "")

    def test_nonexistent_cwd_rejected(self, tmp_path):
        bogus = str(tmp_path / "does-not-exist")
        with pytest.raises(ValueError, match="does not exist"):
            _resolve_safe_path("foo.txt", bogus)

    def test_symlink_escape_rejected(self, tmp_path):
        # Symlink inside cwd that points outside should be rejected by realpath check
        if sys.platform == "win32":
            pytest.skip("symlink creation needs admin on Windows")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        link = cwd / "escape"
        os.symlink(outside, link)
        with pytest.raises(ValueError, match="traversal"):
            _resolve_safe_path("escape/secret.txt", str(cwd))


# ── Handler integration tests ────────────────────────────────


@pytest.mark.asyncio
class TestHandleReadFile:
    async def test_reads_file_inside_cwd(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("hello world")
        resp = await handle_read_file({"request_id": "r1", "path": "hello.txt", "cwd": str(tmp_path)})
        assert resp["type"] == "file_content"
        assert resp.get("content") == "hello world"
        assert "error" not in resp

    async def test_blocks_absolute_outside(self, tmp_path):
        target = "/etc/passwd" if sys.platform != "win32" else r"C:\Windows\System32\drivers\etc\hosts"
        resp = await handle_read_file({"request_id": "r2", "path": target, "cwd": str(tmp_path)})
        assert "error" in resp
        assert "traversal" in resp["error"].lower()

    async def test_blocks_dotdot(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "secret.txt").write_text("nope")
        resp = await handle_read_file({"request_id": "r3", "path": "../secret.txt", "cwd": str(sub)})
        assert "error" in resp
        assert "traversal" in resp["error"].lower()

    async def test_missing_cwd_rejected(self):
        resp = await handle_read_file({"request_id": "r4", "path": "anything"})
        assert "error" in resp
        assert "cwd" in resp["error"].lower()


@pytest.mark.asyncio
class TestHandleWriteFile:
    async def test_writes_inside_cwd(self, tmp_path):
        resp = await handle_write_file({
            "request_id": "w1", "path": "out.txt", "cwd": str(tmp_path), "content": "data",
        })
        assert resp.get("success") is True
        assert (tmp_path / "out.txt").read_text() == "data"

    async def test_blocks_absolute_outside(self, tmp_path):
        target = "/tmp/escaped.txt" if sys.platform != "win32" else r"C:\escaped.txt"
        resp = await handle_write_file({
            "request_id": "w2", "path": target, "cwd": str(tmp_path), "content": "x",
        })
        assert resp.get("success") is False
        assert "traversal" in resp["error"].lower()
        assert not os.path.exists(target)

    async def test_blocks_dotdot(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        resp = await handle_write_file({
            "request_id": "w3", "path": "../escape.txt", "cwd": str(sub), "content": "x",
        })
        assert resp.get("success") is False
        assert "traversal" in resp["error"].lower()
        assert not (tmp_path / "escape.txt").exists()

    async def test_missing_cwd_rejected(self):
        resp = await handle_write_file({"request_id": "w4", "path": "x", "content": "y"})
        assert resp.get("success") is False
        assert "cwd" in resp["error"].lower()


@pytest.mark.asyncio
class TestHandleListDir:
    async def test_lists_inside_cwd(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        resp = await handle_list_dir({"request_id": "l1", "path": ".", "cwd": str(tmp_path)})
        assert resp["type"] == "dir_listing"
        names = {e["name"] for e in resp["entries"]}
        assert names == {"a.txt", "b.txt"}

    async def test_blocks_absolute_outside(self, tmp_path):
        target = "/etc" if sys.platform != "win32" else r"C:\Windows"
        resp = await handle_list_dir({"request_id": "l2", "path": target, "cwd": str(tmp_path)})
        assert "error" in resp
        assert "traversal" in resp["error"].lower()

    async def test_blocks_dotdot(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        resp = await handle_list_dir({"request_id": "l3", "path": "..", "cwd": str(sub)})
        assert "error" in resp
        assert "traversal" in resp["error"].lower()

    async def test_missing_cwd_rejected(self):
        resp = await handle_list_dir({"request_id": "l4", "path": "."})
        assert "error" in resp
        assert "cwd" in resp["error"].lower()
