"""Unit tests for the MCP remote_* tool layer.

These exercise `app.mcp.tools.remote` in isolation by mocking out:
- `authenticate()` so we don't need a real X-API-Key
- `_resolve_workspace()` so we don't touch MongoDB
- `_log_operation()` so audit writes don't hit Beanie
- `agent_manager.send_request()` so we don't need a real agent

Coverage focuses on the new behavior added in 2026-04-07:
- `_validate_remote_path` (defense-in-depth)
- `remote_exec` cwd/env passthrough + truncated flag passthrough
- `remote_read_file` offset/limit/encoding passthrough
- new `remote_stat` / `remote_file_exists` / `remote_glob` / `remote_grep`
  / `remote_mkdir` / `remote_delete_file` / `remote_move_file` /
  `remote_copy_file` tools
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.exceptions import ToolError

from app.mcp.tools import remote
from app.models.remote import RemoteExecLog


_MOCK_KEY_INFO = {"key_id": "test-key", "key_name": "test", "user_id": "test-user", "is_admin": True, "auth_kind": "api_key"}


def _fake_binding():
    return remote.ResolvedBinding(
        project_id="p-1",
        agent_id="agent-1",
        remote_path="/work",
    )


@pytest.fixture
def patch_auth():
    """Patch authenticate + _resolve_binding + _log_operation."""
    binding = _fake_binding()
    with patch("app.mcp.tools.remote.authenticate", new=AsyncMock(return_value=_MOCK_KEY_INFO)), \
         patch("app.mcp.tools.remote._resolve_binding", new=AsyncMock(return_value=binding)), \
         patch("app.mcp.tools.remote._log_operation", new=AsyncMock(return_value=None)):
        yield binding


# ──────────────────────────────────────────────
# _validate_remote_path
# ──────────────────────────────────────────────


class TestValidateRemotePath:
    def test_normal_path(self):
        assert remote._validate_remote_path("src/main.py") == "src/main.py"

    def test_dot_segment_allowed(self):
        # Single dots like "./foo" don't contain ".." parts
        remote._validate_remote_path("./foo")

    def test_traversal_rejected(self):
        with pytest.raises(ToolError, match="traversal"):
            remote._validate_remote_path("../etc/passwd")

    def test_traversal_in_middle_rejected(self):
        with pytest.raises(ToolError, match="traversal"):
            remote._validate_remote_path("a/../../b")

    def test_nul_byte_rejected(self):
        with pytest.raises(ToolError, match="Invalid"):
            remote._validate_remote_path("a\x00b")

    def test_newline_rejected(self):
        with pytest.raises(ToolError, match="Invalid"):
            remote._validate_remote_path("a\nb")

    def test_non_string_rejected(self):
        with pytest.raises(ToolError):
            remote._validate_remote_path(None)  # type: ignore[arg-type]

    def test_windows_separator_traversal_rejected(self):
        with pytest.raises(ToolError):
            remote._validate_remote_path("..\\evil")


# ──────────────────────────────────────────────
# _validate_remote_command
# ──────────────────────────────────────────────


class TestValidateRemoteCommand:
    def test_normal_command(self):
        assert remote._validate_remote_command("ls -la") == "ls -la"

    def test_chained_commands_allowed(self):
        # Multi-step invocations must join with ; or && on a single line
        remote._validate_remote_command("cd backend && pytest -x")

    def test_newline_rejected(self):
        with pytest.raises(ToolError, match="Invalid"):
            remote._validate_remote_command("ls\nrm -rf /")

    def test_carriage_return_rejected(self):
        with pytest.raises(ToolError, match="Invalid"):
            remote._validate_remote_command("ls\rrm")

    def test_nul_byte_rejected(self):
        with pytest.raises(ToolError, match="Invalid"):
            remote._validate_remote_command("a\x00b")

    def test_length_limit(self):
        with pytest.raises(ToolError, match="too long"):
            remote._validate_remote_command("x" * (remote.MAX_COMMAND_BYTES + 1))

    def test_empty_rejected(self):
        with pytest.raises(ToolError, match="empty"):
            remote._validate_remote_command("   ")

    def test_non_string_rejected(self):
        with pytest.raises(ToolError):
            remote._validate_remote_command(123)  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# _validate_remote_pattern
# ──────────────────────────────────────────────


class TestValidateRemotePattern:
    def test_normal_regex(self):
        assert remote._validate_remote_pattern("foo.*bar", kind="grep") == "foo.*bar"

    def test_normal_glob(self):
        remote._validate_remote_pattern("**/*.py", kind="glob")

    def test_empty_rejected(self):
        with pytest.raises(ToolError, match="empty"):
            remote._validate_remote_pattern("   ", kind="grep")

    def test_nul_rejected(self):
        with pytest.raises(ToolError, match="NUL"):
            remote._validate_remote_pattern("a\x00b", kind="grep")

    def test_newline_rejected(self):
        with pytest.raises(ToolError, match="CR/LF"):
            remote._validate_remote_pattern("a\nb", kind="grep")

    def test_carriage_return_rejected(self):
        with pytest.raises(ToolError, match="CR/LF"):
            remote._validate_remote_pattern("a\rb", kind="glob")

    def test_length_limit(self):
        with pytest.raises(ToolError, match="too long"):
            remote._validate_remote_pattern("x" * (remote.MAX_PATTERN_BYTES + 1), kind="grep")

    def test_kind_appears_in_error(self):
        # Caller-supplied ``kind`` should surface in messages so users can
        # tell whether grep or glob rejected them.
        with pytest.raises(ToolError, match="glob"):
            remote._validate_remote_pattern("", kind="glob")

    def test_non_string_rejected(self):
        with pytest.raises(ToolError):
            remote._validate_remote_pattern(123, kind="grep")  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# remote_exec
# ──────────────────────────────────────────────


class TestRemoteExec:
    async def test_basic_exec_passes_through_truncated_flags(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0,
            "stdout": "hello",
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_total_bytes": 5,
            "stderr_total_bytes": 0,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(
                project_id="p", command="echo hi", format="json",
            )

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"
        assert result["stdout_truncated"] is False
        assert result["stderr_truncated"] is False
        assert result["stdout_total_bytes"] == 5

    async def test_exec_passes_cwd_and_env_to_agent(self, patch_auth):
        send_request = AsyncMock(return_value={"exit_code": 0, "stdout": "", "stderr": ""})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_exec(
                project_id="p",
                command="pytest",
                cwd="backend",
                env={"TESTING": "1", "SECRET_KEY": "test"},
            )

        # Verify the payload pushed to the agent
        args, kwargs = send_request.call_args
        payload = args[2]  # send_request(agent_id, msg_type, payload, ...)
        assert payload["cwd_override"] == "backend"
        assert payload["env"] == {"TESTING": "1", "SECRET_KEY": "test"}

    async def test_exec_rejects_traversal_in_cwd(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="traversal"):
                await remote.remote_exec(
                    project_id="p", command="ls", cwd="../../etc",
                )

    async def test_exec_rejects_non_string_env_values(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="strings"):
                await remote.remote_exec(
                    project_id="p", command="ls", env={"X": 123},  # type: ignore[arg-type]
                )

    async def test_exec_truncated_flag_true_when_agent_says_so(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0,
            "stdout": "x" * 100,
            "stderr": "",
            "stdout_truncated": True,
            "stderr_truncated": False,
            "stdout_total_bytes": 5_000_000,
            "stderr_total_bytes": 0,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(
                project_id="p", command="cat huge.log", format="json",
            )
        assert result["stdout_truncated"] is True
        assert result["stdout_total_bytes"] == 5_000_000


    async def test_exec_rejects_newline_in_command(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="Invalid"):
                await remote.remote_exec(project_id="p", command="ls\nrm -rf /")

    async def test_exec_rejects_nul_in_command(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="Invalid"):
                await remote.remote_exec(project_id="p", command="echo a\x00b")

    async def test_exec_rejects_oversized_command(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="too long"):
                await remote.remote_exec(
                    project_id="p", command="x" * (remote.MAX_COMMAND_BYTES + 1),
                )

    async def test_exec_rejects_empty_command(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="empty"):
                await remote.remote_exec(project_id="p", command="   ")

    # ── format="text" (default): Bash-like plain text for token efficiency

    async def test_exec_text_format_is_default(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "hello\n", "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(project_id="p", command="echo hello")
        assert isinstance(result, str)
        assert result == "hello\n"

    async def test_exec_text_zero_exit_omits_exit_marker(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "ok\n", "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(project_id="p", command="true")
        assert "[exit" not in result

    async def test_exec_text_nonzero_exit_shows_exit_marker(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 2, "stdout": "", "stderr": "boom\n",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(project_id="p", command="false")
        assert "[stderr]\nboom\n" in result
        assert "[exit 2]" in result

    async def test_exec_text_empty_stderr_omits_stderr_block(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "line\n", "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(project_id="p", command="echo line")
        assert "[stderr]" not in result

    async def test_exec_text_truncation_markers(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0,
            "stdout": "x" * 100,
            "stderr": "",
            "stdout_truncated": True,
            "stderr_truncated": False,
            "stdout_total_bytes": 5_000_000,
            "stderr_total_bytes": 0,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(project_id="p", command="cat huge.log")
        assert "[stdout truncated at 5000000 bytes]" in result
        assert "[stderr truncated" not in result

    async def test_exec_text_vs_json_size_reduction(self, patch_auth):
        """Typical command output: text format must be meaningfully smaller than JSON."""
        import json as _json

        send_request = AsyncMock(return_value={
            "exit_code": 0,
            "stdout": "line1\nline2\nline3\nline4\nline5\n" * 10,
            "stderr": "",
            "stdout_truncated": False,
            "stderr_truncated": False,
            "stdout_total_bytes": 300,
            "stderr_total_bytes": 0,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            text_result = await remote.remote_exec(
                project_id="p", command="cat lines", format="text",
            )
            json_result = await remote.remote_exec(
                project_id="p", command="cat lines", format="json",
            )
        json_bytes = len(_json.dumps(json_result))
        text_bytes = len(text_result)
        # ≥30% reduction is the acceptance target
        assert text_bytes <= json_bytes * 0.7, (
            f"text={text_bytes}B json={json_bytes}B (text must be ≤70% of json)"
        )

    async def test_exec_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_exec(
                    project_id="p", command="ls", format="xml",
                )

    async def test_exec_shell_default_omits_shell_field(self, patch_auth):
        """shell='default' must not send a shell key to the agent."""
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "", "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_exec(
                project_id="p", command="echo hi",
            )
        payload = send_request.call_args[0][2]
        assert "shell" not in payload

    async def test_exec_shell_bash_forwarded_to_agent(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "bash world\n", "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(
                project_id="p", command="echo bash world", shell="bash",
            )
        payload = send_request.call_args[0][2]
        assert payload["shell"] == "bash"
        assert result == "bash world\n"

    async def test_exec_rejects_unknown_shell(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="shell"):
                await remote.remote_exec(
                    project_id="p", command="ls", shell="fish",
                )


# ──────────────────────────────────────────────
# remote_read_file
# ──────────────────────────────────────────────


class TestRemoteReadFile:
    async def test_read_passes_offset_limit_to_agent(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "line2\nline3\n",
            "size": 100,
            "path": "/work/lines.txt",
            "encoding": "utf-8",
            "is_binary": False,
            "total_lines": 10,
            "truncated": True,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="lines.txt", offset=2, limit=2,
                format="json",
            )

        args, _kwargs = send_request.call_args
        payload = args[2]
        assert payload["offset"] == 2
        assert payload["limit"] == 2
        assert result["truncated"] is True
        assert result["total_lines"] == 10

    async def test_read_binary_passes_encoding(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "aGVsbG8=",  # base64 'hello'
            "size": 5,
            "path": "/work/blob.bin",
            "encoding": "base64",
            "is_binary": True,
            "total_lines": 0,
            "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            # Binary always returns dict regardless of format.
            result = await remote.remote_read_file(
                project_id="p", path="blob.bin", encoding="binary",
            )

        args, _ = send_request.call_args
        assert args[2]["encoding"] == "binary"
        assert isinstance(result, dict)
        assert result["is_binary"] is True
        assert result["encoding"] == "base64"

    async def test_read_rejects_negative_offset(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match=">= 0"):
                await remote.remote_read_file(project_id="p", path="x.txt", offset=-1)

    async def test_read_normalises_zero_offset_to_one(self, patch_auth):
        """offset=0 should be silently normalised to 1 (callers may use 0-based)."""
        mock_send = AsyncMock(return_value={
            "content": "line1\n", "size": 6, "path": "x.txt",
            "encoding": "utf-8", "is_binary": False, "total_lines": 1,
            "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", mock_send):
            await remote.remote_read_file(project_id="p", path="x.txt", offset=0)
            payload = mock_send.call_args[1].get("payload") or mock_send.call_args[0][2]
            assert payload["offset"] == 1

    async def test_read_rejects_negative_limit(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match=">= 0"):
                await remote.remote_read_file(project_id="p", path="x.txt", limit=-5)

    # ── format="text" (default): Read-compatible cat -n output

    async def test_read_text_format_is_default(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "line1\nline2\nline3\n",
            "size": 18, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 3, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(project_id="p", path="f.txt")
        assert isinstance(result, str)
        assert result == "1\tline1\n2\tline2\n3\tline3\n4\t\n"

    async def test_read_text_offset_reflected_in_line_numbers(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "line5\nline6\n",
            "size": 12, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 10, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", offset=5, limit=2,
            )
        assert result.startswith("5\tline5\n6\tline6\n")

    async def test_read_text_appends_truncation_marker(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "line1\nline2\n",
            "size": 12, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 500, "truncated": True,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", limit=2,
            )
        assert "[truncated at 500 total lines]" in result

    async def test_read_text_binary_returns_dict(self, patch_auth):
        """Binary content cannot be rendered as text — always dict."""
        send_request = AsyncMock(return_value={
            "content": "aGVsbG8=",
            "size": 5, "path": "/work/blob.bin", "encoding": "base64",
            "is_binary": True, "total_lines": 0, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            # format="text" is default but binary must still return dict.
            result = await remote.remote_read_file(
                project_id="p", path="blob.bin", encoding="binary",
            )
        assert isinstance(result, dict)
        assert result["is_binary"] is True

    async def test_read_text_empty_content(self, patch_auth):
        send_request = AsyncMock(return_value={
            "content": "",
            "size": 0, "path": "/work/empty.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 0, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(project_id="p", path="empty.txt")
        assert result == ""

    async def test_read_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_read_file(
                    project_id="p", path="x.txt", format="xml",
                )

    async def test_read_text_vs_json_size_reduction(self, patch_auth):
        """Typical text file: text format must be meaningfully smaller than JSON."""
        import json as _json

        send_request = AsyncMock(return_value={
            "content": "the quick brown fox\n" * 20,
            "size": 400, "path": "D:\\work\\lines.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 20, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            text_result = await remote.remote_read_file(
                project_id="p", path="lines.txt",
            )
            json_result = await remote.remote_read_file(
                project_id="p", path="lines.txt", format="json",
            )
        json_bytes = len(_json.dumps(json_result))
        text_bytes = len(text_result)
        # Text must stay within +5% of a local-Read-equivalent baseline.
        # Practically this means text should be ≤ JSON size (JSON has
        # ~160B of metadata overhead).
        assert text_bytes < json_bytes, (
            f"text={text_bytes}B json={json_bytes}B (text must be smaller)"
        )

    # ── if_not_hash: conditional read / differential response

    async def test_read_if_not_hash_match_returns_unchanged_text(self, patch_auth):
        import hashlib

        content = "line1\nline2\nline3\n"
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        send_request = AsyncMock(return_value={
            "content": content,
            "size": len(content), "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 3, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", if_not_hash=expected_hash,
            )
        assert isinstance(result, str)
        assert result == f"unchanged sha256:{expected_hash}\n"

    async def test_read_if_not_hash_match_returns_unchanged_json(self, patch_auth):
        import hashlib

        content = "hello\n"
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        send_request = AsyncMock(return_value={
            "content": content,
            "size": 6, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", format="json",
                if_not_hash=expected_hash,
            )
        assert result == {"unchanged": True, "hash": expected_hash}

    async def test_read_if_not_hash_mismatch_returns_full_content_plus_hash(
        self, patch_auth,
    ):
        import hashlib

        content = "new content\n"
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        stale_hash = "0" * 64

        send_request = AsyncMock(return_value={
            "content": content,
            "size": len(content), "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", if_not_hash=stale_hash,
            )
        assert isinstance(result, str)
        # Full content rendered, with hash trailer so caller can update cache.
        assert "1\tnew content\n" in result
        assert f"[sha256:{new_hash}]\n" in result

    async def test_read_if_not_hash_json_mismatch_includes_hash_field(
        self, patch_auth,
    ):
        import hashlib

        content = "body\n"
        new_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        send_request = AsyncMock(return_value={
            "content": content,
            "size": 5, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", format="json",
                if_not_hash="deadbeef" * 8,
            )
        assert result["hash"] == new_hash
        assert result["content"] == content

    async def test_read_without_if_not_hash_omits_hash(self, patch_auth):
        """When caller didn't opt into caching, no hash work is done."""
        send_request = AsyncMock(return_value={
            "content": "x\n",
            "size": 2, "path": "/work/f.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="f.txt", format="json",
            )
        assert "hash" not in result

    async def test_read_if_not_hash_match_90_percent_reduction(self, patch_auth):
        """Unchanged response must be dramatically smaller than full content."""
        import hashlib

        content = "line of data\n" * 200  # 2.6 KB of content
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        send_request = AsyncMock(return_value={
            "content": content,
            "size": len(content), "path": "/work/big.txt", "encoding": "utf-8",
            "is_binary": False, "total_lines": 200, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            full_result = await remote.remote_read_file(
                project_id="p", path="big.txt",
            )
            unchanged_result = await remote.remote_read_file(
                project_id="p", path="big.txt", if_not_hash=expected_hash,
            )
        # unchanged marker must be ≤10% of full content
        assert len(unchanged_result) <= len(full_result) * 0.1, (
            f"unchanged={len(unchanged_result)}B full={len(full_result)}B"
        )


# ──────────────────────────────────────────────
# remote_stat / remote_file_exists
# ──────────────────────────────────────────────


class TestRemoteStat:
    async def test_stat_returns_metadata(self, patch_auth):
        # ``send_request`` unwraps the envelope and returns the inner
        # payload dict directly to the MCP tool. With the envelope
        # redesign (2026-04-08), an inner ``type`` key is safe again
        # because handler data is nested under ``payload`` at the wire
        # level — no shadowing risk.
        send_request = AsyncMock(return_value={
            "exists": True,
            "type": "file",
            "size": 1234,
            "mtime": "2026-04-07T00:00:00+00:00",
            "mode": "0o644",
            "path": "/work/f.txt",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_stat(project_id="p", path="f.txt")
        assert result["exists"] is True
        assert result["type"] == "file"
        assert result["size"] == 1234

    async def test_stat_nonexistent_returns_null_type(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exists": False,
            "type": None,
            "path": "/work/nope.txt",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_stat(project_id="p", path="nope.txt")
        assert result["exists"] is False
        assert result["type"] is None

    async def test_file_exists_strips_to_minimal_response(self, patch_auth):
        send_request = AsyncMock(return_value={
            "exists": True,
            "type": "directory",
            "size": 0,
            "mtime": "2026-04-07T00:00:00+00:00",
            "mode": "0o755",
            "path": "/work/d",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_file_exists(project_id="p", path="d")
        assert result == {"exists": True, "type": "directory"}


# ──────────────────────────────────────────────
# remote_glob / remote_grep
# ──────────────────────────────────────────────


class TestRemoteGlob:
    async def test_glob_rejects_newline_pattern(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="CR/LF"):
                await remote.remote_glob(project_id="p", pattern="*.py\n")

    async def test_glob_rejects_nul_pattern(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="NUL"):
                await remote.remote_glob(project_id="p", pattern="*.\x00py")

    async def test_glob_rejects_oversized_pattern(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="too long"):
                await remote.remote_glob(
                    project_id="p", pattern="x" * (remote.MAX_PATTERN_BYTES + 1),
                )

    async def test_glob_passes_pattern(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{"path": "/work/a.py", "size": 10, "mtime": "..."}],
            "count": 1,
            "base": "/work",
            "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_glob(
                project_id="p", pattern="**/*.py", path=".",
                format="json",
            )
        args, _ = send_request.call_args
        assert args[2]["pattern"] == "**/*.py"
        assert result["count"] == 1

    # ── format="text" (default): one path per line

    async def test_glob_text_format_is_default(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [
                {"path": "src/a.py", "size": 100, "mtime": "t3"},
                {"path": "src/b.py", "size": 200, "mtime": "t2"},
                {"path": "src/c.py", "size": 300, "mtime": "t1"},
            ],
            "count": 3, "base": "src", "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_glob(
                project_id="p", pattern="**/*.py",
            )
        assert isinstance(result, str)
        assert result == "src/a.py\nsrc/b.py\nsrc/c.py\n"

    async def test_glob_text_appends_truncated_marker(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{"path": "a.py", "size": 1, "mtime": "t"}],
            "count": 1, "base": ".", "truncated": True,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_glob(project_id="p", pattern="*.py")
        assert "[truncated]" in result

    async def test_glob_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_glob(
                    project_id="p", pattern="*.py", format="xml",
                )


class TestRemoteListDir:
    async def test_list_dir_text_format_is_default(self, patch_auth):
        # Agent uses the short ``type == "dir"`` form.
        send_request = AsyncMock(return_value={
            "entries": [
                {"name": "README.md", "type": "file", "size": 100, "mtime": "t"},
                {"name": "src", "type": "dir"},
                {"name": "tests", "type": "dir"},
                {"name": "pyproject.toml", "type": "file", "size": 50, "mtime": "t"},
            ],
            "path": ".",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_list_dir(project_id="p", path=".")
        assert isinstance(result, str)
        assert result == "README.md\nsrc/\ntests/\npyproject.toml\n"

    async def test_list_dir_text_accepts_both_dir_type_spellings(self, patch_auth):
        """Accept both 'dir' (agent) and 'directory' (doc spec) for resilience."""
        send_request = AsyncMock(return_value={
            "entries": [
                {"name": "a", "type": "dir"},
                {"name": "b", "type": "directory"},
            ],
            "path": ".",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_list_dir(project_id="p", path=".")
        assert result == "a/\nb/\n"

    async def test_list_dir_json_format_returns_dict(self, patch_auth):
        send_request = AsyncMock(return_value={
            "entries": [{"name": "a.txt", "type": "file", "size": 1, "mtime": "t"}],
            "path": ".",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_list_dir(
                project_id="p", path=".", format="json",
            )
        assert isinstance(result, dict)
        assert result["entries"][0]["name"] == "a.txt"
        assert result["count"] == 1

    async def test_list_dir_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_list_dir(
                    project_id="p", path=".", format="xml",
                )

    async def test_list_dir_text_vs_json_size_reduction(self, patch_auth):
        """50-entry listing: text must be meaningfully smaller."""
        import json as _json

        entries = [
            {"name": f"file_{i:02d}.py", "type": "file",
             "size": 100 + i, "mtime": "2026-04-17T00:00:00+00:00"}
            for i in range(50)
        ]
        send_request = AsyncMock(return_value={"entries": entries, "path": "."})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            text_result = await remote.remote_list_dir(project_id="p", path=".")
            json_result = await remote.remote_list_dir(
                project_id="p", path=".", format="json",
            )
        json_bytes = len(_json.dumps(json_result))
        text_bytes = len(text_result)
        # 50%+ reduction is the acceptance target.
        assert text_bytes <= json_bytes * 0.5, (
            f"text={text_bytes}B json={json_bytes}B (text must be ≤50% of json)"
        )


class TestRemoteGrep:
    async def test_grep_passes_filters(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{"file": "/work/a.py", "line": 1, "text": "import foo"}],
            "count": 1,
            "files_scanned": 1,
            "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p",
                pattern="import",
                path="src",
                glob="*.py",
                case_insensitive=True,
                max_results=50,
                format="json",
            )
        args, _ = send_request.call_args
        payload = args[2]
        assert payload["pattern"] == "import"
        assert payload["glob"] == "*.py"
        assert payload["case_insensitive"] is True
        assert payload["max_results"] == 50
        assert result["count"] == 1

    async def test_grep_rejects_empty_pattern(self, patch_auth):
        with pytest.raises(ToolError, match="empty"):
            await remote.remote_grep(project_id="p", pattern="")

    async def test_grep_rejects_max_results_zero(self, patch_auth):
        with pytest.raises(ToolError, match="max_results"):
            await remote.remote_grep(project_id="p", pattern="x", max_results=0)

    async def test_grep_rejects_max_results_negative(self, patch_auth):
        with pytest.raises(ToolError, match="max_results"):
            await remote.remote_grep(project_id="p", pattern="x", max_results=-5)

    async def test_grep_rejects_max_results_too_large(self, patch_auth):
        with pytest.raises(ToolError, match="max_results"):
            await remote.remote_grep(project_id="p", pattern="x", max_results=2001)

    async def test_grep_accepts_max_results_at_boundaries(self, patch_auth):
        send_request = AsyncMock(return_value={"matches": [], "count": 0,
                                               "files_scanned": 0, "truncated": False})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_grep(project_id="p", pattern="x", max_results=1)
            await remote.remote_grep(project_id="p", pattern="x", max_results=2000)
        assert send_request.call_count == 2

    async def test_grep_respect_gitignore_default_false(self, patch_auth):
        """Default for respect_gitignore must be False (Phase 1 compat)."""
        send_request = AsyncMock(return_value={"matches": [], "count": 0,
                                               "files_scanned": 0, "truncated": False})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_grep(project_id="p", pattern="x")
        payload = send_request.call_args[0][2]
        assert payload["respect_gitignore"] is False

    async def test_grep_respect_gitignore_passthrough(self, patch_auth):
        send_request = AsyncMock(return_value={"matches": [], "count": 0,
                                               "files_scanned": 0, "truncated": False})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_grep(project_id="p", pattern="x", respect_gitignore=True)
        payload = send_request.call_args[0][2]
        assert payload["respect_gitignore"] is True

    async def test_grep_context_lines_passthrough(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{"file": "/work/a.py", "line": 5, "text": "needle",
                         "context_before": [{"line": 4, "text": "before"}],
                         "context_after": [{"line": 6, "text": "after"}]}],
            "count": 1, "files_scanned": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="needle", context_lines=2,
                format="json",
            )
        payload = send_request.call_args[0][2]
        assert payload["context_lines"] == 2
        assert result["matches"][0]["context_before"][0]["text"] == "before"
        assert result["matches"][0]["context_after"][0]["text"] == "after"

    async def test_grep_context_lines_default_zero(self, patch_auth):
        send_request = AsyncMock(return_value={"matches": [], "count": 0,
                                               "files_scanned": 0, "truncated": False})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_grep(project_id="p", pattern="x")
        payload = send_request.call_args[0][2]
        assert payload["context_lines"] == 0

    async def test_grep_rejects_context_lines_negative(self, patch_auth):
        with pytest.raises(ToolError, match="context_lines"):
            await remote.remote_grep(project_id="p", pattern="x", context_lines=-1)

    async def test_grep_rejects_context_lines_too_large(self, patch_auth):
        with pytest.raises(ToolError, match="context_lines"):
            await remote.remote_grep(project_id="p", pattern="x", context_lines=21)

    # ── format="text" (default): ripgrep-style rendering

    async def test_grep_text_format_is_default(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [
                {"file": "a.py", "line": 1, "text": "import foo\r"},
                {"file": "a.py", "line": 5, "text": "foo.bar()\n"},
                {"file": "b.py", "line": 2, "text": "from foo import x"},
            ],
            "count": 3, "files_scanned": 2, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(project_id="p", pattern="foo")
        assert isinstance(result, str)
        assert result == (
            "a.py:1:import foo\n"
            "a.py:5:foo.bar()\n"
            "b.py:2:from foo import x\n"
        )

    async def test_grep_text_files_with_matches_mode(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [
                {"file": "a.py", "line": 1, "text": "x"},
                {"file": "a.py", "line": 5, "text": "y"},
                {"file": "b.py", "line": 2, "text": "z"},
            ],
            "count": 3, "files_scanned": 2, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="x",
                output_mode="files_with_matches",
            )
        assert result == "a.py\nb.py\n"

    async def test_grep_text_count_mode(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [
                {"file": "a.py", "line": 1, "text": "x"},
                {"file": "a.py", "line": 5, "text": "y"},
                {"file": "b.py", "line": 2, "text": "z"},
            ],
            "count": 3, "files_scanned": 2, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="x", output_mode="count",
            )
        assert "a.py:2" in result
        assert "b.py:1" in result

    async def test_grep_text_context_lines(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{
                "file": "a.py", "line": 5, "text": "needle\n",
                "context_before": [{"line": 4, "text": "before"}],
                "context_after": [{"line": 6, "text": "after"}],
            }],
            "count": 1, "files_scanned": 1, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="needle", context_lines=1,
            )
        assert result == (
            "a.py-4-before\n"
            "a.py:5:needle\n"
            "a.py-6-after\n"
        )

    async def test_grep_text_truncation_marker(self, patch_auth):
        send_request = AsyncMock(return_value={
            "matches": [{"file": "a.py", "line": 1, "text": "hit"}],
            "count": 1, "files_scanned": 1, "truncated": True,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="hit", max_results=200,
            )
        assert "[truncated at 200 matches]" in result

    async def test_grep_rejects_invalid_format(self, patch_auth):
        with pytest.raises(ToolError, match="format"):
            await remote.remote_grep(project_id="p", pattern="x", format="xml")

    async def test_grep_rejects_invalid_output_mode(self, patch_auth):
        with pytest.raises(ToolError, match="output_mode"):
            await remote.remote_grep(
                project_id="p", pattern="x", output_mode="bogus",
            )

    async def test_grep_text_vs_json_size_reduction(self, patch_auth):
        """Multi-match result: text format must be meaningfully smaller."""
        import json as _json

        matches = [
            {"file": f"src/module_{i}.py", "line": i * 3, "text": f"hit number {i}"}
            for i in range(20)
        ]
        agent_result = {
            "matches": matches, "count": 20,
            "files_scanned": 20, "truncated": False,
        }
        send_request = AsyncMock(return_value=agent_result)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            text_result = await remote.remote_grep(project_id="p", pattern="hit")
            json_result = await remote.remote_grep(
                project_id="p", pattern="hit", format="json",
            )
        json_bytes = len(_json.dumps(json_result))
        text_bytes = len(text_result)
        # Content mode should be meaningfully smaller than per-match JSON.
        assert text_bytes <= json_bytes * 0.6, (
            f"text={text_bytes}B json={json_bytes}B (text must be ≤60% of json)"
        )


# ──────────────────────────────────────────────
# remote_mkdir / remote_delete_file / remote_move_file / remote_copy_file
# ──────────────────────────────────────────────


class TestRemoteMkdir:
    async def test_mkdir_passes_parents(self, patch_auth):
        send_request = AsyncMock(return_value={"success": True, "path": "/work/a/b"})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_mkdir(
                project_id="p", path="a/b", parents=True,
            )
        args, _ = send_request.call_args
        assert args[2]["parents"] is True
        assert result["success"] is True


class TestRemoteDelete:
    async def test_delete_requires_recursive_for_dirs(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": False,
            "error": "Directory delete requires recursive=True",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_delete_file(
                project_id="p", path="d",
            )
        assert result["success"] is False

    async def test_delete_recursive(self, patch_auth):
        send_request = AsyncMock(return_value={"success": True, "path": "/work/d", "type": "directory"})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_delete_file(
                project_id="p", path="d", recursive=True,
            )
        args, _ = send_request.call_args
        assert args[2]["recursive"] is True
        assert result["success"] is True

    async def test_delete_traversal_rejected(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="traversal"):
                await remote.remote_delete_file(project_id="p", path="../etc/passwd")


class TestRemoteMove:
    async def test_move_passes_src_dst(self, patch_auth):
        send_request = AsyncMock(return_value={"success": True, "src": "/work/a", "dst": "/work/b"})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_move_file(
                project_id="p", src="a", dst="b", overwrite=True,
            )
        args, _ = send_request.call_args
        payload = args[2]
        assert payload["src"] == "a"
        assert payload["dst"] == "b"
        assert payload["overwrite"] is True
        assert result["success"] is True

    async def test_move_traversal_rejected(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError):
                await remote.remote_move_file(
                    project_id="p", src="a", dst="../escape",
                )


class TestRemoteCopy:
    async def test_copy_passes_src_dst(self, patch_auth):
        send_request = AsyncMock(return_value={"success": True, "src": "/work/a", "dst": "/work/b"})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_copy_file(
                project_id="p", src="a", dst="b",
            )
        args, _ = send_request.call_args
        payload = args[2]
        assert payload["src"] == "a"
        assert payload["dst"] == "b"
        assert result["success"] is True


# ──────────────────────────────────────────────
# list_remote_agents — exposes update-related fields
# ──────────────────────────────────────────────


class TestListRemoteAgents:
    """Regression: the MCP `list_remote_agents` tool must expose
    `agent_version`, `auto_update`, and `update_channel` so operators
    can tell from Claude Code whether a rollout has landed.
    """

    async def test_returns_update_fields(self, admin_user):
        from app.core.security import hash_api_key
        from app.models.remote import RemoteAgent

        agent = RemoteAgent(
            name="release-check-host",
            key_hash=hash_api_key("ta_listtest_token_0001"),
            owner_id=str(admin_user.id),
            hostname="rch",
            os_type="win32",
            agent_version="0.2.0",
            auto_update=True,
            update_channel="beta",
        )
        await agent.insert()

        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ):
            result = await remote.list_remote_agents()

        assert len(result) == 1
        entry = result[0]
        assert entry["name"] == "release-check-host"
        assert entry["agent_version"] == "0.2.0"
        assert entry["auto_update"] is True
        assert entry["update_channel"] == "beta"
        # Existing fields must still be present.
        assert entry["id"] == str(agent.id)
        assert entry["os_type"] == "win32"
        assert "project_count" in entry
        assert "is_online" in entry

    async def test_handles_agent_without_version(self, admin_user):
        """Older agents (pre-self-update build) report agent_version=None."""
        from app.core.security import hash_api_key
        from app.models.remote import RemoteAgent

        agent = RemoteAgent(
            name="legacy-agent",
            key_hash=hash_api_key("ta_listtest_token_0002"),
            owner_id=str(admin_user.id),
            os_type="linux",
            # agent_version deliberately omitted -> defaults to None
        )
        await agent.insert()

        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ):
            result = await remote.list_remote_agents()

        entry = next(e for e in result if e["name"] == "legacy-agent")
        assert entry["agent_version"] is None
        assert entry["auto_update"] is True  # model default
        assert entry["update_channel"] == "stable"  # model default

    async def test_returns_shell_fields(self, admin_user):
        """list_remote_agents exposes available_shells and default_shell."""
        from app.core.security import hash_api_key
        from app.models.remote import RemoteAgent

        agent = RemoteAgent(
            name="win-agent-with-bash",
            key_hash=hash_api_key("ta_listtest_token_0003"),
            owner_id=str(admin_user.id),
            os_type="win32",
            available_shells=["cmd", "pwsh", "bash"],
        )
        await agent.insert()

        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ):
            result = await remote.list_remote_agents()

        entry = next(e for e in result if e["name"] == "win-agent-with-bash")
        assert entry["available_shells"] == ["cmd", "pwsh", "bash"]
        # bash wins because it's POSIX and the agent reported it
        assert entry["default_shell"] == "bash"


class TestDeriveDefaultShell:
    """Unit coverage for the platform-aware default-shell picker."""

    def test_bash_wins_when_reported(self):
        assert (
            remote._derive_default_shell("win32", ["cmd", "pwsh", "bash"]) == "bash"
        )

    def test_basename_match_for_full_paths(self):
        """Agent reports absolute paths; helper must still match on stem."""
        shells = [
            r"C:\Windows\system32\cmd.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ]
        assert remote._derive_default_shell("win32", shells) == "bash"

    def test_posix_fallback_when_no_reported_shells(self):
        assert remote._derive_default_shell("linux", []) == "sh"
        assert remote._derive_default_shell("darwin", []) == "sh"

    def test_windows_fallback_when_no_reported_shells(self):
        """Legacy agents (no shell reporting) keep cmd.exe as default."""
        assert remote._derive_default_shell("win32", []) == "cmd"
        assert remote._derive_default_shell("windows", []) == "cmd"

    def test_prefers_zsh_over_sh(self):
        assert remote._derive_default_shell("darwin", ["sh", "zsh"]) == "zsh"

    def test_prefers_zsh_over_sh_with_paths(self):
        assert (
            remote._derive_default_shell("darwin", ["/bin/sh", "/bin/zsh"]) == "zsh"
        )


# ──────────────────────────────────────────────
# Regression: CLAUDE.md "No error hiding" / Security H-3
#
# The tests below lock in the audit-trail guarantees added alongside the
# error-hiding sweep (task 69d62a50):
#
#   1. Denied attempts (auth failure, _resolve_binding failure,
#      _validate_remote_path failure) MUST be recorded to
#      RemoteExecLog with error="denied: ...". Silently rejecting
#      a request was the Security H-3 finding.
#
#   2. _log_operation DB write failures MUST propagate. An audit
#      record dropped on the floor is a critical event — swallowing
#      the exception would re-introduce the exact pattern CLAUDE.md
#      forbids.
#
#   3. Successful operations must NOT double-log (no denied row
#      when the happy path ran to completion).
# ──────────────────────────────────────────────


class TestDeniedAuditTrail:
    """Security H-3: rejected MCP tool calls must leave an audit trail."""

    async def test_auth_failure_is_recorded_as_denied(self):
        """An McpAuthError from authenticate() must produce a denied entry."""
        from app.mcp.auth import McpAuthError

        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(side_effect=McpAuthError("Invalid API key")),
        ):
            with pytest.raises(ToolError):
                await remote.remote_exec(project_id="p-1", command="ls")

        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert logs[0].operation == "exec"
        assert logs[0].error.startswith("denied: ")
        assert "Invalid API key" in logs[0].error
        # Before the binding is resolved, project_id falls back to the
        # raw argument so the operator can still correlate the attempt.
        assert logs[0].project_id == "p-1"
        assert logs[0].agent_id == ""

    async def test_resolve_binding_failure_is_recorded_as_denied(self):
        """A ToolError from _resolve_binding() must produce a denied entry."""
        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ), patch(
            "app.mcp.tools.remote._resolve_binding",
            new=AsyncMock(side_effect=ToolError("No remote agent bound to project p-1")),
        ):
            with pytest.raises(ToolError):
                await remote.remote_stat(project_id="p-1", path="x.txt")

        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert logs[0].operation == "stat"
        assert logs[0].error.startswith("denied: ")
        assert "No remote agent bound" in logs[0].error
        assert logs[0].mcp_key_id == _MOCK_KEY_INFO["key_id"]

    async def test_path_validation_failure_is_recorded_as_denied(self):
        """Path traversal probes must leave an audit entry (attack signal)."""
        binding = _fake_binding()
        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ), patch(
            "app.mcp.tools.remote._resolve_binding",
            new=AsyncMock(return_value=binding),
        ):
            with pytest.raises(ToolError, match="traversal"):
                await remote.remote_read_file(
                    project_id="p-1", path="../../etc/passwd",
                )

        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert logs[0].operation == "read_file"
        assert logs[0].error.startswith("denied: ")
        assert "traversal" in logs[0].error
        # binding was resolved before validation failed, so project_id
        # / agent_id come from the binding not the raw arg.
        assert logs[0].project_id == binding.project_id
        assert logs[0].agent_id == binding.agent_id

    async def test_command_validation_failure_is_recorded_as_denied(self):
        """Rejected remote_exec payloads (newline/NUL/too long) must audit."""
        binding = _fake_binding()
        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ), patch(
            "app.mcp.tools.remote._resolve_binding",
            new=AsyncMock(return_value=binding),
        ):
            with pytest.raises(ToolError, match="Invalid"):
                await remote.remote_exec(
                    project_id="p-1", command="ls\nrm -rf /",
                )

        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert logs[0].operation == "exec"
        assert logs[0].error.startswith("denied: ")

    async def test_happy_path_does_not_record_denied_entry(self):
        """Regression guard: successful calls must not emit a denied row."""
        binding = _fake_binding()
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": "ok", "stderr": "",
        })
        with patch(
            "app.mcp.tools.remote.authenticate",
            new=AsyncMock(return_value=_MOCK_KEY_INFO),
        ), patch(
            "app.mcp.tools.remote._resolve_binding",
            new=AsyncMock(return_value=binding),
        ), patch(
            "app.mcp.tools.remote.agent_manager.send_request", send_request,
        ):
            await remote.remote_exec(project_id="p-1", command="echo hi")

        denied = await RemoteExecLog.find({"error": {"$regex": "^denied:"}}).to_list()
        assert denied == []


class TestEnvDenylist:
    """Security H-2: remote_exec env must reject runtime-hijack vars."""

    @pytest.mark.parametrize("bad_key", [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "PATH",
        "PYTHONPATH",
        "PYTHONSTARTUP",
        "NODE_OPTIONS",
        "CLASSPATH",
        "JAVA_TOOL_OPTIONS",
        "PERL5OPT",
        "RUBYOPT",
        # Case insensitivity guard — a denylist keyed on uppercase must
        # not be evadable by submitting the lowercase form on a
        # case-sensitive OS.
        "ld_preload",
        "Path",
    ])
    async def test_denylisted_env_key_rejected(self, patch_auth, bad_key):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="denied"):
                await remote.remote_exec(
                    project_id="p", command="ls",
                    env={bad_key: "/tmp/evil.so"},
                )

    async def test_denylisted_env_key_audits_as_denied(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError):
                await remote.remote_exec(
                    project_id="p", command="ls",
                    env={"LD_PRELOAD": "/tmp/evil.so"},
                )
        # patch_auth mocks _log_operation but _log_denied is real —
        # the RemoteExecLog collection should have the denied row.
        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert logs[0].error.startswith("denied: ")
        assert "LD_PRELOAD" in logs[0].error

    async def test_harmless_env_key_passes_through(self, patch_auth):
        send_request = AsyncMock(return_value={"exit_code": 0, "stdout": "", "stderr": ""})
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            await remote.remote_exec(
                project_id="p", command="ls",
                env={"TESTING": "1", "MY_APP_CONFIG": "dev"},
            )
        # No denied row, payload was actually sent
        assert send_request.call_count == 1


class TestSecretMasking:
    """Security H-4: audit log detail must mask common secret shapes."""

    def test_bearer_token_masked(self):
        masked = remote._mask_secrets('curl -H "Authorization: Bearer sk-abcdef123" https://api')
        assert "sk-abcdef" not in masked
        assert "***" in masked

    def test_token_flag_masked(self):
        masked = remote._mask_secrets("gh api --token=ghp_abcdef1234567890")
        assert "ghp_abcdef" not in masked
        assert "***" in masked

    def test_env_var_assignment_masked(self):
        masked = remote._mask_secrets("AWS_SECRET_KEY=wJalrXUtnFEMI ./deploy.sh")
        assert "wJalrXUtn" not in masked
        assert "***" in masked
        # The key name should survive so operators can see what kind of
        # secret was there without the value.
        assert "AWS_SECRET_KEY" in masked

    def test_password_flag_masked(self):
        masked = remote._mask_secrets("psql -U foo --password supersecret123 -h db")
        assert "supersecret" not in masked

    def test_plain_text_unchanged(self):
        text = "git log --oneline -n 5"
        assert remote._mask_secrets(text) == text

    async def test_log_operation_masks_detail(self):
        """End-to-end: a command containing a bearer token must be
        stored as ``***`` in RemoteExecLog.detail."""
        binding = remote.ResolvedBinding(
            project_id="p-1", agent_id="a-1", remote_path="/w",
        )
        await remote._log_operation(
            binding=binding,
            operation="exec",
            detail='curl -H "Authorization: Bearer sk-topsecret" https://api',
            key_info={"key_id": "k-1", "user_id": "u-1"},
        )
        logs = await RemoteExecLog.find_all().to_list()
        assert len(logs) == 1
        assert "sk-topsecret" not in logs[0].detail
        assert "***" in logs[0].detail
        # Audit log carries both the key id AND the owning user id so
        # operators can answer "who did this?" with a single User
        # lookup instead of the historical 2-hop chain.
        assert logs[0].mcp_key_id == "k-1"
        assert logs[0].mcp_key_owner_id == "u-1"


class TestAuditLogPropagates:
    """CLAUDE.md "No error hiding": audit write failures must NOT be swallowed."""

    async def test_log_operation_insert_failure_propagates(self):
        """If RemoteExecLog.insert() blows up, the exception reaches the caller.

        Previously ``_log_operation`` wrapped the insert in
        ``except Exception: logger.warning(...)`` which meant a broken
        audit store would silently drop rows. The sweep removed that
        swallow — verify the new behavior.
        """
        binding = _fake_binding()

        with patch.object(
            RemoteExecLog, "insert",
            new=AsyncMock(side_effect=RuntimeError("mongo is down")),
        ):
            with pytest.raises(RuntimeError, match="mongo is down"):
                await remote._log_operation(
                    binding=binding,
                    operation="exec",
                    detail="ls",
                    key_info={"key_id": "test-key", "user_id": "u-1"},
                )

    async def test_log_denied_insert_failure_propagates(self):
        """Same guarantee for the denied audit helper."""
        with patch.object(
            RemoteExecLog, "insert",
            new=AsyncMock(side_effect=RuntimeError("audit store unavailable")),
        ):
            with pytest.raises(RuntimeError, match="audit store unavailable"):
                await remote._log_denied(
                    operation="exec",
                    project_id="p-1",
                    agent_id="a-1",
                    key_info={"key_id": "k-1", "user_id": "u-1"},
                    detail="ls",
                    reason="test",
                )


# ──────────────────────────────────────────────
# remote_edit_file
# ──────────────────────────────────────────────


class TestTruncateTextHelper:
    """Unit tests for the _maybe_truncate_text helper."""

    def test_under_limit_returns_as_is(self):
        out = remote._maybe_truncate_text(
            "line1\nline2\nline3\n", max_bytes=1000,
        )
        assert out == "line1\nline2\nline3\n"

    def test_none_max_bytes_passes_through(self):
        """Large text + max_bytes=None must be returned unchanged."""
        text = "x\n" * 5000
        out = remote._maybe_truncate_text(text, max_bytes=None)
        assert out == text

    def test_over_limit_truncates_with_marker(self):
        text = "".join(f"line{i}\n" for i in range(100))
        out = remote._maybe_truncate_text(
            text, max_bytes=200,
            continue_hint="pass offset= to continue",
        )
        assert len(out) <= 400  # Some slack for marker
        assert "bytes omitted" in out
        assert "pass offset= to continue" in out
        # Should contain both head and tail samples
        assert "line0" in out
        assert "line99" in out

    def test_omitted_count_is_accurate(self):
        text = "a" * 1000
        out = remote._maybe_truncate_text(text, max_bytes=100)
        assert "[... 900 bytes omitted ...]" in out


class TestRemoteReadFileTruncation:
    async def test_read_file_truncates_when_over_max_bytes(self, patch_auth):
        big_content = "line\n" * 20_000  # 100 KB
        send_request = AsyncMock(return_value={
            "content": big_content,
            "size": len(big_content), "path": "/work/big.log",
            "encoding": "utf-8", "is_binary": False,
            "total_lines": 20_000, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(
                project_id="p", path="big.log", max_bytes=5_000,
            )
        assert isinstance(result, str)
        assert len(result) < 10_000  # Well under the original
        assert "bytes omitted" in result
        assert "offset=" in result  # Continuation hint

    async def test_read_file_no_max_bytes_full_content(self, patch_auth):
        content = "line\n" * 1000
        send_request = AsyncMock(return_value={
            "content": content, "size": len(content), "path": "/work/f.txt",
            "encoding": "utf-8", "is_binary": False,
            "total_lines": 1000, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_file(project_id="p", path="f.txt")
        assert "bytes omitted" not in result


class TestRemoteExecTruncation:
    async def test_exec_truncates_large_stdout(self, patch_auth):
        big_stdout = "log line\n" * 20_000  # ~180 KB
        send_request = AsyncMock(return_value={
            "exit_code": 0, "stdout": big_stdout, "stderr": "",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec(
                project_id="p", command="cat huge.log", max_bytes=2_000,
            )
        assert len(result) < 5_000
        assert "bytes omitted" in result
        assert "tail" in result.lower()  # Continue hint mentions tail/filter


class TestRemoteGrepTruncation:
    async def test_grep_truncates_many_matches(self, patch_auth):
        matches = [
            {"file": f"src/file_{i}.py", "line": i, "text": f"very long match text {i}"}
            for i in range(500)
        ]
        send_request = AsyncMock(return_value={
            "matches": matches, "count": 500,
            "files_scanned": 500, "truncated": False,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_grep(
                project_id="p", pattern="text", max_bytes=3_000,
            )
        assert len(result) < 6_000
        assert "bytes omitted" in result


class TestRemoteReadFilesBatch:
    async def test_read_files_concatenates_with_headers(self, patch_auth):
        # Each call to send_request returns a different file.
        payloads = [
            {"content": "a content\n", "size": 10, "path": "/work/a.txt",
             "encoding": "utf-8", "is_binary": False, "total_lines": 1,
             "truncated": False},
            {"content": "b content\n", "size": 10, "path": "/work/b.txt",
             "encoding": "utf-8", "is_binary": False, "total_lines": 1,
             "truncated": False},
        ]
        send_request = AsyncMock(side_effect=payloads)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_read_files(
                project_id="p", paths=["a.txt", "b.txt"],
            )
        assert result == (
            "=== /work/a.txt ===\n"
            "a content\n"
            "=== /work/b.txt ===\n"
            "b content\n"
        )

    async def test_read_files_per_file_error_inline(self, patch_auth):
        good = {"content": "ok\n", "size": 3, "path": "/work/a.txt",
                "encoding": "utf-8", "is_binary": False, "total_lines": 1,
                "truncated": False}

        async def side_effect(agent_id, msg_type, payload, **kwargs):
            if payload["path"] == "a.txt":
                return good
            raise ToolError("file not found")

        with patch(
            "app.mcp.tools.remote.agent_manager.send_request",
            new=AsyncMock(side_effect=side_effect),
        ):
            result = await remote.remote_read_files(
                project_id="p", paths=["a.txt", "missing.txt"],
                format="json",
            )
        assert result["count"] == 2
        assert result["errors"] == 1
        assert result["files"][0]["content"] == "ok\n"
        assert "file not found" in result["files"][1]["error"]

    async def test_read_files_rejects_empty_list(self, patch_auth):
        with pytest.raises(ToolError, match="non-empty"):
            await remote.remote_read_files(project_id="p", paths=[])

    async def test_read_files_rejects_oversized_batch(self, patch_auth):
        with pytest.raises(ToolError, match="batch limit"):
            await remote.remote_read_files(
                project_id="p", paths=[f"f{i}.txt" for i in range(25)],
            )

    async def test_read_files_rejects_invalid_format(self, patch_auth):
        with pytest.raises(ToolError, match="format"):
            await remote.remote_read_files(
                project_id="p", paths=["a.txt"], format="xml",
            )

    async def test_read_files_vs_individual_calls_size(self, patch_auth):
        """5-file batch text must be smaller than 5 individual JSON responses."""
        import json as _json

        payloads = [
            {"content": f"content of file {i}\n",
             "size": 20, "path": f"/work/file_{i}.txt",
             "encoding": "utf-8", "is_binary": False, "total_lines": 1,
             "truncated": False}
            for i in range(5)
        ]
        send_request = AsyncMock(side_effect=payloads)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            batch_text = await remote.remote_read_files(
                project_id="p", paths=[f"file_{i}.txt" for i in range(5)],
            )
        # Simulated sum of 5 individual dict responses
        individual_json_total = sum(len(_json.dumps(p)) for p in payloads)
        assert len(batch_text) < individual_json_total


class TestRemoteExecBatch:
    async def test_exec_batch_concatenates_blocks(self, patch_auth):
        payloads = [
            {"exit_code": 0, "stdout": "hello\n", "stderr": ""},
            {"exit_code": 0, "stdout": "world\n", "stderr": ""},
        ]
        send_request = AsyncMock(side_effect=payloads)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec_batch(
                project_id="p", commands=["echo hello", "echo world"],
            )
        assert result == (
            "$ echo hello\nhello\n"
            "$ echo world\nworld\n"
        )

    async def test_exec_batch_stop_on_error(self, patch_auth):
        payloads = [
            {"exit_code": 1, "stdout": "", "stderr": "boom\n"},
            # second command should NOT be called
        ]
        send_request = AsyncMock(side_effect=payloads)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec_batch(
                project_id="p", commands=["false", "echo skipped"],
                stop_on_error=True, format="json",
            )
        assert result["count"] == 1
        assert result["stopped"] is True

    async def test_exec_batch_continues_on_error_by_default(self, patch_auth):
        payloads = [
            {"exit_code": 1, "stdout": "", "stderr": "boom\n"},
            {"exit_code": 0, "stdout": "ok\n", "stderr": ""},
        ]
        send_request = AsyncMock(side_effect=payloads)
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_exec_batch(
                project_id="p", commands=["false", "true"],
                format="json",
            )
        assert result["count"] == 2
        assert result["stopped"] is False

    async def test_exec_batch_rejects_empty_list(self, patch_auth):
        with pytest.raises(ToolError, match="non-empty"):
            await remote.remote_exec_batch(project_id="p", commands=[])

    async def test_exec_batch_rejects_oversized(self, patch_auth):
        with pytest.raises(ToolError, match="batch limit"):
            await remote.remote_exec_batch(
                project_id="p",
                commands=[f"echo {i}" for i in range(15)],
            )

    async def test_exec_batch_rejects_invalid_command(self, patch_auth):
        """Security passthrough: the same validators reject bad commands."""
        with pytest.raises(ToolError, match="Invalid"):
            await remote.remote_exec_batch(
                project_id="p", commands=["ls\nrm -rf /"],
            )

    async def test_exec_batch_rejects_invalid_format(self, patch_auth):
        with pytest.raises(ToolError, match="format"):
            await remote.remote_exec_batch(
                project_id="p", commands=["ls"], format="xml",
            )


class TestRemoteEditFile:
    async def test_edit_passes_payload_to_agent(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True,
            "path": "/work/src/main.py",
            "replacements": 1,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_edit_file(
                project_id="p",
                path="src/main.py",
                old_string="return 1",
                new_string="return 42",
                format="json",
            )

        args, _kwargs = send_request.call_args
        payload = args[2]
        assert payload["path"] == "src/main.py"
        assert payload["cwd"] == "/work"
        assert payload["old_string"] == "return 1"
        assert payload["new_string"] == "return 42"
        assert payload["replace_all"] is False
        assert result["success"] is True
        assert result["replacements"] == 1

    async def test_edit_replace_all_flag(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True,
            "path": "/work/data.txt",
            "replacements": 5,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_edit_file(
                project_id="p",
                path="data.txt",
                old_string="foo",
                new_string="bar",
                replace_all=True,
                format="json",
            )

        args, _ = send_request.call_args
        assert args[2]["replace_all"] is True
        assert result["replacements"] == 5

    async def test_edit_rejects_empty_old_string(self, patch_auth):
        with pytest.raises(ToolError, match="required"):
            await remote.remote_edit_file(
                project_id="p", path="x.txt",
                old_string="", new_string="something",
            )

    async def test_edit_rejects_same_strings(self, patch_auth):
        with pytest.raises(ToolError, match="must differ"):
            await remote.remote_edit_file(
                project_id="p", path="x.txt",
                old_string="same", new_string="same",
            )

    async def test_edit_agent_error_raises_tool_error(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": False,
            "error": "old_string not found in file",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            with pytest.raises(ToolError, match="not found"):
                await remote.remote_edit_file(
                    project_id="p", path="x.txt",
                    old_string="missing", new_string="whatever",
                )

    async def test_edit_rejects_path_traversal(self, patch_auth):
        with pytest.raises(ToolError, match="traversal"):
            await remote.remote_edit_file(
                project_id="p", path="../../../etc/passwd",
                old_string="root", new_string="hacked",
            )

    # ── format="text" (default): single-line confirmation

    async def test_edit_text_single_replacement(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True, "path": "/work/x.txt", "replacements": 1,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_edit_file(
                project_id="p", path="x.txt",
                old_string="a", new_string="b",
            )
        assert result == "edited /work/x.txt\n"

    async def test_edit_text_multiple_replacements(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True, "path": "/work/x.txt", "replacements": 3,
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_edit_file(
                project_id="p", path="x.txt",
                old_string="a", new_string="b", replace_all=True,
            )
        assert result == "edited /work/x.txt (3 replacements)\n"

    async def test_edit_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_edit_file(
                    project_id="p", path="x.txt",
                    old_string="a", new_string="b", format="xml",
                )


# ──────────────────────────────────────────────
# remote_write_file
# ──────────────────────────────────────────────


class TestRemoteWriteFile:
    async def test_write_text_format_is_default(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True, "bytes_written": 1234, "path": "/work/f.txt",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_write_file(
                project_id="p", path="f.txt", content="hello",
            )
        assert isinstance(result, str)
        assert result == "wrote 1234 bytes to /work/f.txt\n"

    async def test_write_json_format_returns_dict(self, patch_auth):
        send_request = AsyncMock(return_value={
            "success": True, "bytes_written": 5, "path": "/work/f.txt",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            result = await remote.remote_write_file(
                project_id="p", path="f.txt", content="hello",
                format="json",
            )
        assert isinstance(result, dict)
        assert result["bytes_written"] == 5
        assert result["success"] is True

    async def test_write_rejects_invalid_format(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match="format"):
                await remote.remote_write_file(
                    project_id="p", path="f.txt", content="x", format="xml",
                )

    async def test_write_text_reduction_vs_json(self, patch_auth):
        """Text response is meaningfully smaller than JSON.

        The path echo keeps parity with local Write's output, so the
        reduction is path-length-dominated (≈30% for long paths, higher
        for short ones).
        """
        import json as _json

        send_request = AsyncMock(return_value={
            "success": True, "bytes_written": 45812, "path": "/work/f.txt",
        })
        with patch("app.mcp.tools.remote.agent_manager.send_request", send_request):
            text_result = await remote.remote_write_file(
                project_id="p", path="f.txt", content="x" * 100,
            )
            json_result = await remote.remote_write_file(
                project_id="p", path="f.txt", content="x" * 100,
                format="json",
            )
        json_bytes = len(_json.dumps(json_result))
        text_bytes = len(text_result)
        # Text must be at least 30% smaller than JSON. A short path like
        # ``/work/f.txt`` gives ≈50%; realistic absolute paths give ≈30%.
        assert text_bytes <= json_bytes * 0.7, (
            f"text={text_bytes}B json={json_bytes}B (text must be ≤70% of json)"
        )
