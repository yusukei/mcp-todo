"""Chat session REST API integration tests."""

import pytest

from app.models.chat import ChatMessage, ChatSession, MessageRole, MessageStatus, SessionStatus


BASE = "/api/v1/chat"


class TestCreateSession:
    async def test_create_session(self, client, admin_user, admin_headers, test_project):
        resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id), "title": "Test Chat"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "Test Chat"
        assert body["project_id"] == str(test_project.id)
        assert body["status"] == "idle"
        assert body["claude_session_id"] is None

    async def test_create_session_auto_title(self, client, admin_user, admin_headers, test_project):
        resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["title"].startswith("Chat ")

    async def test_create_session_nonmember_rejected(self, client, test_project):
        """Users not in project cannot create sessions."""
        from app.models.user import AuthType, User
        from app.core.security import create_access_token, hash_password
        outsider = User(
            email="outsider@test.com", name="Outsider",
            auth_type=AuthType.admin, password_hash=hash_password("pass"),
            is_admin=False, is_active=True,
        )
        await outsider.insert()
        token = create_access_token(str(outsider.id))

        resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    async def test_create_session_invalid_project(self, client, admin_user, admin_headers):
        resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": "000000000000000000000000"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_unauthenticated_rejected(self, client):
        resp = await client.post(f"{BASE}/sessions", json={"project_id": "x"})
        assert resp.status_code == 401


class TestListSessions:
    async def test_list_sessions(self, client, admin_user, admin_headers, test_project):
        # Create 2 sessions
        for i in range(2):
            await client.post(
                f"{BASE}/sessions",
                json={"project_id": str(test_project.id), "title": f"Chat {i}"},
                headers=admin_headers,
            )

        resp = await client.get(
            f"{BASE}/sessions",
            params={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2

    async def test_list_sessions_empty(self, client, admin_user, admin_headers, test_project):
        resp = await client.get(
            f"{BASE}/sessions",
            params={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json() == []



class TestListSessionsMembership:
    """list_sessions must filter by user's project membership when no project_id is given (S-7)."""

    async def test_no_project_id_returns_only_member_projects(
        self, client, admin_user, regular_user, user_headers, test_project
    ):
        # Create a session in test_project (regular_user is a member)
        from app.models.chat import ChatSession
        s1 = ChatSession(
            project_id=str(test_project.id), title="Visible", created_by=str(regular_user.id)
        )
        await s1.insert()

        # Create a second project the regular_user is NOT a member of, with a session
        from app.models import Project
        from app.models.project import ProjectMember
        other = Project(
            name="Other",
            description="",
            color="#000000",
            created_by=admin_user,
            members=[ProjectMember(user_id=str(admin_user.id))],
        )
        await other.insert()
        s2 = ChatSession(
            project_id=str(other.id), title="Hidden", created_by=str(admin_user.id)
        )
        await s2.insert()

        resp = await client.get(f"{BASE}/sessions", headers=user_headers)
        assert resp.status_code == 200
        items = resp.json()
        titles = {item["title"] for item in items}
        assert "Visible" in titles
        assert "Hidden" not in titles

    async def test_no_project_id_admin_sees_all(
        self, client, admin_user, regular_user, admin_headers, test_project
    ):
        # Two projects with one session each — admin should see both
        from app.models import Project
        from app.models.chat import ChatSession
        from app.models.project import ProjectMember

        other = Project(
            name="Other",
            description="",
            color="#000000",
            created_by=regular_user,
            members=[ProjectMember(user_id=str(regular_user.id))],
        )
        await other.insert()

        s1 = ChatSession(project_id=str(test_project.id), title="A", created_by=str(admin_user.id))
        s2 = ChatSession(project_id=str(other.id), title="B", created_by=str(regular_user.id))
        await s1.insert()
        await s2.insert()

        resp = await client.get(f"{BASE}/sessions", headers=admin_headers)
        assert resp.status_code == 200
        titles = {item["title"] for item in resp.json()}
        assert {"A", "B"}.issubset(titles)

    async def test_no_project_id_no_memberships_returns_empty(
        self, client, regular_user, user_headers
    ):
        # User is not a member of any project — list_sessions returns []
        from app.models.chat import ChatSession
        # Even if a stray session exists in some other project, the user should see []
        s = ChatSession(
            project_id="69bfffad73ed736a9d13fd0f", title="X", created_by="someone-else"
        )
        await s.insert()

        resp = await client.get(f"{BASE}/sessions", headers=user_headers)
        assert resp.status_code == 200
        assert resp.json() == []


class TestGetSession:
    async def test_get_session(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id), "title": "Get Test"},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        resp = await client.get(f"{BASE}/sessions/{session_id}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get Test"

    async def test_get_session_not_found(self, client, admin_user, admin_headers):
        resp = await client.get(f"{BASE}/sessions/000000000000000000000000", headers=admin_headers)
        assert resp.status_code == 404


class TestUpdateSession:
    async def test_update_title(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id), "title": "Old Title"},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/sessions/{session_id}",
            json={"title": "New Title"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"

    async def test_update_model(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        resp = await client.patch(
            f"{BASE}/sessions/{session_id}",
            json={"model": "sonnet"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["model"] == "sonnet"


class TestDeleteSession:
    async def test_delete_session(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        # Add a message to verify cascade delete
        msg = ChatMessage(
            session_id=session_id,
            role=MessageRole.user,
            content="test message",
        )
        await msg.insert()

        resp = await client.delete(f"{BASE}/sessions/{session_id}", headers=admin_headers)
        assert resp.status_code == 204

        # Verify session gone
        resp = await client.get(f"{BASE}/sessions/{session_id}", headers=admin_headers)
        assert resp.status_code == 404

        # Verify messages also deleted
        remaining = await ChatMessage.find({"session_id": session_id}).count()
        assert remaining == 0

    async def test_delete_nonexistent(self, client, admin_user, admin_headers):
        resp = await client.delete(f"{BASE}/sessions/000000000000000000000000", headers=admin_headers)
        assert resp.status_code == 404


class TestGetMessages:
    async def test_get_messages(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        # Insert messages directly
        for i in range(3):
            role = MessageRole.user if i % 2 == 0 else MessageRole.assistant
            await ChatMessage(
                session_id=session_id,
                role=role,
                content=f"Message {i}",
            ).insert()

        resp = await client.get(
            f"{BASE}/sessions/{session_id}/messages",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert body["items"][0]["content"] == "Message 0"

    async def test_get_messages_pagination(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        for i in range(5):
            await ChatMessage(
                session_id=session_id,
                role=MessageRole.user,
                content=f"Msg {i}",
            ).insert()

        resp = await client.get(
            f"{BASE}/sessions/{session_id}/messages",
            params={"limit": 2, "skip": 1},
            headers=admin_headers,
        )
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["items"][0]["content"] == "Msg 1"

    async def test_get_messages_with_tool_calls(self, client, admin_user, admin_headers, test_project):
        create_resp = await client.post(
            f"{BASE}/sessions",
            json={"project_id": str(test_project.id)},
            headers=admin_headers,
        )
        session_id = create_resp.json()["id"]

        from app.models.chat import ToolCallData
        await ChatMessage(
            session_id=session_id,
            role=MessageRole.assistant,
            content="I'll read the file",
            tool_calls=[
                ToolCallData(tool_name="Read", input={"file_path": "/app/main.py"}, output="contents..."),
            ],
            cost_usd=0.05,
            duration_ms=3000,
        ).insert()

        resp = await client.get(
            f"{BASE}/sessions/{session_id}/messages",
            headers=admin_headers,
        )
        msg = resp.json()["items"][0]
        assert msg["role"] == "assistant"
        assert len(msg["tool_calls"]) == 1
        assert msg["tool_calls"][0]["tool_name"] == "Read"
        assert msg["cost_usd"] == 0.05

    async def test_get_messages_nonexistent_session(self, client, admin_user, admin_headers):
        resp = await client.get(
            f"{BASE}/sessions/000000000000000000000000/messages",
            headers=admin_headers,
        )
        assert resp.status_code == 404


class TestChatConnectionManager:
    """Unit tests for ChatConnectionManager."""

    def test_connect_and_disconnect(self):
        from app.services.chat_manager import ChatConnectionManager
        from unittest.mock import MagicMock

        mgr = ChatConnectionManager()
        ws1 = MagicMock()
        ws2 = MagicMock()

        mgr.connect("s1", ws1)
        mgr.connect("s1", ws2)
        assert mgr.connection_count("s1") == 2

        mgr.disconnect("s1", ws1)
        assert mgr.connection_count("s1") == 1

        mgr.disconnect("s1", ws2)
        assert mgr.connection_count("s1") == 0
        assert "s1" not in mgr._connections

    async def test_broadcast(self):
        from app.services.chat_manager import ChatConnectionManager
        from unittest.mock import AsyncMock

        mgr = ChatConnectionManager()
        ws1 = AsyncMock()
        ws2 = AsyncMock()

        mgr.connect("s1", ws1)
        mgr.connect("s1", ws2)

        await mgr.broadcast("s1", {"type": "test", "data": "hello"})

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

    async def test_broadcast_removes_disconnected(self):
        from app.services.chat_manager import ChatConnectionManager
        from unittest.mock import AsyncMock

        mgr = ChatConnectionManager()
        ws_ok = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = Exception("disconnected")

        mgr.connect("s1", ws_ok)
        mgr.connect("s1", ws_dead)

        await mgr.broadcast("s1", {"type": "test"})

        assert mgr.connection_count("s1") == 1


class TestHandleChatEvent:
    """Tests for stream event processing."""

    async def test_text_delta_creates_message(self):
        from app.services.chat_events import _process_stream_event
        from app.services.chat_manager import chat_manager
        from unittest.mock import AsyncMock

        # Create a session
        session = ChatSession(project_id="proj1", title="test")
        await session.insert()
        session_id = str(session.id)

        # Mock broadcast
        ws = AsyncMock()
        chat_manager.connect(session_id, ws)

        try:
            # First assistant event should create the streaming message.
            # claude CLI v2 emits SDKMessage-shaped events: each `assistant`
            # event is a complete message snapshot for one turn.
            await _process_stream_event(session_id, {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Hello "}],
                },
            })

            msgs = await ChatMessage.find({"session_id": session_id}).to_list()
            assert len(msgs) == 1
            assert msgs[0].role == MessageRole.assistant
            assert msgs[0].status == MessageStatus.streaming
            assert msgs[0].content == "Hello "

            # A second assistant event in the same turn appends to the
            # same streaming message (multi-turn case: text → tool_use →
            # tool_result → text).
            await _process_stream_event(session_id, {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "world!"}],
                },
            })

            msgs = await ChatMessage.find({"session_id": session_id}).to_list()
            assert len(msgs) == 1
            assert msgs[0].content == "Hello world!"
        finally:
            chat_manager.disconnect(session_id, ws)

    async def test_handle_chat_complete(self):
        from app.services.chat_events import handle_chat_event
        from app.services.chat_manager import chat_manager
        from unittest.mock import AsyncMock

        session = ChatSession(project_id="proj1", title="test", status=SessionStatus.busy)
        await session.insert()
        session_id = str(session.id)

        # Create streaming message
        msg = ChatMessage(
            session_id=session_id,
            role=MessageRole.assistant,
            content="Done!",
            status=MessageStatus.streaming,
        )
        await msg.insert()

        ws = AsyncMock()
        chat_manager.connect(session_id, ws)

        try:
            await handle_chat_event({
                "type": "chat_complete",
                "session_id": session_id,
                "claude_session_id": "claude-abc123",
                "cost_usd": 0.03,
                "duration_ms": 2000,
            })

            # Session should be idle with claude_session_id saved
            session = await ChatSession.get(session.id)
            assert session.status == SessionStatus.idle
            assert session.claude_session_id == "claude-abc123"

            # Message should be complete with cost
            msg = await ChatMessage.get(msg.id)
            assert msg.status == MessageStatus.complete
            assert msg.cost_usd == 0.03
        finally:
            chat_manager.disconnect(session_id, ws)
