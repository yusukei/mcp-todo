"""Phase 6.B — admin user-detail endpoint tests.

Covers:
  * GET /users/{user_id}        — extras (ai_runs_30d, projects_count) included
  * GET /users/{user_id}/projects — project membership listing
  * GET /users/{user_id}/ai_runs  — bucket aggregation by tool name
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.models import McpToolUsageBucket, Project, User
from app.models.mcp_api_key import McpApiKey
from app.models.project import ProjectMember
from app.models.user import AuthType


pytestmark = pytest.mark.asyncio


def _floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


@pytest_asyncio.fixture
async def populated(admin_user, regular_user):
    """admin_user owns 1 key, regular_user owns 1 key, both populate
    buckets. admin_user is in 1 project, regular_user is in 2."""
    admin_key = McpApiKey(
        key_hash="hash-admin-detail",
        name="admin key",
        created_by=admin_user,
    )
    regular_key = McpApiKey(
        key_hash="hash-regular-detail",
        name="regular key",
        created_by=regular_user,
    )
    await admin_key.insert()
    await regular_key.insert()

    p1 = Project(
        name="P1",
        color="#fc618d",
        created_by=str(admin_user.id),
        members=[
            ProjectMember(user_id=str(admin_user.id), role="owner"),
            ProjectMember(user_id=str(regular_user.id), role="member"),
        ],
    )
    p2 = Project(
        name="P2",
        color="#a9dc76",
        created_by=str(regular_user.id),
        members=[ProjectMember(user_id=str(regular_user.id), role="owner")],
    )
    await p1.insert()
    await p2.insert()

    now_h = _floor_hour(datetime.now(UTC))
    await McpToolUsageBucket(
        tool_name="create_task",
        api_key_id=str(admin_key.id),
        hour=now_h - timedelta(hours=1),
        call_count=7,
    ).insert()
    await McpToolUsageBucket(
        tool_name="list_tasks",
        api_key_id=str(admin_key.id),
        hour=now_h - timedelta(days=5),
        call_count=4,
    ).insert()
    # Outside 30d — must NOT count
    await McpToolUsageBucket(
        tool_name="old_tool",
        api_key_id=str(admin_key.id),
        hour=now_h - timedelta(days=45),
        call_count=99,
    ).insert()

    return {"admin_key": admin_key, "regular_key": regular_key, "p1": p1, "p2": p2}


# ── GET /users/{user_id} extras ────────────────────────────────


async def test_get_user_includes_extras(
    client: AsyncClient, admin_headers, admin_user, populated
):
    r = await client.get(f"/api/v1/users/{admin_user.id}", headers=admin_headers)
    assert r.status_code == 200
    body = r.json()
    # 7 + 4 inside the 30d window, 99 outside is excluded
    assert body["ai_runs_30d"] == 11
    assert body["projects_count"] == 1


async def test_get_user_404_for_unknown(
    client: AsyncClient, admin_headers
):
    # Valid 24-hex ObjectId that doesn't exist
    r = await client.get(
        "/api/v1/users/000000000000000000000000", headers=admin_headers
    )
    assert r.status_code == 404


async def test_get_user_forbidden_for_non_admin(
    client: AsyncClient, user_headers, admin_user
):
    r = await client.get(f"/api/v1/users/{admin_user.id}", headers=user_headers)
    assert r.status_code == 403


# ── GET /users/{user_id}/projects ──────────────────────────────


async def test_list_user_projects_returns_memberships(
    client: AsyncClient, admin_headers, regular_user, populated
):
    r = await client.get(
        f"/api/v1/users/{regular_user.id}/projects", headers=admin_headers
    )
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list)
    names = {p["name"] for p in items}
    assert names == {"P1", "P2"}
    # Roles inherited from ProjectMember row
    by_name = {p["name"]: p for p in items}
    assert by_name["P1"]["role"] == "member"
    assert by_name["P2"]["role"] == "owner"
    # member_count surfaces total members on each project
    assert by_name["P1"]["member_count"] == 2
    assert by_name["P2"]["member_count"] == 1


async def test_list_user_projects_empty_for_invitee(
    client: AsyncClient, admin_headers
):
    invitee = User(
        email="invitee-detail@test.com",
        name="Invitee",
        auth_type=AuthType.admin,
    )
    await invitee.insert()
    r = await client.get(
        f"/api/v1/users/{invitee.id}/projects", headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_list_user_projects_forbidden_for_non_admin(
    client: AsyncClient, user_headers, admin_user
):
    r = await client.get(
        f"/api/v1/users/{admin_user.id}/projects", headers=user_headers
    )
    assert r.status_code == 403


# ── GET /users/{user_id}/ai_runs ───────────────────────────────


async def test_user_ai_runs_aggregates_by_tool(
    client: AsyncClient, admin_headers, admin_user, populated
):
    r = await client.get(
        f"/api/v1/users/{admin_user.id}/ai_runs", headers=admin_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 11
    by_tool = {row["tool_name"]: row["call_count"] for row in body["by_tool"]}
    assert by_tool == {"create_task": 7, "list_tasks": 4}
    # Older bucket must be excluded by the default 30d window
    assert "old_tool" not in by_tool


async def test_user_ai_runs_respects_days_param(
    client: AsyncClient, admin_headers, admin_user, populated
):
    """days=90 should bring the 45-day-old bucket back in."""
    r = await client.get(
        f"/api/v1/users/{admin_user.id}/ai_runs?days=90",
        headers=admin_headers,
    )
    assert r.status_code == 200
    by_tool = {row["tool_name"]: row["call_count"] for row in r.json()["by_tool"]}
    assert by_tool.get("old_tool") == 99


async def test_user_ai_runs_zero_when_no_keys(
    client: AsyncClient, admin_headers
):
    """A user without any API keys returns total_calls=0, empty by_tool."""
    keyless = User(
        email="keyless@test.com",
        name="Keyless",
        auth_type=AuthType.admin,
    )
    await keyless.insert()
    r = await client.get(
        f"/api/v1/users/{keyless.id}/ai_runs", headers=admin_headers
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 0
    assert body["by_tool"] == []


async def test_user_ai_runs_forbidden_for_non_admin(
    client: AsyncClient, user_headers, admin_user
):
    r = await client.get(
        f"/api/v1/users/{admin_user.id}/ai_runs", headers=user_headers
    )
    assert r.status_code == 403
