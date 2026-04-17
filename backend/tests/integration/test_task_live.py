"""Integration tests for ``GET /tasks/live`` (Sprint 2 / S2-8)."""

from __future__ import annotations

import pytest

from app.models.task import TaskStatus
from tests.helpers.factories import make_project, make_task


class TestLiveTasks:
    async def test_returns_only_in_progress(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(str(test_project.id), admin_user, status=TaskStatus.in_progress, title="Active")
        await make_task(str(test_project.id), admin_user, status=TaskStatus.todo, title="Pending")
        await make_task(str(test_project.id), admin_user, status=TaskStatus.done, title="Done")

        resp = await client.get("/api/v1/tasks/live", headers=admin_headers)
        assert resp.status_code == 200
        items = resp.json()
        titles = [t["title"] for t in items]
        assert "Active" in titles
        assert "Pending" not in titles
        assert "Done" not in titles

    async def test_excludes_soft_deleted(
        self, client, admin_user, test_project, admin_headers
    ):
        await make_task(
            str(test_project.id), admin_user,
            status=TaskStatus.in_progress, title="Deleted", is_deleted=True,
        )
        resp = await client.get("/api/v1/tasks/live", headers=admin_headers)
        assert all(t["title"] != "Deleted" for t in resp.json())

    async def test_response_shape_includes_active_form_and_project(
        self, client, admin_user, test_project, admin_headers
    ):
        t = await make_task(
            str(test_project.id), admin_user,
            status=TaskStatus.in_progress, title="Work",
        )
        t.active_form = "Writing tests"
        await t.save()

        resp = await client.get("/api/v1/tasks/live", headers=admin_headers)
        items = resp.json()
        match = next((x for x in items if x["id"] == str(t.id)), None)
        assert match is not None
        assert match["active_form"] == "Writing tests"
        assert match["project_id"] == str(test_project.id)
        assert match["project_name"] == test_project.name
        assert "updated_at" in match

    async def test_cross_project_for_admin(
        self, client, admin_user, test_project, admin_headers
    ):
        other = await make_project(admin_user, name="Other Proj")
        await make_task(str(test_project.id), admin_user, status=TaskStatus.in_progress, title="A")
        await make_task(str(other.id), admin_user, status=TaskStatus.in_progress, title="B")

        resp = await client.get("/api/v1/tasks/live", headers=admin_headers)
        titles = [t["title"] for t in resp.json()]
        assert "A" in titles
        assert "B" in titles

    async def test_ordered_by_updated_at_desc(
        self, client, admin_user, test_project, admin_headers
    ):
        import asyncio

        t1 = await make_task(
            str(test_project.id), admin_user,
            status=TaskStatus.in_progress, title="First",
        )
        await asyncio.sleep(0.05)
        t2 = await make_task(
            str(test_project.id), admin_user,
            status=TaskStatus.in_progress, title="Second",
        )

        resp = await client.get("/api/v1/tasks/live", headers=admin_headers)
        ids_in_order = [t["id"] for t in resp.json()]
        # More recently updated task comes first.
        assert ids_in_order.index(str(t2.id)) < ids_in_order.index(str(t1.id))

    async def test_requires_auth(self, client):
        resp = await client.get("/api/v1/tasks/live")
        assert resp.status_code in (401, 403)

    async def test_limit_parameter(
        self, client, admin_user, test_project, admin_headers
    ):
        for i in range(5):
            await make_task(
                str(test_project.id), admin_user,
                status=TaskStatus.in_progress, title=f"T{i}",
            )
        resp = await client.get("/api/v1/tasks/live?limit=2", headers=admin_headers)
        assert len(resp.json()) == 2
