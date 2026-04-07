"""Unit tests for the new remote handlers added in 2026-04-07.

Covers exec (cwd_override / env / truncated flags), read_file (offset /
limit / encoding), stat, mkdir, delete, move, copy, glob, grep.

Each handler is invoked directly with a synthetic message dict so we
don't need a real WebSocket. ``tmp_path`` provides a sandboxed
workspace; the handlers' path-traversal guard makes that the agent's
``cwd``.
"""

from __future__ import annotations

import asyncio
import base64
import sys

import pytest

import main


REQ_ID = "test-req-1"


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────
# handle_exec — cwd_override / env / truncated
# ──────────────────────────────────────────────


class TestExecCwdOverride:
    def test_cwd_override_runs_in_subdirectory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "marker.txt").write_text("hi")

        # Use a portable command: list current directory contents.
        cmd = "dir /b" if sys.platform == "win32" else "ls"
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": cmd,
            "cwd": str(tmp_path),
            "cwd_override": "sub",
            "timeout": 10,
        }))

        assert result["exit_code"] == 0, result
        assert "marker.txt" in result["stdout"]

    def test_cwd_override_traversal_rejected(self, tmp_path):
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": "echo hello",
            "cwd": str(tmp_path),
            "cwd_override": "../../../etc",
            "timeout": 10,
        }))
        assert result["exit_code"] == -1
        assert "Path traversal" in result["stderr"] or "Invalid cwd_override" in result["stderr"]

    def test_cwd_override_nonexistent_dir(self, tmp_path):
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": "echo x",
            "cwd": str(tmp_path),
            "cwd_override": "does-not-exist",
            "timeout": 10,
        }))
        assert result["exit_code"] == -1


class TestExecEnv:
    def test_env_variable_visible_to_command(self, tmp_path):
        # Use a portable env-print command
        cmd = "echo %TEST_VAR%" if sys.platform == "win32" else "echo $TEST_VAR"
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": cmd,
            "cwd": str(tmp_path),
            "env": {"TEST_VAR": "hello123"},
            "timeout": 10,
        }))
        assert result["exit_code"] == 0
        assert "hello123" in result["stdout"]

    def test_env_must_be_dict(self, tmp_path):
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": "echo x",
            "cwd": str(tmp_path),
            "env": ["not", "a", "dict"],
            "timeout": 10,
        }))
        assert result["exit_code"] == -1
        assert "env" in result["stderr"]


class TestExecTruncatedFlags:
    def test_normal_output_not_truncated(self, tmp_path):
        result = _run(main.handle_exec({
            "request_id": REQ_ID,
            "command": "echo small output",
            "cwd": str(tmp_path),
            "timeout": 10,
        }))
        assert result["stdout_truncated"] is False
        assert result["stderr_truncated"] is False
        assert result["stdout_total_bytes"] > 0
        assert result["stderr_total_bytes"] == 0

    def test_truncate_with_flag_helper(self):
        big = b"x" * (main.MAX_OUTPUT_BYTES + 100)
        text, truncated, total = main._truncate_with_flag(big, main.MAX_OUTPUT_BYTES)
        assert truncated is True
        assert total == main.MAX_OUTPUT_BYTES + 100
        assert len(text.encode("utf-8")) == main.MAX_OUTPUT_BYTES


# ──────────────────────────────────────────────
# handle_read_file — offset/limit/encoding
# ──────────────────────────────────────────────


class TestReadFileOffsetLimit:
    def test_full_read_when_no_offset_limit(self, tmp_path):
        target = tmp_path / "lines.txt"
        target.write_text("line1\nline2\nline3\n")

        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "lines.txt",
            "cwd": str(tmp_path),
        }))
        assert "error" not in result
        assert result["content"] == "line1\nline2\nline3\n"
        assert result["total_lines"] == 3
        assert result["truncated"] is False
        assert result["is_binary"] is False

    def test_offset_only(self, tmp_path):
        target = tmp_path / "lines.txt"
        target.write_text("a\nb\nc\nd\ne\n")
        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "lines.txt",
            "cwd": str(tmp_path),
            "offset": 3,
        }))
        assert result["content"] == "c\nd\ne\n"
        assert result["total_lines"] == 5
        assert result["truncated"] is False

    def test_offset_and_limit(self, tmp_path):
        target = tmp_path / "lines.txt"
        target.write_text("a\nb\nc\nd\ne\n")
        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "lines.txt",
            "cwd": str(tmp_path),
            "offset": 2,
            "limit": 2,
        }))
        assert result["content"] == "b\nc\n"
        assert result["total_lines"] == 5
        assert result["truncated"] is True  # 2 < 5

    def test_limit_zero_returns_empty_slice(self, tmp_path):
        target = tmp_path / "lines.txt"
        target.write_text("a\nb\n")
        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "lines.txt",
            "cwd": str(tmp_path),
            "offset": 1,
            "limit": 0,
        }))
        assert result["content"] == ""


class TestReadFileBinary:
    def test_binary_encoding_returns_base64(self, tmp_path):
        target = tmp_path / "blob.bin"
        target.write_bytes(b"\x00\x01\x02\xff")
        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "blob.bin",
            "cwd": str(tmp_path),
            "encoding": "binary",
        }))
        assert result["is_binary"] is True
        assert result["encoding"] == "base64"
        assert base64.b64decode(result["content"]) == b"\x00\x01\x02\xff"

    def test_base64_encoding_alias(self, tmp_path):
        target = tmp_path / "blob.bin"
        target.write_bytes(b"hello")
        result = _run(main.handle_read_file({
            "request_id": REQ_ID,
            "path": "blob.bin",
            "cwd": str(tmp_path),
            "encoding": "base64",
        }))
        assert base64.b64decode(result["content"]) == b"hello"


# ──────────────────────────────────────────────
# handle_stat
# ──────────────────────────────────────────────


class TestStat:
    def test_stat_existing_file(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("hello")
        result = _run(main.handle_stat({
            "request_id": REQ_ID,
            "path": "f.txt",
            "cwd": str(tmp_path),
        }))
        assert result["exists"] is True
        assert result["type"] == "file"
        assert result["size"] == 5
        assert "mtime" in result

    def test_stat_existing_directory(self, tmp_path):
        sub = tmp_path / "d"
        sub.mkdir()
        result = _run(main.handle_stat({
            "request_id": REQ_ID,
            "path": "d",
            "cwd": str(tmp_path),
        }))
        assert result["exists"] is True
        assert result["type"] == "directory"

    def test_stat_nonexistent(self, tmp_path):
        result = _run(main.handle_stat({
            "request_id": REQ_ID,
            "path": "nope.txt",
            "cwd": str(tmp_path),
        }))
        assert result["exists"] is False
        assert result["type"] is None


# ──────────────────────────────────────────────
# handle_mkdir
# ──────────────────────────────────────────────


class TestMkdir:
    def test_mkdir_creates_directory(self, tmp_path):
        result = _run(main.handle_mkdir({
            "request_id": REQ_ID,
            "path": "newdir",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert (tmp_path / "newdir").is_dir()

    def test_mkdir_with_parents(self, tmp_path):
        result = _run(main.handle_mkdir({
            "request_id": REQ_ID,
            "path": "a/b/c",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert (tmp_path / "a" / "b" / "c").is_dir()

    def test_mkdir_existing_with_parents_succeeds(self, tmp_path):
        (tmp_path / "exists").mkdir()
        result = _run(main.handle_mkdir({
            "request_id": REQ_ID,
            "path": "exists",
            "cwd": str(tmp_path),
            "parents": True,
        }))
        assert result["success"] is True

    def test_mkdir_traversal_rejected(self, tmp_path):
        result = _run(main.handle_mkdir({
            "request_id": REQ_ID,
            "path": "../../../tmp/evil",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is False


# ──────────────────────────────────────────────
# handle_delete
# ──────────────────────────────────────────────


class TestDelete:
    def test_delete_file(self, tmp_path):
        target = tmp_path / "f.txt"
        target.write_text("x")
        result = _run(main.handle_delete({
            "request_id": REQ_ID,
            "path": "f.txt",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert not target.exists()

    def test_delete_directory_requires_recursive(self, tmp_path):
        sub = tmp_path / "d"
        sub.mkdir()
        result = _run(main.handle_delete({
            "request_id": REQ_ID,
            "path": "d",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is False
        assert sub.exists()

    def test_delete_directory_recursive(self, tmp_path):
        sub = tmp_path / "d"
        sub.mkdir()
        (sub / "f.txt").write_text("x")
        result = _run(main.handle_delete({
            "request_id": REQ_ID,
            "path": "d",
            "cwd": str(tmp_path),
            "recursive": True,
        }))
        assert result["success"] is True
        assert not sub.exists()

    def test_delete_workspace_root_refused(self, tmp_path):
        result = _run(main.handle_delete({
            "request_id": REQ_ID,
            "path": ".",
            "cwd": str(tmp_path),
            "recursive": True,
        }))
        assert result["success"] is False
        assert "workspace root" in result["error"]


# ──────────────────────────────────────────────
# handle_move / handle_copy
# ──────────────────────────────────────────────


class TestMove:
    def test_move_file(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hi")
        result = _run(main.handle_move({
            "request_id": REQ_ID,
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert not src.exists()
        assert (tmp_path / "b.txt").read_text() == "hi"

    def test_move_overwrite_required(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = _run(main.handle_move({
            "request_id": REQ_ID,
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is False

    def test_move_overwrite_true(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        result = _run(main.handle_move({
            "request_id": REQ_ID,
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": str(tmp_path),
            "overwrite": True,
        }))
        assert result["success"] is True
        assert (tmp_path / "b.txt").read_text() == "a"


class TestCopy:
    def test_copy_file(self, tmp_path):
        src = tmp_path / "a.txt"
        src.write_text("hi")
        result = _run(main.handle_copy({
            "request_id": REQ_ID,
            "src": "a.txt",
            "dst": "b.txt",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert src.exists()
        assert (tmp_path / "b.txt").read_text() == "hi"

    def test_copy_directory(self, tmp_path):
        src = tmp_path / "d"
        src.mkdir()
        (src / "f.txt").write_text("x")
        result = _run(main.handle_copy({
            "request_id": REQ_ID,
            "src": "d",
            "dst": "d2",
            "cwd": str(tmp_path),
        }))
        assert result["success"] is True
        assert (tmp_path / "d2" / "f.txt").read_text() == "x"


# ──────────────────────────────────────────────
# handle_glob
# ──────────────────────────────────────────────


class TestGlob:
    def test_glob_recursive(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.py").write_text("y")
        (tmp_path / "sub" / "c.txt").write_text("z")
        result = _run(main.handle_glob({
            "request_id": REQ_ID,
            "pattern": "**/*.py",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "matches" in result
        paths = sorted(m["path"] for m in result["matches"])
        # Both .py files should be found, .txt skipped
        assert len(paths) == 2
        assert all(p.endswith(".py") for p in paths)

    def test_glob_no_matches(self, tmp_path):
        result = _run(main.handle_glob({
            "request_id": REQ_ID,
            "pattern": "*.nonexistent",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert result["matches"] == []
        assert result["count"] == 0

    def test_glob_pattern_required(self, tmp_path):
        result = _run(main.handle_glob({
            "request_id": REQ_ID,
            "pattern": "",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "error" in result


# ──────────────────────────────────────────────
# handle_grep
# ──────────────────────────────────────────────


class TestGrep:
    def test_grep_basic_match(self, tmp_path):
        (tmp_path / "f.txt").write_text("hello world\nfoo bar\nbar baz\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "bar",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert result["count"] == 2
        lines = sorted(m["line"] for m in result["matches"])
        assert lines == [2, 3]

    def test_grep_glob_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("import foo")
        (tmp_path / "b.txt").write_text("import foo")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "import",
            "path": ".",
            "cwd": str(tmp_path),
            "glob": "*.py",
        }))
        assert result["count"] == 1
        assert result["matches"][0]["file"].endswith("a.py")

    def test_grep_case_insensitive(self, tmp_path):
        (tmp_path / "f.txt").write_text("HELLO\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "hello",
            "path": ".",
            "cwd": str(tmp_path),
            "case_insensitive": True,
        }))
        assert result["count"] == 1

    def test_grep_invalid_regex(self, tmp_path):
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "[invalid",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "error" in result

    def test_grep_max_results_truncates(self, tmp_path):
        (tmp_path / "f.txt").write_text("\n".join(["match"] * 50) + "\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "match",
            "path": ".",
            "cwd": str(tmp_path),
            "max_results": 10,
        }))
        assert result["count"] == 10
        assert result["truncated"] is True

    def test_grep_skips_vendored_dirs(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "secret.txt").write_text("password")
        (tmp_path / "f.txt").write_text("password")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "password",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        # Only the top-level file should match
        assert result["count"] == 1
        assert ".git" not in result["matches"][0]["file"]
