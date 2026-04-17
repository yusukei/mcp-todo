"""Integration tests for ``POST/DELETE /tasks/{id}/links`` (Sprint 1 / S1-2)."""

from __future__ import annotations

import pytest

from tests.helpers.factories import make_task


def _link_url(project_id: str, task_id: str, target_id: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/tasks/{task_id}/links"
    return f"{base}/{target_id}" if target_id else base


class TestCreateLink:
    async def test_happy_path_updates_both_sides(
        self, client, admin_user, test_project, admin_headers
    ):
        source = await make_task(str(test_project.id), admin_user, title="A")
        target = await make_task(str(test_project.id), admin_user, title="B")

        resp = await client.post(
            _link_url(str(test_project.id), str(source.id)),
            json={"target_id": str(target.id), "relation": "blocks"},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert str(target.id) in data["source"]["blocks"]
        assert str(source.id) in data["target"]["blocked_by"]
        assert data["target"]["blocks"] == []
        assert data["source"]["blocked_by"] == []

    async def test_self_reference_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)
        resp = await client.post(
            _link_url(str(test_project.id), str(task.id)),
            json={"target_id": str(task.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "self_reference"

    async def test_duplicate_link_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user)
        b = await make_task(str(test_project.id), admin_user)

        first = await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        assert first.status_code == 201

        dup = await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        assert dup.status_code == 400
        assert dup.json()["detail"]["error"] == "duplicate_link"

    async def test_cycle_rejected_with_path(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user, title="A")
        b = await make_task(str(test_project.id), admin_user, title="B")

        # A blocks B (OK)
        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        # B blocks A → cycle
        resp = await client.post(
            _link_url(str(test_project.id), str(b.id)),
            json={"target_id": str(a.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error"] == "cycle_detected"
        assert detail["path"] == [str(a.id), str(b.id)]

    async def test_cross_project_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        from tests.helpers.factories import make_project

        source = await make_task(str(test_project.id), admin_user)
        other = await make_project(admin_user, name="Other")
        target = await make_task(str(other.id), admin_user)

        resp = await client.post(
            _link_url(str(test_project.id), str(source.id)),
            json={"target_id": str(target.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "cross_project"

    async def test_target_not_found(
        self, client, admin_user, test_project, admin_headers
    ):
        source = await make_task(str(test_project.id), admin_user)
        # A valid-looking but non-existent ObjectId.
        missing = "507f1f77bcf86cd799439011"
        resp = await client.post(
            _link_url(str(test_project.id), str(source.id)),
            json={"target_id": missing},
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "target_not_found"

    async def test_non_member_forbidden(
        self, client, admin_user, test_project, user_headers, regular_user
    ):
        # regular_user is a member of test_project per conftest; create a new
        # project without them to check 403 path.
        from tests.helpers.factories import make_project

        isolated = await make_project(admin_user, members=[admin_user], name="Admin only")
        source = await make_task(str(isolated.id), admin_user)
        target = await make_task(str(isolated.id), admin_user)

        resp = await client.post(
            _link_url(str(isolated.id), str(source.id)),
            json={"target_id": str(target.id)},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestDeleteLink:
    async def test_unlink_cleans_both_sides(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user)
        b = await make_task(str(test_project.id), admin_user)

        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )

        resp = await client.delete(
            _link_url(str(test_project.id), str(a.id), str(b.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["source"]["blocks"] == []
        assert data["target"]["blocked_by"] == []

    async def test_unlink_missing_returns_404(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user)
        b = await make_task(str(test_project.id), admin_user)

        resp = await client.delete(
            _link_url(str(test_project.id), str(a.id), str(b.id)),
            headers=admin_headers,
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"] == "link_not_found"

    async def test_relink_after_unlink(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user)
        b = await make_task(str(test_project.id), admin_user)

        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        await client.delete(
            _link_url(str(test_project.id), str(a.id), str(b.id)),
            headers=admin_headers,
        )
        resp = await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        assert resp.status_code == 201


class TestDeleteTaskWithDependents:
    """S1-5 acceptance: ``DELETE /tasks/{id}`` respects blocked_by dependents."""

    async def test_delete_without_dependents_succeeds(
        self, client, admin_user, test_project, admin_headers
    ):
        task = await make_task(str(test_project.id), admin_user)
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/tasks/{task.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204

    async def test_delete_with_dependents_rejected_without_force(
        self, client, admin_user, test_project, admin_headers
    ):
        a = await make_task(str(test_project.id), admin_user, title="A")
        b = await make_task(str(test_project.id), admin_user, title="B")

        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        # Now B.blocked_by contains A — deleting A should be refused.
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/tasks/{a.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["error"] == "blocks_dependents"
        assert str(b.id) in detail["dependents"]

    async def test_delete_with_force_purges_dependents(
        self, client, admin_user, test_project, admin_headers
    ):
        from app.models import Task

        a = await make_task(str(test_project.id), admin_user, title="A")
        b = await make_task(str(test_project.id), admin_user, title="B")

        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/tasks/{a.id}?force=true",
            headers=admin_headers,
        )
        assert resp.status_code == 204

        # B's blocked_by should no longer reference A (reference was purged).
        reloaded = await Task.get(str(b.id))
        assert str(a.id) not in reloaded.blocked_by

    async def test_delete_leaf_task_with_only_blocks_out_succeeds(
        self, client, admin_user, test_project, admin_headers
    ):
        """Deleting A in A→B only blocks B from being blocked by A; no dependents
        reference A in their blocked_by (B does, actually — so this is the
        reverse case). Here we test the mirror: delete B when only A points
        at it. B has no dependents of its own, so deletion is allowed."""
        from app.models import Task

        a = await make_task(str(test_project.id), admin_user, title="A")
        b = await make_task(str(test_project.id), admin_user, title="B")

        await client.post(
            _link_url(str(test_project.id), str(a.id)),
            json={"target_id": str(b.id)},
            headers=admin_headers,
        )
        # Deleting B: nobody has B in blocked_by, so this is allowed.
        resp = await client.delete(
            f"/api/v1/projects/{test_project.id}/tasks/{b.id}",
            headers=admin_headers,
        )
        assert resp.status_code == 204
        # A's blocks list still contains B's id (stale reference) — this is
        # expected because the delete path doesn't walk ``A.blocks`` backwards.
        # The UI / read path should filter out deleted targets.
        reloaded_a = await Task.get(str(a.id))
        assert reloaded_a.blocks == [str(b.id)]
