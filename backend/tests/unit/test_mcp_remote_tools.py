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
            result = await remote.remote_exec(project_id="p", command="echo hi")

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
            result = await remote.remote_exec(project_id="p", command="cat huge.log")
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
            result = await remote.remote_read_file(
                project_id="p", path="blob.bin", encoding="binary",
            )

        args, _ = send_request.call_args
        assert args[2]["encoding"] == "binary"
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
            )
        args, _ = send_request.call_args
        assert args[2]["pattern"] == "**/*.py"
        assert result["count"] == 1


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
