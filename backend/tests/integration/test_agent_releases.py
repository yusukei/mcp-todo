"""Integration tests for agent release upload / list / download endpoints
and the version-comparison helpers used by the WebSocket update push.

The tests cover three layers:

1. Pure helper functions (``_parse_version_tuple``, ``_is_newer``,
   ``_find_latest_release``) — these power the decision of *whether* an
   agent should be told to update.
2. Admin REST endpoints — upload, list, delete.
3. Agent-token-authenticated endpoints — ``releases/latest`` and the
   download stream, which is what the running agent talks to.

The actual WebSocket push is harder to exercise here (the test app
mounts routers but not WS endpoints in a fully driveable way), so the
push side is unit-tested via ``_maybe_push_update`` indirectly through
``_find_latest_release`` + ``_is_newer``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.api.v1.endpoints import workspaces as terminal_module
from app.api.v1.endpoints.workspaces import (
    _find_latest_release,
    _is_newer,
    _parse_version_tuple,
)
from app.core.security import hash_api_key
from app.models import AgentRelease, RemoteAgent


# ──────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────


def _make_binary_payload(marker: str = "agent", size: int = 256) -> tuple[bytes, str]:
    """Return ``(bytes, sha256_hex)`` for a deterministic fake binary."""
    payload = (marker.encode("ascii") * (size // len(marker) + 1))[:size]
    return payload, hashlib.sha256(payload).hexdigest()


@pytest.fixture(autouse=True)
def isolate_releases_dir(tmp_path, monkeypatch):
    """Point AGENT_RELEASES_DIR at a per-test temporary directory.

    Without this the upload endpoint would write into the real repo's
    ``agent_releases`` directory, which is shared across tests and
    would persist binaries between runs.
    """
    monkeypatch.setattr(
        terminal_module.settings,
        "AGENT_RELEASES_DIR",
        str(tmp_path / "agent_releases"),
    )
    yield


# ──────────────────────────────────────────────
# version helpers
# ──────────────────────────────────────────────


class TestVersionHelpers:
    @pytest.mark.parametrize(
        "version,expected",
        [
            ("0.1.0", (0, 1, 0)),
            ("1.2.3", (1, 2, 3)),
            ("0.2.0-beta.1", (0, 2, 0)),
            ("2.0.0+build.99", (2, 0, 0)),
            ("0.10.0", (0, 10, 0)),
            ("", ()),
            (None, ()),
            ("not-a-version", (0,)),
        ],
    )
    def test_parse_version_tuple(self, version, expected):
        assert _parse_version_tuple(version) == expected

    def test_is_newer_basic(self):
        assert _is_newer("0.2.0", "0.1.0")
        assert _is_newer("0.10.0", "0.9.9")
        assert _is_newer("1.0.0", None)  # unknown current → always upgrade
        assert _is_newer("1.0.0", "")
        assert not _is_newer("0.1.0", "0.2.0")
        assert not _is_newer("1.0.0", "1.0.0")

    def test_pre_release_suffix_ignored(self):
        # Strict version comparison ignores -beta / +build qualifiers,
        # which is intentional for this MVP — we only care about the
        # numeric component.
        assert not _is_newer("0.2.0-beta.1", "0.2.0")
        assert not _is_newer("0.2.0", "0.2.0+build.99")


# ──────────────────────────────────────────────
# admin upload / list / delete
# ──────────────────────────────────────────────


class TestUploadRelease:
    async def test_admin_can_upload_release(self, client, admin_user, admin_headers):
        payload, digest = _make_binary_payload()
        files = {"file": ("agent.exe", payload, "application/octet-stream")}
        data = {
            "version": "0.2.0",
            "os_type": "win32",
            "channel": "stable",
            "arch": "x64",
            "release_notes": "Initial release",
        }
        resp = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=admin_headers
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["version"] == "0.2.0"
        assert body["os_type"] == "win32"
        assert body["channel"] == "stable"
        assert body["sha256"] == digest
        assert body["size_bytes"] == len(payload)
        assert body["release_notes"] == "Initial release"
        assert "download_url" in body

        # Stored on disk under AGENT_RELEASES_DIR
        rel = await AgentRelease.get(body["id"])
        assert rel is not None
        from app.core.config import settings
        on_disk = Path(settings.AGENT_RELEASES_DIR) / rel.storage_path
        assert on_disk.exists()
        assert on_disk.read_bytes() == payload

    async def test_duplicate_upload_rejected(self, client, admin_user, admin_headers):
        payload, _ = _make_binary_payload()
        data = {"version": "0.2.0", "os_type": "win32", "channel": "stable", "arch": "x64"}
        files = {"file": ("agent.exe", payload, "application/octet-stream")}
        first = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=admin_headers
        )
        assert first.status_code == 201

        files2 = {"file": ("agent.exe", payload, "application/octet-stream")}
        dup = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files2, headers=admin_headers
        )
        assert dup.status_code == 409

    async def test_invalid_os_type_rejected(self, client, admin_user, admin_headers):
        payload, _ = _make_binary_payload()
        data = {"version": "0.2.0", "os_type": "plan9", "channel": "stable"}
        files = {"file": ("agent.exe", payload, "application/octet-stream")}
        resp = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=admin_headers
        )
        assert resp.status_code == 422

    async def test_invalid_version_rejected(self, client, admin_user, admin_headers):
        payload, _ = _make_binary_payload()
        data = {"version": "abc-xyz", "os_type": "win32", "channel": "stable"}
        files = {"file": ("agent.exe", payload, "application/octet-stream")}
        resp = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=admin_headers
        )
        assert resp.status_code == 422

    async def test_empty_file_rejected(self, client, admin_user, admin_headers):
        data = {"version": "0.3.0", "os_type": "win32", "channel": "stable"}
        files = {"file": ("agent.exe", b"", "application/octet-stream")}
        resp = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=admin_headers
        )
        assert resp.status_code == 422

    async def test_regular_user_cannot_upload(self, client, regular_user, user_headers):
        payload, _ = _make_binary_payload()
        data = {"version": "0.2.0", "os_type": "win32", "channel": "stable"}
        files = {"file": ("agent.exe", payload, "application/octet-stream")}
        resp = await client.post(
            "/api/v1/workspaces/releases", data=data, files=files, headers=user_headers
        )
        assert resp.status_code == 403


class TestListAndDeleteRelease:
    async def test_list_filters_by_os_and_channel(self, client, admin_user, admin_headers):
        # Three releases across two OSes / two channels
        for version, os_type, channel in [
            ("0.2.0", "win32", "stable"),
            ("0.3.0", "win32", "beta"),
            ("0.2.0", "linux", "stable"),
        ]:
            payload, _ = _make_binary_payload(marker=f"{os_type}-{version}")
            await client.post(
                "/api/v1/workspaces/releases",
                data={"version": version, "os_type": os_type, "channel": channel},
                files={"file": ("agent.bin", payload, "application/octet-stream")},
                headers=admin_headers,
            )

        all_resp = await client.get("/api/v1/workspaces/releases", headers=admin_headers)
        assert all_resp.status_code == 200
        assert len(all_resp.json()) == 3

        win_stable = await client.get(
            "/api/v1/workspaces/releases?os_type=win32&channel=stable",
            headers=admin_headers,
        )
        body = win_stable.json()
        assert len(body) == 1
        assert body[0]["version"] == "0.2.0"
        assert body[0]["os_type"] == "win32"

    async def test_delete_release_removes_db_and_file(
        self, client, admin_user, admin_headers
    ):
        payload, _ = _make_binary_payload()
        upload = await client.post(
            "/api/v1/workspaces/releases",
            data={"version": "0.2.0", "os_type": "win32", "channel": "stable"},
            files={"file": ("agent.exe", payload, "application/octet-stream")},
            headers=admin_headers,
        )
        release_id = upload.json()["id"]

        rel = await AgentRelease.get(release_id)
        assert rel is not None
        from app.core.config import settings
        path = Path(settings.AGENT_RELEASES_DIR) / rel.storage_path
        assert path.exists()

        del_resp = await client.delete(
            f"/api/v1/workspaces/releases/{release_id}", headers=admin_headers
        )
        assert del_resp.status_code == 204
        assert await AgentRelease.get(release_id) is None
        assert not path.exists()


# ──────────────────────────────────────────────
# agent-facing endpoints
# ──────────────────────────────────────────────


@pytest.fixture
async def registered_agent(admin_user):
    """Register a RemoteAgent and return ``(agent, raw_token)``."""
    raw_token = "ta_unittesttoken_0123456789abcdef"
    agent = RemoteAgent(
        name="test-agent",
        key_hash=hash_api_key(raw_token),
        owner_id=str(admin_user.id),
        os_type="win32",
        update_channel="stable",
        agent_version="0.1.0",
    )
    await agent.insert()
    return agent, raw_token


class TestAgentReleaseEndpoints:
    async def test_get_latest_requires_token(self, client):
        resp = await client.get(
            "/api/v1/workspaces/releases/latest?os_type=win32&channel=stable"
        )
        assert resp.status_code == 401

    async def test_get_latest_returns_highest_version(
        self, client, admin_user, admin_headers, registered_agent
    ):
        agent, token = registered_agent
        for v in ("0.2.0", "0.10.0", "0.9.0"):
            payload, _ = _make_binary_payload(marker=f"v{v}")
            await client.post(
                "/api/v1/workspaces/releases",
                data={"version": v, "os_type": "win32", "channel": "stable"},
                files={"file": ("agent.exe", payload, "application/octet-stream")},
                headers=admin_headers,
            )

        resp = await client.get(
            "/api/v1/workspaces/releases/latest?os_type=win32&channel=stable",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "0.10.0"

    async def test_download_returns_payload_and_sha_header(
        self, client, admin_user, admin_headers, registered_agent
    ):
        agent, token = registered_agent
        payload, digest = _make_binary_payload(marker="downloadme")
        upload = await client.post(
            "/api/v1/workspaces/releases",
            data={"version": "0.2.0", "os_type": "win32", "channel": "stable"},
            files={"file": ("agent.exe", payload, "application/octet-stream")},
            headers=admin_headers,
        )
        release_id = upload.json()["id"]

        resp = await client.get(
            f"/api/v1/workspaces/releases/{release_id}/download",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.content == payload
        assert resp.headers["X-Agent-Release-Sha256"] == digest

    async def test_download_with_invalid_token_rejected(
        self, client, admin_user, admin_headers
    ):
        payload, _ = _make_binary_payload()
        upload = await client.post(
            "/api/v1/workspaces/releases",
            data={"version": "0.2.0", "os_type": "win32", "channel": "stable"},
            files={"file": ("agent.exe", payload, "application/octet-stream")},
            headers=admin_headers,
        )
        release_id = upload.json()["id"]

        resp = await client.get(
            f"/api/v1/workspaces/releases/{release_id}/download",
            headers={"Authorization": "Bearer ta_not_a_real_token"},
        )
        assert resp.status_code == 401


# ──────────────────────────────────────────────
# version comparison wired to find_latest_release
# ──────────────────────────────────────────────


class TestFindLatestRelease:
    async def test_returns_none_when_empty(self):
        result = await _find_latest_release("win32", "stable")
        assert result is None

    async def test_returns_highest_version_match(self, admin_user):
        for v in ("0.2.0", "0.3.0", "0.10.0", "1.0.0-beta"):
            await AgentRelease(
                version=v,
                os_type="win32",
                channel="stable",
                arch="x64",
                storage_path=f"win32/stable/x64/agent-{v}.exe",
                sha256="0" * 64,
                size_bytes=1024,
                uploaded_by=str(admin_user.id),
            ).insert()

        # 1.0.0-beta strips suffix → (1, 0, 0) which beats 0.10.0 → (0, 10, 0)
        result = await _find_latest_release("win32", "stable")
        assert result is not None
        assert result.version == "1.0.0-beta"

    async def test_filters_by_os_and_channel(self, admin_user):
        await AgentRelease(
            version="0.2.0",
            os_type="win32",
            channel="stable",
            arch="x64",
            storage_path="win32/stable/x64/a.exe",
            sha256="0" * 64,
            size_bytes=10,
            uploaded_by=str(admin_user.id),
        ).insert()
        await AgentRelease(
            version="0.5.0",
            os_type="linux",
            channel="stable",
            arch="x64",
            storage_path="linux/stable/x64/a.bin",
            sha256="0" * 64,
            size_bytes=10,
            uploaded_by=str(admin_user.id),
        ).insert()

        win = await _find_latest_release("win32", "stable")
        assert win is not None and win.os_type == "win32"
        nope = await _find_latest_release("darwin", "stable")
        assert nope is None


# ──────────────────────────────────────────────
# POST /workspaces/agents/{id}/check-update
# ──────────────────────────────────────────────


class _FakeWebSocket:
    """Minimal WebSocket stub compatible with agent_manager.send_raw()."""

    def __init__(self):
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


@pytest.fixture
def registered_agent_with_ws(admin_user, monkeypatch):
    """Create an agent record + pretend its WS is connected."""
    from app.core.security import hash_api_key
    from app.services.agent_manager import agent_manager

    async def _build(os_type="win32", channel="stable", agent_version="0.2.0", auto_update=True, os_reported=True):
        raw_token = "ta_checkupdate_token_0001"
        agent = RemoteAgent(
            name="check-update-host",
            key_hash=hash_api_key(raw_token),
            owner_id=str(admin_user.id),
            os_type=os_type if os_reported else "",
            update_channel=channel,
            agent_version=agent_version,
            auto_update=auto_update,
        )
        await agent.insert()
        fake_ws = _FakeWebSocket()
        agent_manager.register(str(agent.id), fake_ws)  # type: ignore[arg-type]
        return agent, fake_ws

    created: list[tuple] = []

    async def factory(**kw):
        res = await _build(**kw)
        created.append(res)
        return res

    yield factory

    # Cleanup: unregister any WS we registered.
    from app.services.agent_manager import agent_manager

    for agent, fake_ws in created:
        agent_manager.unregister(str(agent.id), fake_ws)  # type: ignore[arg-type]


async def _upload_release(client, admin_headers, version, os_type="win32"):
    payload, digest = _make_binary_payload(marker=f"rel-{version}")
    resp = await client.post(
        "/api/v1/workspaces/releases",
        data={"version": version, "os_type": os_type, "channel": "stable"},
        files={"file": (f"agent-{version}.exe", payload, "application/octet-stream")},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json(), digest


class TestCheckAgentUpdate:
    async def test_pushes_update_when_newer_release_exists(
        self, client, admin_user, admin_headers, registered_agent_with_ws
    ):
        agent, fake_ws = await registered_agent_with_ws()
        release_body, _ = await _upload_release(client, admin_headers, "0.3.0")

        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pushed"] is True
        assert body["version"] == "0.3.0"
        assert body["current"] == "0.2.0"
        assert body["release_id"] == release_body["id"]

        # The fake WebSocket should have received an update_available frame.
        assert len(fake_ws.sent) == 1
        import json as _json
        frame = _json.loads(fake_ws.sent[0])
        assert frame["type"] == "update_available"
        assert frame["version"] == "0.3.0"
        assert frame["release_id"] == release_body["id"]
        assert frame["sha256"] == release_body["sha256"]
        assert frame["size_bytes"] == release_body["size_bytes"]
        assert frame["download_url"].endswith(f"/releases/{release_body['id']}/download")

    async def test_noop_when_already_up_to_date(
        self, client, admin_user, admin_headers, registered_agent_with_ws
    ):
        agent, fake_ws = await registered_agent_with_ws(agent_version="0.3.0")
        await _upload_release(client, admin_headers, "0.3.0")

        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed"] is False
        assert body["reason"] == "already up to date"
        assert body["current"] == "0.3.0"
        assert body["latest"] == "0.3.0"
        assert fake_ws.sent == []

    async def test_noop_when_no_release_available(
        self, client, admin_user, admin_headers, registered_agent_with_ws
    ):
        agent, fake_ws = await registered_agent_with_ws()
        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed"] is False
        assert body["reason"] == "no release available"
        assert fake_ws.sent == []

    async def test_noop_when_auto_update_disabled(
        self, client, admin_user, admin_headers, registered_agent_with_ws
    ):
        agent, fake_ws = await registered_agent_with_ws(auto_update=False)
        await _upload_release(client, admin_headers, "0.3.0")
        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed"] is False
        assert body["reason"] == "auto_update disabled"

    async def test_noop_when_os_type_unknown(
        self, client, admin_user, admin_headers, registered_agent_with_ws
    ):
        agent, fake_ws = await registered_agent_with_ws(os_reported=False)
        await _upload_release(client, admin_headers, "0.3.0")
        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["pushed"] is False
        assert body["reason"] == "agent os_type unknown"

    async def test_409_when_agent_not_connected(
        self, client, admin_user, admin_headers
    ):
        from app.core.security import hash_api_key

        agent = RemoteAgent(
            name="offline-host",
            key_hash=hash_api_key("ta_offline_token_0001"),
            owner_id=str(admin_user.id),
            os_type="win32",
            update_channel="stable",
            agent_version="0.2.0",
        )
        await agent.insert()

        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert "not connected" in resp.json()["detail"].lower()

    async def test_404_when_agent_missing(self, client, admin_user, admin_headers):
        resp = await client.post(
            "/api/v1/workspaces/agents/000000000000000000000000/check-update",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_regular_user_cannot_trigger(
        self, client, regular_user, user_headers, registered_agent_with_ws
    ):
        agent, _ = await registered_agent_with_ws()
        resp = await client.post(
            f"/api/v1/workspaces/agents/{agent.id}/check-update",
            headers=user_headers,
        )
        assert resp.status_code in (403, 404)

