"""Tests for ResilientSessionManager (app/mcp/session_manager.py).

Verifies the session recovery logic that allows clients to reconnect
after container restarts without receiving 404 errors.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from app.mcp.session_manager import ResilientSessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scope(session_id: str | None = None) -> dict:
    """Build a minimal ASGI scope with optional Mcp-Session-Id header."""
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json"),
    ]
    if session_id is not None:
        headers.append((b"mcp-session-id", session_id.encode()))
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "query_string": b"",
        "headers": headers,
    }


def _make_manager(**overrides) -> ResilientSessionManager:
    """Create a ResilientSessionManager with fully-mocked internals."""
    app = MagicMock()
    app.run = AsyncMock()
    app.create_initialization_options = MagicMock(return_value={})

    defaults = dict(
        app=app,
        event_store=MagicMock(),
        json_response=False,
        stateless=False,
        security_settings=None,
        retry_interval=None,
    )
    defaults.update(overrides)
    mgr = ResilientSessionManager(**defaults)
    return mgr


def _mock_transport(session_id: str = "test-session") -> MagicMock:
    """Create a mock StreamableHTTPServerTransport."""
    transport = MagicMock()
    transport.mcp_session_id = session_id
    transport.is_terminated = False
    transport.handle_request = AsyncMock()
    transport.connect = MagicMock()
    return transport


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestKnownSession:
    """When a request carries a session ID that already exists in _server_instances."""

    @pytest.mark.asyncio
    async def test_delegates_to_existing_transport(self):
        """Known session ID delegates directly to the existing transport."""
        mgr = _make_manager()
        existing_transport = _mock_transport("known-session-123")
        mgr._server_instances["known-session-123"] = existing_transport

        scope = _make_scope("known-session-123")
        receive = AsyncMock()
        send = AsyncMock()

        await mgr._handle_stateful_request(scope, receive, send)

        existing_transport.handle_request.assert_awaited_once_with(
            scope, receive, send,
        )

    @pytest.mark.asyncio
    async def test_does_not_create_new_transport(self):
        """Known session does not instantiate a new transport."""
        mgr = _make_manager()
        existing_transport = _mock_transport("known-session")
        mgr._server_instances["known-session"] = existing_transport
        mgr._session_creation_lock = anyio.Lock()

        scope = _make_scope("known-session")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport"
        ) as mock_cls:
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())
            mock_cls.assert_not_called()


class TestUnknownSessionRecovery:
    """When a request carries a session ID that is NOT in _server_instances
    (e.g. after a container restart)."""

    @pytest.mark.asyncio
    async def test_creates_transport_with_same_session_id(self):
        """Recovery re-creates a transport bound to the original session ID."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("recovered-id")

        # Make connect() return an async context manager yielding stream pair
        read_stream, write_stream = MagicMock(), MagicMock()

        @asynccontextmanager
        async def fake_connect():
            yield (read_stream, write_stream)

        mock_transport.connect = fake_connect

        # Mock task_group.start to invoke the callback immediately
        async def fake_start(fn):
            # We don't actually run the server; just signal started
            pass

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=fake_start)
        mgr._task_group = task_group

        scope = _make_scope("recovered-id")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ) as mock_cls:
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

            mock_cls.assert_called_once_with(
                mcp_session_id="recovered-id",
                is_json_response_enabled=False,
                event_store=mgr.event_store,
                security_settings=None,
                retry_interval=None,
            )

    @pytest.mark.asyncio
    async def test_logs_recovery_message(self, caplog):
        """Recovery path logs an info message about re-creating transport."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("sess-abc")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope("sess-abc")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            with caplog.at_level(logging.INFO, logger="app.mcp.session_manager"):
                await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert any(
            "Unknown session sess-abc" in record.message
            and "re-creating transport" in record.message
            for record in caplog.records
        ), f"Expected recovery log message, got: {[r.message for r in caplog.records]}"

    @pytest.mark.asyncio
    async def test_recovery_sets_stateless_true(self):
        """Recovery sessions start with stateless=True so clients skip re-initialization."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("sess-recovery")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        # Capture the run_server coroutine passed to task_group.start
        captured_fn = None

        async def capture_start(fn):
            nonlocal captured_fn
            captured_fn = fn

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=capture_start)
        mgr._task_group = task_group

        scope = _make_scope("sess-recovery")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        # Now execute the captured run_server to check app.run args
        assert captured_fn is not None, "run_server should have been captured"

        # Create a mock task_status for the run_server call
        task_status = MagicMock()
        task_status.started = MagicMock()
        await captured_fn(task_status=task_status)

        mgr.app.run.assert_awaited_once()
        call_kwargs = mgr.app.run.call_args
        assert call_kwargs.kwargs.get("stateless") is True, (
            "Recovery sessions must pass stateless=True"
        )

    @pytest.mark.asyncio
    async def test_transport_registered_in_server_instances(self):
        """The recovered transport is stored in _server_instances."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("sess-register")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope("sess-register")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert "sess-register" in mgr._server_instances
        assert mgr._server_instances["sess-register"] is mock_transport


class TestNewSession:
    """When a request has no session ID header (fresh connection)."""

    @pytest.mark.asyncio
    async def test_creates_transport_with_generated_uuid(self):
        """No session ID creates a transport with a new UUID."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("new-uuid-hex")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope(session_id=None)  # No session header

        fake_uuid = MagicMock()
        fake_uuid.hex = "deadbeef1234567890abcdef12345678"

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ) as mock_cls:
            with patch("uuid.uuid4", return_value=fake_uuid):
                # Override mcp_session_id to match what uuid4 would produce
                mock_transport.mcp_session_id = "deadbeef1234567890abcdef12345678"
                await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

            mock_cls.assert_called_once_with(
                mcp_session_id="deadbeef1234567890abcdef12345678",
                is_json_response_enabled=False,
                event_store=mgr.event_store,
                security_settings=None,
                retry_interval=None,
            )

    @pytest.mark.asyncio
    async def test_does_not_log_recovery_message(self, caplog):
        """New session (no header) does NOT log the recovery message."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("fresh-sess")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope(session_id=None)

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            with caplog.at_level(logging.INFO, logger="app.mcp.session_manager"):
                await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert not any(
            "Unknown session" in record.message
            for record in caplog.records
        ), "New session should not trigger recovery log"

    @pytest.mark.asyncio
    async def test_new_session_sets_stateless_false(self):
        """New sessions start with stateless=False (require initialization)."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("new-sess")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        captured_fn = None

        async def capture_start(fn):
            nonlocal captured_fn
            captured_fn = fn

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=capture_start)
        mgr._task_group = task_group

        scope = _make_scope(session_id=None)

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert captured_fn is not None
        task_status = MagicMock()
        task_status.started = MagicMock()
        await captured_fn(task_status=task_status)

        mgr.app.run.assert_awaited_once()
        call_kwargs = mgr.app.run.call_args
        assert call_kwargs.kwargs.get("stateless") is False, (
            "New sessions must pass stateless=False"
        )


class TestDoubleCheckUnderLock:
    """Concurrent requests for the same unknown session ID should not
    create duplicate transports thanks to the double-check pattern."""

    @pytest.mark.asyncio
    async def test_second_request_uses_transport_created_by_first(self):
        """After the lock is acquired, a second check finds the transport
        already created by a concurrent request."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        # Simulate: first request already created the transport while
        # second was waiting for the lock
        existing_transport = _mock_transport("dup-session")

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope("dup-session")

        # The session ID is NOT in _server_instances when the method starts
        # (to enter the recovery branch), but appears inside the lock
        call_count = 0
        original_contains = mgr._server_instances.__contains__

        # We need to intercept the flow: first __contains__ check returns False,
        # second (inside lock) returns True
        def patched_contains(key):
            nonlocal call_count
            call_count += 1
            if key == "dup-session" and call_count <= 1:
                return False  # First check (before lock)
            if key == "dup-session":
                return True  # Second check (inside lock)
            return original_contains(key)

        mgr._server_instances["dup-session"] = existing_transport
        # Override __contains__ to simulate the race
        mgr._server_instances = type(
            "FakeDict", (dict,), {"__contains__": lambda self, k: patched_contains(k)}
        )(mgr._server_instances)
        # But keep getitem working
        mgr._server_instances["dup-session"] = existing_transport

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport"
        ) as mock_cls:
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

            # No new transport should be created
            mock_cls.assert_not_called()

        # The existing transport should have been used
        existing_transport.handle_request.assert_awaited_once()


class TestSessionCrashCleanup:
    """The run_server finally-block should remove the transport from
    _server_instances when a crash occurs."""

    @pytest.mark.asyncio
    async def test_crash_removes_transport_from_instances(self):
        """When app.run raises, the transport is removed from _server_instances."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        # Make app.run raise an exception
        mgr.app.run = AsyncMock(side_effect=RuntimeError("boom"))

        mock_transport = _mock_transport("crash-sess")
        mock_transport.is_terminated = False

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        captured_fn = None

        async def capture_start(fn):
            nonlocal captured_fn
            captured_fn = fn

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=capture_start)
        mgr._task_group = task_group

        scope = _make_scope("crash-sess")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        # Verify the transport was registered
        assert "crash-sess" in mgr._server_instances

        # Now execute the run_server — it should crash and clean up
        assert captured_fn is not None
        task_status = MagicMock()
        task_status.started = MagicMock()
        await captured_fn(task_status=task_status)

        # After crash, transport should be removed
        assert "crash-sess" not in mgr._server_instances

    @pytest.mark.asyncio
    async def test_terminated_transport_not_removed(self):
        """If transport.is_terminated is True, the finally block does NOT
        remove it (graceful termination handled elsewhere)."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mgr.app.run = AsyncMock(side_effect=RuntimeError("boom"))

        mock_transport = _mock_transport("term-sess")
        mock_transport.is_terminated = True  # Already terminated

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        captured_fn = None

        async def capture_start(fn):
            nonlocal captured_fn
            captured_fn = fn

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=capture_start)
        mgr._task_group = task_group

        scope = _make_scope("term-sess")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert "term-sess" in mgr._server_instances

        # Execute the run_server
        assert captured_fn is not None
        task_status = MagicMock()
        task_status.started = MagicMock()
        await captured_fn(task_status=task_status)

        # Should NOT be removed because is_terminated is True
        assert "term-sess" in mgr._server_instances

    @pytest.mark.asyncio
    async def test_crash_logs_error(self, caplog):
        """When app.run raises, the error is logged."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mgr.app.run = AsyncMock(side_effect=ValueError("test error"))

        mock_transport = _mock_transport("log-sess")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        captured_fn = None

        async def capture_start(fn):
            nonlocal captured_fn
            captured_fn = fn

        task_group = MagicMock()
        task_group.start = AsyncMock(side_effect=capture_start)
        mgr._task_group = task_group

        scope = _make_scope("log-sess")

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, AsyncMock(), AsyncMock())

        assert captured_fn is not None
        task_status = MagicMock()
        task_status.started = MagicMock()

        with caplog.at_level(logging.ERROR, logger="app.mcp.session_manager"):
            await captured_fn(task_status=task_status)

        assert any(
            "log-sess" in record.message and "crashed" in record.message
            for record in caplog.records
        ), f"Expected crash log, got: {[r.message for r in caplog.records]}"


class TestHandleRequestDelegation:
    """Verify that handle_request (the outer method) calls the inner handler
    after the transport is created."""

    @pytest.mark.asyncio
    async def test_handle_request_calls_transport_handle(self):
        """After setup, handle_request is called on the transport."""
        mgr = _make_manager()
        mgr._session_creation_lock = anyio.Lock()

        mock_transport = _mock_transport("handle-sess")

        @asynccontextmanager
        async def fake_connect():
            yield (MagicMock(), MagicMock())

        mock_transport.connect = fake_connect

        task_group = MagicMock()
        task_group.start = AsyncMock()
        mgr._task_group = task_group

        scope = _make_scope("handle-sess")
        receive = AsyncMock()
        send = AsyncMock()

        with patch(
            "app.mcp.session_manager.StreamableHTTPServerTransport",
            return_value=mock_transport,
        ):
            await mgr._handle_stateful_request(scope, receive, send)

        mock_transport.handle_request.assert_awaited_once_with(scope, receive, send)
