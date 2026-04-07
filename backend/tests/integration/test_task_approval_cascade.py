"""Integration tests for cascading approve flag to subtasks."""

import pytest

from app.models import Task
from app.services.task_approval import cascade_approve_subtasks
from tests.helpers.factories import make_task


def _task_url(project_id: str, task_id: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/tasks"
    return f"{base}/{task_id}" if task_id else base


class TestCascadeApproveHelper:
    async def test_no_subtasks(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="Parent")
        result = await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        assert result == []

    async def test_approves_direct_subtasks(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="Parent")
        child1 = await make_task(
            str(test_project.id), admin_user, title="C1", parent_task_id=str(parent.id)
        )
        child2 = await make_task(
            str(test_project.id), admin_user, title="C2", parent_task_id=str(parent.id)
        )

        result = await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        assert len(result) == 2

        c1 = await Task.get(str(child1.id))
        c2 = await Task.get(str(child2.id))
        assert c1.approved is True
        assert c2.approved is True

    async def test_cascades_recursively(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="P")
        child = await make_task(
            str(test_project.id), admin_user, title="C", parent_task_id=str(parent.id)
        )
        grandchild = await make_task(
            str(test_project.id), admin_user, title="GC", parent_task_id=str(child.id)
        )

        result = await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        assert len(result) == 2

        gc = await Task.get(str(grandchild.id))
        assert gc.approved is True

    async def test_skips_deleted_subtasks(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="P")
        live = await make_task(
            str(test_project.id), admin_user, title="L", parent_task_id=str(parent.id)
        )
        await make_task(
            str(test_project.id),
            admin_user,
            title="D",
            parent_task_id=str(parent.id),
            is_deleted=True,
        )

        result = await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        assert len(result) == 1
        assert str(result[0].id) == str(live.id)

    async def test_clears_needs_detail(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="P")
        child = await make_task(
            str(test_project.id),
            admin_user,
            title="C",
            parent_task_id=str(parent.id),
            needs_detail=True,
        )

        await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        c = await Task.get(str(child.id))
        assert c.approved is True
        assert c.needs_detail is False

    async def test_idempotent_on_already_approved(self, admin_user, test_project):
        parent = await make_task(str(test_project.id), admin_user, title="P")
        await make_task(
            str(test_project.id),
            admin_user,
            title="C",
            parent_task_id=str(parent.id),
            approved=True,
        )

        result = await cascade_approve_subtasks(str(parent.id), str(admin_user.id))
        # Already-approved subtasks are skipped (no mutation needed)
        assert result == []


class TestRestUpdateTaskCascade:
    async def test_approving_parent_cascades_to_subtasks(
        self, client, admin_user, test_project, admin_headers
    ):
        parent = await make_task(str(test_project.id), admin_user, title="Parent")
        c1 = await make_task(
            str(test_project.id), admin_user, title="C1", parent_task_id=str(parent.id)
        )
        c2 = await make_task(
            str(test_project.id), admin_user, title="C2", parent_task_id=str(parent.id)
        )

        resp = await client.patch(
            _task_url(str(test_project.id), str(parent.id)),
            json={"approved": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["approved"] is True

        c1_after = await Task.get(str(c1.id))
        c2_after = await Task.get(str(c2.id))
        assert c1_after.approved is True
        assert c2_after.approved is True

    async def test_unapproving_parent_does_not_cascade(
        self, client, admin_user, test_project, admin_headers
    ):
        parent = await make_task(
            str(test_project.id), admin_user, title="Parent", approved=True
        )
        child = await make_task(
            str(test_project.id),
            admin_user,
            title="C",
            parent_task_id=str(parent.id),
            approved=True,
        )

        resp = await client.patch(
            _task_url(str(test_project.id), str(parent.id)),
            json={"approved": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200

        c_after = await Task.get(str(child.id))
        # Subtasks remain approved (cascade only applies on True)
        assert c_after.approved is True


class TestRestBatchUpdateCascade:
    async def test_batch_approve_cascades(
        self, client, admin_user, test_project, admin_headers
    ):
        parent = await make_task(str(test_project.id), admin_user, title="P")
        child = await make_task(
            str(test_project.id), admin_user, title="C", parent_task_id=str(parent.id)
        )
        grandchild = await make_task(
            str(test_project.id), admin_user, title="GC", parent_task_id=str(child.id)
        )

        resp = await client.patch(
            f"/api/v1/projects/{str(test_project.id)}/tasks/batch",
            json={"updates": [{"task_id": str(parent.id), "approved": True}]},
            headers=admin_headers,
        )
        assert resp.status_code == 200

        c = await Task.get(str(child.id))
        gc = await Task.get(str(grandchild.id))
        assert c.approved is True
        assert gc.approved is True
