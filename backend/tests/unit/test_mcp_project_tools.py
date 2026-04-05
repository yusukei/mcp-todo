"""MCP tool unit tests for create_project, update_project, delete_project."""

from unittest.mock import AsyncMock, patch

import pytest

from app.models import Project, Task
from app.models.project import ProjectStatus
from app.models.task import TaskStatus
from tests.helpers.factories import make_project, make_task


# Shared mock for authenticate + check_project_access
_MOCK_KEY_INFO = {"key_id": "test-key", "project_scopes": []}


def _patch_project_auth():
    """Patch authenticate() and check_project_access() for MCP project tool tests."""
    return [
        patch(
            "app.mcp.tools.projects.authenticate",
            new_callable=AsyncMock,
            return_value=_MOCK_KEY_INFO,
        ),
        patch("app.mcp.tools.projects.check_project_access"),
    ]


def _patch_project_publish():
    """Patch publish_event() for MCP project tool tests."""
    return patch(
        "app.mcp.tools.projects.publish_event",
        new_callable=AsyncMock,
    )


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


class TestCreateProject:
    async def test_basic_creation(self, admin_user):
        """create_project creates a project with default values."""
        from app.mcp.tools.projects import create_project

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish() as mock_pub:
            result = await create_project(name="My Project")

        assert result["name"] == "My Project"
        assert result["description"] == ""
        assert result["color"] == "#6366f1"
        assert result["status"] == "active"
        assert len(result["members"]) == 1
        assert result["members"][0]["user_id"] == str(admin_user.id)
        assert result["created_by"] == str(admin_user.id)

        mock_pub.assert_called_once()
        call_args = mock_pub.call_args
        assert call_args[0][1] == "project.created"

    async def test_creation_with_all_fields(self, admin_user):
        """create_project accepts description and color."""
        from app.mcp.tools.projects import create_project

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            result = await create_project(
                name="Custom Project",
                description="A detailed description",
                color="#ff0000",
            )

        assert result["name"] == "Custom Project"
        assert result["description"] == "A detailed description"
        assert result["color"] == "#ff0000"

    async def test_create_persists_to_db(self, admin_user):
        """create_project persists the project in the database."""
        from app.mcp.tools.projects import create_project

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            result = await create_project(name="Persisted Project")

        db_project = await Project.get(result["id"])
        assert db_project is not None
        assert db_project.name == "Persisted Project"
        assert db_project.status == ProjectStatus.active

    async def test_create_without_admin_user_raises(self):
        """create_project raises ToolError when no admin user exists."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.projects import create_project

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with pytest.raises(ToolError, match="No admin user found"):
                await create_project(name="No Admin")


# ---------------------------------------------------------------------------
# update_project
# ---------------------------------------------------------------------------


class TestUpdateProject:
    async def test_update_name(self, admin_user, test_project):
        """update_project changes the project name."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish() as mock_pub:
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(project_id=pid, name="Updated Name")

        assert result["name"] == "Updated Name"

        db_project = await Project.get(pid)
        assert db_project.name == "Updated Name"

        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][1] == "project.updated"

    async def test_update_description(self, admin_user, test_project):
        """update_project changes the project description."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(project_id=pid, description="New desc")

        assert result["description"] == "New desc"

    async def test_update_color(self, admin_user, test_project):
        """update_project changes the project color."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(project_id=pid, color="#00ff00")

        assert result["color"] == "#00ff00"

    async def test_update_status_to_archived(self, admin_user, test_project):
        """update_project can set status to archived."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(project_id=pid, status="archived")

        assert result["status"] == "archived"

        db_project = await Project.get(pid)
        assert db_project.status == ProjectStatus.archived

    async def test_update_multiple_fields(self, admin_user, test_project):
        """update_project can change multiple fields at once."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(
                    project_id=pid,
                    name="Multi Update",
                    description="Multi desc",
                    color="#abcdef",
                )

        assert result["name"] == "Multi Update"
        assert result["description"] == "Multi desc"
        assert result["color"] == "#abcdef"

    async def test_update_invalid_status_raises(self, admin_user, test_project):
        """update_project raises ToolError for invalid status."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                with pytest.raises(ToolError, match="Invalid status"):
                    await update_project(project_id=pid, status="invalid")

    async def test_update_nonexistent_project_raises(self, admin_user):
        """update_project raises ToolError for missing project."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.projects import update_project

        fake_id = "000000000000000000000000"

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=fake_id,
            ):
                with pytest.raises(ToolError, match="Project not found"):
                    await update_project(project_id=fake_id, name="Ghost")

    async def test_update_no_changes(self, admin_user, test_project):
        """update_project with no fields still returns the project."""
        from app.mcp.tools.projects import update_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await update_project(project_id=pid)

        assert result["name"] == "Test Project"


# ---------------------------------------------------------------------------
# delete_project
# ---------------------------------------------------------------------------


class TestDeleteProject:
    async def test_delete_archives_project(self, admin_user, test_project):
        """delete_project sets project status to archived."""
        from app.mcp.tools.projects import delete_project

        pid = str(test_project.id)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish() as mock_pub:
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                result = await delete_project(project_id=pid)

        assert result["success"] is True
        assert result["project_id"] == pid

        db_project = await Project.get(pid)
        assert db_project.status == ProjectStatus.archived

        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][1] == "project.deleted"

    async def test_delete_soft_deletes_tasks(self, admin_user, test_project):
        """delete_project soft-deletes all tasks in the project."""
        from app.mcp.tools.projects import delete_project

        pid = str(test_project.id)
        task1 = await make_task(pid, admin_user, title="Task 1")
        task2 = await make_task(pid, admin_user, title="Task 2")
        deleted_task = await make_task(pid, admin_user, title="Already Deleted", is_deleted=True)

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=pid,
            ):
                await delete_project(project_id=pid)

        db_task1 = await Task.get(task1.id)
        db_task2 = await Task.get(task2.id)
        assert db_task1.is_deleted is True
        assert db_task2.is_deleted is True

    async def test_delete_nonexistent_project_raises(self, admin_user):
        """delete_project raises ToolError for missing project."""
        from fastmcp.exceptions import ToolError

        from app.mcp.tools.projects import delete_project

        fake_id = "000000000000000000000000"

        patches = _patch_project_auth()
        with patches[0], patches[1], _patch_project_publish():
            with patch(
                "app.mcp.tools.projects._resolve_project_id",
                new_callable=AsyncMock,
                return_value=fake_id,
            ):
                with pytest.raises(ToolError, match="Project not found"):
                    await delete_project(project_id=fake_id)
