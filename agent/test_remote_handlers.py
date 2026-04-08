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
import json
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
        # Handlers now return ONLY the inner payload (the dispatcher
        # wraps it in {"type": ..., "request_id": ..., "payload": ...}).
        # An inner ``type`` key is therefore safe again — it can no
        # longer shadow the envelope.
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
        assert result["type"] == "file"
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
        assert result["type"] == "directory"
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
# handle_grep — ripgrep is REQUIRED. There is no Python fallback.
# Tests that need to exercise the actual ripgrep binary are guarded by
# @pytest.mark.skipif(not _HAS_RG); the rest mock create_subprocess_exec
# so they run in any environment.
# ──────────────────────────────────────────────


import shutil as _shutil
_HAS_RG = _shutil.which("rg") is not None


class TestGrepRequiresRipgrep:
    """When ripgrep is unavailable, handle_grep must surface a clear error."""

    def test_rg_missing_returns_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", None)
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "error" in result
        assert "ripgrep" in result["error"].lower()

    def test_rg_missing_error_includes_install_hint(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", None)
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        # Error message must point operators at how to fix the problem
        assert "install" in result["error"].lower()


class TestGrepValidation:
    """Argument validation is independent of ripgrep — exercised via mock."""

    def _mock_rg(self, monkeypatch):
        class _FakeProc:
            returncode = 0
            pid = 12345
            async def communicate(self):
                return (b"", b"")
        async def _fake(*args, **kwargs):
            return _FakeProc()
        monkeypatch.setattr(main, "RG_PATH", "rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake)

    def test_empty_pattern_rejected(self, tmp_path, monkeypatch):
        self._mock_rg(monkeypatch)
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "", "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "error" in result
        assert "pattern" in result["error"].lower()

    def test_max_results_non_integer_rejected(self, tmp_path, monkeypatch):
        self._mock_rg(monkeypatch)
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "x", "path": ".",
            "cwd": str(tmp_path), "max_results": "abc",
        }))
        assert "error" in result
        assert "integer" in result["error"]

    def test_nonexistent_base_dir_rejected(self, tmp_path, monkeypatch):
        self._mock_rg(monkeypatch)
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "x",
            "path": "does-not-exist", "cwd": str(tmp_path),
        }))
        assert "error" in result

    def test_path_traversal_rejected(self, tmp_path, monkeypatch):
        self._mock_rg(monkeypatch)
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "x",
            "path": "../../etc", "cwd": str(tmp_path),
        }))
        assert "error" in result


@pytest.mark.skipif(not _HAS_RG, reason="ripgrep not installed")
class TestGrepWithRipgrep:
    """End-to-end tests against the real ripgrep binary."""

    def test_rg_basic_match(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "f.txt").write_text("alpha\nbeta needle gamma\ndelta\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert result["engine"] == "ripgrep"
        assert result["count"] == 1
        assert result["matches"][0]["line"] == 2
        assert "needle" in result["matches"][0]["text"]

    def test_rg_glob_filter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "a.py").write_text("import needle")
        (tmp_path / "b.txt").write_text("import needle")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
            "glob": "*.py",
        }))
        assert result["count"] == 1
        assert result["matches"][0]["file"].endswith("a.py")

    def test_rg_case_insensitive(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "f.txt").write_text("NEEDLE\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
            "case_insensitive": True,
        }))
        assert result["count"] == 1

    def test_rg_max_results_truncates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "f.txt").write_text("\n".join(["needle"] * 50) + "\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
            "max_results": 10,
        }))
        assert result["count"] == 10
        assert result["truncated"] is True

    def test_rg_invalid_regex_raises_ripgrep_error(self, tmp_path, monkeypatch):
        """Invalid regex must surface as a clear error from ripgrep."""
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "f.txt").write_text("anything")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "[invalid",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert "error" in result

    def test_rg_respects_gitignore_when_requested(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / ".gitignore").write_text("ignored.txt\n")
        (tmp_path / "ignored.txt").write_text("needle\n")
        (tmp_path / "kept.txt").write_text("needle\n")

        # respect_gitignore=False (default) → both files matched
        r1 = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "needle",
            "path": ".", "cwd": str(tmp_path),
            "respect_gitignore": False,
        }))
        assert r1["count"] == 2

        # respect_gitignore=True → only kept.txt
        r2 = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "needle",
            "path": ".", "cwd": str(tmp_path),
            "respect_gitignore": True,
        }))
        assert r2["count"] == 1
        assert r2["matches"][0]["file"].endswith("kept.txt")

    def test_rg_invalid_utf8_via_bytes_field(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "RG_PATH", _shutil.which("rg"))
        (tmp_path / "f.txt").write_bytes(b"needle \xff\xfe rest\n")
        result = _run(main.handle_grep({
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
        }))
        assert result["count"] == 1
        assert "needle" in result["matches"][0]["text"]


class TestGrepRgCommandLine:
    """Verify the command line passed to ripgrep.

    These tests work without a real ripgrep binary by mocking
    ``asyncio.create_subprocess_exec`` and capturing the argv. They
    catch regressions like "we forgot to pass --no-ignore" or "we
    forgot to exclude .venv when --no-ignore is set".
    """

    def _run_with_capture(self, monkeypatch, tmp_path, **grep_kwargs):
        """Run handle_grep with a fake rg subprocess and return the captured argv."""
        captured = {}

        class _FakeProc:
            returncode = 0
            pid = 12345
            async def communicate(self):
                # Empty stdout/stderr → no matches
                return (b"", b"")

        async def _fake_create(*args, **kwargs):
            captured["argv"] = list(args)
            return _FakeProc()

        monkeypatch.setattr(main, "RG_PATH", "rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake_create)
        # Touch a file so base_dir exists
        (tmp_path / "f.txt").write_text("x")

        msg = {
            "request_id": REQ_ID,
            "pattern": "needle",
            "path": ".",
            "cwd": str(tmp_path),
            **grep_kwargs,
        }
        result = _run(main.handle_grep(msg))
        return captured.get("argv"), result

    def test_no_ignore_added_by_default(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(monkeypatch, tmp_path)
        assert "--no-ignore" in argv

    def test_no_ignore_omitted_when_respecting_gitignore(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(
            monkeypatch, tmp_path, respect_gitignore=True
        )
        assert "--no-ignore" not in argv

    def test_skip_dirs_excluded_when_no_ignore(self, tmp_path, monkeypatch):
        """When --no-ignore is set, our heavy-dir glob filters MUST be passed.

        This is the regression that caused remote_grep to time out on the
        mcp-todo project: rg without these globs walks .venv / node_modules /
        .git and never finishes on real-world repos.
        """
        argv, _ = self._run_with_capture(monkeypatch, tmp_path)
        # Spot-check the most painful directories
        for skip in (".git", ".venv", "node_modules", "__pycache__",
                     "dist", "build", ".next", "target"):
            assert f"!{skip}" in argv, f"missing top-level skip glob for {skip}"
            assert f"!**/{skip}/**" in argv, f"missing recursive skip glob for {skip}"
        # And every entry in GREP_SKIP_DIRS should be present
        for skip in main.GREP_SKIP_DIRS:
            assert f"!{skip}" in argv

    def test_skip_dirs_NOT_added_when_respecting_gitignore(self, tmp_path, monkeypatch):
        """When the user opts into gitignore, we let rg do its own thing
        and don't pile on extra exclusions."""
        argv, _ = self._run_with_capture(
            monkeypatch, tmp_path, respect_gitignore=True
        )
        # No skip-dir globs should appear
        assert "!.venv" not in argv
        assert "!node_modules" not in argv

    def test_max_count_and_filesize_passed(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(monkeypatch, tmp_path, max_results=42)
        assert "--max-count" in argv
        assert "42" in argv
        assert "--max-filesize" in argv
        assert "10M" in argv

    def test_glob_filter_passed_through(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(monkeypatch, tmp_path, glob="*.py")
        # The user's glob should be present (alongside our skip globs)
        assert "*.py" in argv

    def test_case_insensitive_flag(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(
            monkeypatch, tmp_path, case_insensitive=True
        )
        assert "-i" in argv

    def test_pattern_passed_with_dash_e(self, tmp_path, monkeypatch):
        argv, _ = self._run_with_capture(monkeypatch, tmp_path)
        # -e <pattern> -- <base_dir>
        e_idx = argv.index("-e")
        assert argv[e_idx + 1] == "needle"


class TestGrepRgErrorSurfacing:
    """Verify ripgrep failures surface as structured errors, not as silent fallback."""

    def test_nonzero_exit_returns_error(self, tmp_path, monkeypatch):
        class _FakeProc:
            returncode = 2  # ripgrep exit code 2 = error
            pid = 12345
            async def communicate(self):
                return (b"", b"regex parse error: unclosed group")
        async def _fake(*args, **kwargs):
            return _FakeProc()
        monkeypatch.setattr(main, "RG_PATH", "rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake)
        (tmp_path / "f.txt").write_text("x")
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "needle",
            "path": ".", "cwd": str(tmp_path),
        }))
        assert "error" in result
        assert "ripgrep exited 2" in result["error"]
        assert "regex parse error" in result["error"]

    def test_launch_failure_returns_error(self, tmp_path, monkeypatch):
        async def _fake_create(*args, **kwargs):
            raise FileNotFoundError(2, "No such file or directory: 'rg'")
        monkeypatch.setattr(main, "RG_PATH", "/nonexistent/rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake_create)
        (tmp_path / "f.txt").write_text("x")
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "needle",
            "path": ".", "cwd": str(tmp_path),
        }))
        assert "error" in result
        assert "failed to launch ripgrep" in result["error"].lower()

    def test_timeout_returns_error(self, tmp_path, monkeypatch):
        class _HangingProc:
            returncode = None
            killed = False
            pid = 12345
            async def communicate(self):
                # Simulate hang by waiting forever; wait_for will cancel us.
                await asyncio.sleep(3600)
                return (b"", b"")
            def kill(self):
                self.__class__.killed = True
        async def _fake(*args, **kwargs):
            return _HangingProc()
        # Patch wait_for to raise TimeoutError immediately so the test is fast.
        async def _fake_wait_for(coro, timeout):
            # Cancel the coroutine to clean up
            task = asyncio.ensure_future(coro)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass
            raise asyncio.TimeoutError()
        monkeypatch.setattr(main, "RG_PATH", "rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake)
        monkeypatch.setattr(main.asyncio, "wait_for", _fake_wait_for)
        (tmp_path / "f.txt").write_text("x")
        result = _run(main.handle_grep({
            "request_id": REQ_ID, "pattern": "needle",
            "path": ".", "cwd": str(tmp_path),
        }))
        assert "error" in result
        assert "timed out" in result["error"].lower()
        assert _HangingProc.killed is True

    def test_malformed_json_propagates_as_handler_error(self, tmp_path, monkeypatch):
        """Malformed JSON from rg is a bug — it must NOT be silently skipped."""
        class _FakeProc:
            returncode = 0
            pid = 12345
            async def communicate(self):
                return (b"this is not json\n", b"")
        async def _fake(*args, **kwargs):
            return _FakeProc()
        monkeypatch.setattr(main, "RG_PATH", "rg")
        monkeypatch.setattr(main.asyncio, "create_subprocess_exec", _fake)
        (tmp_path / "f.txt").write_text("x")
        # JSONDecodeError must propagate from _grep_with_rg. handle_grep
        # does NOT catch it (only RipgrepError), so it bubbles up to the
        # caller — the test runner sees a real exception.
        with pytest.raises(__import__("json").JSONDecodeError):
            _run(main.handle_grep({
                "request_id": REQ_ID, "pattern": "needle",
                "path": ".", "cwd": str(tmp_path),
            }))


# ──────────────────────────────────────────────
# Envelope contract — every dispatcher round-trip MUST produce the
# right envelope ``type``, propagate ``request_id`` unchanged, and nest
# the handler's data under ``payload``.
#
# Background (2026-04-08): the original ``handle_stat`` / ``handle_delete``
# returned an inner dict containing ``"type"``, which spread on top of
# ``{"type": "stat_result", **result}`` and silently shadowed the envelope.
# The dispatcher dropped the response and the caller's Future hung until
# the MCP layer's 60s timeout. The fix nests handler data under a
# ``payload`` key so envelope fields can never be shadowed — this test
# is the regression net that says "for every handler, dispatcher round-trip
# produces an envelope where type / request_id are intact".
#
# When you add a new handler:
#   1. Register it in ``main._HANDLERS`` and ``main._RESPONSE_TYPE_FOR``.
#   2. Add a happy-path case to ``_build_contract_cases`` below.
#   3. The parametrized test will drive the dispatcher with a synthetic
#      inbound envelope and assert the OUTBOUND envelope shape:
#        - ``type`` matches the expected ``*_result``
#        - ``request_id`` matches the sentinel
#        - ``payload`` is a dict with the handler's data
# ──────────────────────────────────────────────


_CONTRACT_REQ_ID = "envelope-contract-sentinel-9f3a"


def _build_contract_cases(tmp_path):
    """Return a list of (label, msg_type, payload, expected_envelope_type).

    Each case sets up just enough state in ``tmp_path`` to make the handler
    take its happy path. We build the cases inside a function (rather than
    at module scope) so each case gets a clean filesystem and the side
    effects of one handler can't interfere with another.
    """
    # Sandbox seed
    (tmp_path / "f.txt").write_text("hello world")
    (tmp_path / "to_delete.txt").write_text("bye")
    (tmp_path / "to_move.txt").write_text("mv")
    (tmp_path / "to_copy.txt").write_text("cp")
    (tmp_path / "src_dir").mkdir()
    (tmp_path / "src_dir" / "a.py").write_text("import os")

    cwd = str(tmp_path)
    echo_cmd = "echo hi"  # portable on cmd.exe and POSIX shells alike

    return [
        ("exec",       "exec",       {"command": echo_cmd, "cwd": cwd, "timeout": 10},                "exec_result"),
        ("read_file",  "read_file",  {"path": "f.txt", "cwd": cwd},                                    "file_content"),
        ("write_file", "write_file", {"path": "new.txt", "content": "x", "cwd": cwd},                  "write_result"),
        ("list_dir",   "list_dir",   {"path": ".", "cwd": cwd},                                        "dir_listing"),
        ("stat (file)",     "stat",  {"path": "f.txt", "cwd": cwd},                                    "stat_result"),
        ("stat (missing)",  "stat",  {"path": "nope.txt", "cwd": cwd},                                 "stat_result"),
        ("mkdir",      "mkdir",      {"path": "newdir", "cwd": cwd},                                   "mkdir_result"),
        ("delete (file)", "delete",  {"path": "to_delete.txt", "cwd": cwd},                            "delete_result"),
        ("delete (dir)",  "delete",  {"path": "src_dir", "cwd": cwd, "recursive": True},               "delete_result"),
        ("move",       "move",       {"src": "to_move.txt", "dst": "moved.txt", "cwd": cwd},           "move_result"),
        ("copy",       "copy",       {"src": "to_copy.txt", "dst": "copied.txt", "cwd": cwd},          "copy_result"),
        ("glob",       "glob",       {"pattern": "**/*.txt", "path": ".", "cwd": cwd},                 "glob_result"),
        # handle_grep happy path needs ripgrep mocked — exercise the
        # error path instead (which still goes through the envelope wrap).
        ("grep (missing rg)", "grep", {"pattern": "x", "path": ".", "cwd": cwd},                       "grep_result"),
    ]


class _CapturingAgent:
    """Minimal stand-in for ``WorkspaceAgent`` that captures sent frames.

    We can't use the real class without a WebSocket; this stub records
    every ``_safe_send`` payload into ``self.sent`` so the contract test
    can inspect the OUTBOUND envelope after running ``_run_handler``.
    """

    def __init__(self):
        self.sent: list[str] = []

    async def _safe_send(self, data: str) -> None:
        self.sent.append(data)

    # Borrow the real method directly — it only touches self._safe_send
    # and the module-level _RESPONSE_TYPE_FOR / json / logger, which all
    # work fine when called as a bound method on this stub.
    _run_handler = main.WorkspaceAgent._run_handler


class TestEnvelopeContract:
    """Every dispatcher round-trip must produce a well-formed envelope.

    Drives ``_run_handler`` (the agent's outbound dispatcher) directly
    with a synthetic inbound payload and asserts the captured outbound
    frame:
      - ``type`` is the expected ``*_result``
      - ``request_id`` is the sentinel
      - ``payload`` is a dict containing the handler's data
      - no envelope key is shadowed by inner data

    This catches the 2026-04-08 shadowing-bug class statically: any
    future handler that breaks the envelope contract fails this test
    instead of letting the MCP layer time out 60s later.
    """

    def test_every_handler_emits_correct_envelope(self, tmp_path, monkeypatch):
        # Force handle_grep down its "ripgrep missing" branch so we don't
        # need a real binary to validate the envelope wrap. The error
        # path still goes through the same dispatcher path.
        monkeypatch.setattr(main, "RG_PATH", None)

        cases = _build_contract_cases(tmp_path)
        # Sanity: catch a typo in the cases list itself.
        assert len(cases) >= 11

        failures: list[str] = []
        for label, msg_type, inbound_payload, expected_type in cases:
            agent = _CapturingAgent()
            handler = main._HANDLERS[msg_type]
            synthetic_msg = {**inbound_payload, "request_id": _CONTRACT_REQ_ID}
            try:
                _run(agent._run_handler(handler, synthetic_msg, msg_type))
            except Exception as e:
                failures.append(f"{label}: dispatcher raised {type(e).__name__}: {e}")
                continue

            if not agent.sent:
                failures.append(f"{label}: dispatcher emitted no frames")
                continue
            if len(agent.sent) > 1:
                failures.append(f"{label}: dispatcher emitted {len(agent.sent)} frames, expected 1")

            try:
                envelope = json.loads(agent.sent[0])
            except json.JSONDecodeError as e:
                failures.append(f"{label}: outbound frame is not valid JSON: {e}")
                continue

            if envelope.get("type") != expected_type:
                failures.append(
                    f"{label}: envelope ``type`` is {envelope.get('type')!r}, "
                    f"expected {expected_type!r}"
                )
            if envelope.get("request_id") != _CONTRACT_REQ_ID:
                failures.append(
                    f"{label}: ``request_id`` is {envelope.get('request_id')!r}, "
                    f"expected {_CONTRACT_REQ_ID!r} — dispatcher dropped or "
                    f"overwrote the correlation id; backend cannot resolve "
                    f"the caller's Future"
                )
            payload = envelope.get("payload")
            if not isinstance(payload, dict):
                failures.append(
                    f"{label}: envelope ``payload`` is {type(payload).__name__}, "
                    f"expected dict — handler data must live nested under "
                    f"``payload`` so it cannot shadow envelope keys"
                )
            # Spot check: top-level envelope must have exactly the three
            # reserved keys (type / request_id / payload). Anything else
            # at the top level is a regression that risks future shadowing.
            extra = set(envelope.keys()) - {"type", "request_id", "payload"}
            if extra:
                failures.append(
                    f"{label}: envelope has unexpected top-level keys {sorted(extra)} — "
                    f"only ``type`` / ``request_id`` / ``payload`` are allowed"
                )

        assert not failures, "envelope contract violations:\n  - " + "\n  - ".join(failures)


class TestDecodeRgTextField:
    def test_decode_text(self):
        assert main._decode_rg_text_field({"text": "hello"}) == "hello"

    def test_decode_bytes(self):
        # base64 of "hello" = "aGVsbG8="
        assert main._decode_rg_text_field({"bytes": "aGVsbG8="}) == "hello"

    def test_decode_invalid_bytes_returns_replacement(self):
        # base64 of invalid utf-8 \xff\xfe → "//4="
        result = main._decode_rg_text_field({"bytes": "//4="})
        # Should not raise; result is a string with replacement chars
        assert isinstance(result, str)

    def test_decode_none(self):
        assert main._decode_rg_text_field(None) == ""

    def test_decode_unknown_shape(self):
        assert main._decode_rg_text_field({"weird": "thing"}) == ""
