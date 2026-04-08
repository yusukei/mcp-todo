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

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from app.mcp.tools import remote


_MOCK_KEY_INFO = {"key_id": "test-key", "project_scopes": []}


def _fake_workspace():
    ws = MagicMock()
    ws.id = "ws-1"
    ws.agent_id = "agent-1"
    ws.remote_path = "/work"
    return ws


@pytest.fixture
def patch_auth():
    """Patch authenticate + _resolve_workspace + _log_operation."""
    workspace = _fake_workspace()
    with patch("app.mcp.tools.remote.authenticate", new=AsyncMock(return_value=_MOCK_KEY_INFO)), \
         patch("app.mcp.tools.remote._resolve_workspace", new=AsyncMock(return_value=workspace)), \
         patch("app.mcp.tools.remote._log_operation", new=AsyncMock(return_value=None)):
        yield workspace


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
            with pytest.raises(ToolError, match=">= 1"):
                await remote.remote_read_file(project_id="p", path="x.txt", offset=0)

    async def test_read_rejects_negative_limit(self, patch_auth):
        with patch("app.mcp.tools.remote.agent_manager.send_request", new=AsyncMock()):
            with pytest.raises(ToolError, match=">= 0"):
                await remote.remote_read_file(project_id="p", path="x.txt", limit=-5)


# ──────────────────────────────────────────────
# remote_stat / remote_file_exists
# ──────────────────────────────────────────────


class TestRemoteStat:
    async def test_stat_returns_metadata(self, patch_auth):
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
        with pytest.raises(ToolError, match="pattern is required"):
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
        from app.models.terminal import TerminalAgent

        agent = TerminalAgent(
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
        assert "workspace_count" in entry
        assert "is_online" in entry

    async def test_handles_agent_without_version(self, admin_user):
        """Older agents (pre-self-update build) report agent_version=None."""
        from app.core.security import hash_api_key
        from app.models.terminal import TerminalAgent

        agent = TerminalAgent(
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

