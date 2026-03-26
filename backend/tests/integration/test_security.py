"""Security-related integration tests.

Tests for:
- Path traversal attack prevention on attachments endpoint
- File upload path traversal, size limits, and content type validation
- Comment content size validation
- MCP API key authentication (missing key, invalid key)
"""

import io

import pytest
import pytest_asyncio

from app.core.security import create_access_token, hash_api_key
from app.models import McpApiKey, Task, User
from app.models.task import Attachment
from tests.helpers.factories import make_task


def _task_url(project_id: str, task_id: str | None = None) -> str:
    base = f"/api/v1/projects/{project_id}/tasks"
    return f"{base}/{task_id}" if task_id else base


class TestPathTraversalAttachments:
    """Verify that path traversal attacks on the attachments endpoint are blocked."""

    async def test_path_traversal_in_filename_is_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        """Attempting ../../etc/passwd in the filename should return 404 (not in attachments list)."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            f"/api/v1/attachments/{task.id}/..%2F..%2Fetc%2Fpasswd",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_path_traversal_with_dotdot_slash(
        self, client, admin_user, test_project, admin_headers
    ):
        """../../ sequences in the filename should not serve arbitrary files."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            f"/api/v1/attachments/{task.id}/../../etc/passwd",
            headers=admin_headers,
        )
        # FastAPI path routing may result in 404 or 400; either is acceptable
        assert resp.status_code in (400, 404, 422)

    async def test_path_traversal_with_attachment_in_db(
        self, client, admin_user, test_project, admin_headers
    ):
        """Even if an attachment record contains traversal chars, the path check blocks it."""
        task = await make_task(str(test_project.id), admin_user)

        # Manually inject a malicious attachment filename into the task
        malicious_filename = "../../etc/passwd"
        task.attachments.append(
            Attachment(
                filename=malicious_filename,
                content_type="image/png",
                size=100,
            )
        )
        await task.save()

        resp = await client.get(
            f"/api/v1/attachments/{task.id}/{malicious_filename}",
            headers=admin_headers,
        )
        # Should be blocked - file won't exist on disk and/or path traversal check catches it
        assert resp.status_code in (400, 404)

    async def test_null_byte_in_filename(
        self, client, admin_user, test_project, admin_headers
    ):
        """Null bytes in filename should be rejected."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.get(
            f"/api/v1/attachments/{task.id}/file%00.png",
            headers=admin_headers,
        )
        assert resp.status_code in (400, 404)


class TestUploadPathTraversal:
    """Verify that path traversal filenames are sanitized during upload."""

    def _upload_url(self, project_id: str, task_id: str) -> str:
        return f"/api/v1/projects/{project_id}/tasks/{task_id}/attachments"

    def _make_file(self, filename: str, content: bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100,
                   content_type: str = "image/png"):
        return {"file": (filename, io.BytesIO(content), content_type)}

    async def test_dotdot_slash_stripped_from_filename(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """Upload with ../../etc/passwd filename should have directory components stripped."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files=self._make_file("../../etc/passwd"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        # The stored filename must not contain path traversal sequences
        assert ".." not in data["filename"]
        assert "/" not in data["filename"]
        assert "etc" not in data["filename"] or "passwd" in data["filename"]
        # The final component "passwd" should be preserved (after uuid prefix)
        assert data["filename"].endswith("_passwd")

    async def test_backslash_traversal_stripped(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        r"""Upload with ..\\..\\windows\\system32 filename should be sanitized."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files=self._make_file("..\\..\\windows\\system32\\config.png"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert ".." not in data["filename"]
        assert "\\" not in data["filename"]

    async def test_nested_traversal_stripped(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """Upload with foo/../../../bar.txt should have traversal stripped."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files=self._make_file("foo/../../../bar.png"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert ".." not in data["filename"]
        # Should keep only the final filename component
        assert data["filename"].endswith("_bar.png")

    async def test_null_byte_in_upload_filename(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """Null bytes in upload filename should be handled safely."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files=self._make_file("image\x00.png"),
            headers=admin_headers,
        )
        # Should either succeed with sanitized name or reject; must not crash
        assert resp.status_code in (201, 400, 422)
        if resp.status_code == 201:
            assert "\x00" not in resp.json()["filename"]

    async def test_file_saved_inside_uploads_dir(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """Regardless of filename tricks, uploaded file must land inside UPLOADS_DIR."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files=self._make_file("../../escape.png"),
            headers=admin_headers,
        )
        assert resp.status_code == 201
        saved_name = resp.json()["filename"]
        saved_path = tmp_path / str(task.id) / saved_name
        assert saved_path.exists()
        # Verify the file is under the expected uploads directory
        assert str(saved_path.resolve()).startswith(str(tmp_path.resolve()))


class TestUploadSizeLimits:
    """Verify that file size limits are enforced on upload."""

    def _upload_url(self, project_id: str, task_id: str) -> str:
        return f"/api/v1/projects/{project_id}/tasks/{task_id}/attachments"

    async def test_file_within_size_limit_accepted(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """A file under 5MB should be accepted."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        small_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 1024  # ~1KB
        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files={"file": ("small.png", io.BytesIO(small_content), "image/png")},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["size"] == len(small_content)

    async def test_file_exceeding_size_limit_rejected(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """A file over 5MB should be rejected with 400."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        over_limit = b"\x89PNG\r\n\x1a\n" + b"\x00" * (5 * 1024 * 1024 + 1)  # 5MB + 9 bytes
        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files={"file": ("huge.png", io.BytesIO(over_limit), "image/png")},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "too large" in resp.json()["detail"].lower()

    async def test_file_exactly_at_size_limit_accepted(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch
    ):
        """A file exactly at 5MB should be accepted."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        exact_limit = b"\x00" * (5 * 1024 * 1024)  # exactly 5MB
        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files={"file": ("exact.png", io.BytesIO(exact_limit), "image/png")},
            headers=admin_headers,
        )
        assert resp.status_code == 201


class TestUploadContentTypeValidation:
    """Verify that only allowed content types are accepted."""

    def _upload_url(self, project_id: str, task_id: str) -> str:
        return f"/api/v1/projects/{project_id}/tasks/{task_id}/attachments"

    @pytest.mark.parametrize("content_type", ["image/jpeg", "image/png", "image/gif", "image/webp"])
    async def test_allowed_image_types_accepted(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch,
        content_type
    ):
        """All allowed image types should be accepted."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files={"file": ("test.img", io.BytesIO(b"\x00" * 100), content_type)},
            headers=admin_headers,
        )
        assert resp.status_code == 201

    @pytest.mark.parametrize("content_type", [
        "application/pdf",
        "text/html",
        "application/javascript",
        "text/plain",
        "application/x-executable",
        "application/octet-stream",
        "image/svg+xml",
    ])
    async def test_disallowed_content_types_rejected(
        self, client, admin_user, test_project, admin_headers, tmp_path, monkeypatch,
        content_type
    ):
        """Non-image file types should be rejected with 400."""
        monkeypatch.setattr("app.api.v1.endpoints.tasks.UPLOADS_DIR", tmp_path)
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            self._upload_url(str(test_project.id), str(task.id)),
            files={"file": ("test.file", io.BytesIO(b"\x00" * 100), content_type)},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        assert "not allowed" in resp.json()["detail"].lower()


class TestCommentContentValidation:
    """Verify comment content size limits are enforced."""

    async def test_comment_within_size_limit(
        self, client, admin_user, test_project, admin_headers
    ):
        """A comment within the max_length should succeed."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "A" * 1000},
            headers=admin_headers,
        )
        assert resp.status_code == 201

    async def test_comment_at_max_length(
        self, client, admin_user, test_project, admin_headers
    ):
        """A comment exactly at max_length (10000) should succeed."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "A" * 10000},
            headers=admin_headers,
        )
        assert resp.status_code == 201

    async def test_comment_exceeding_max_length_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        """A comment exceeding max_length (10000) should be rejected with 422."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "A" * 10001},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_very_large_comment_rejected(
        self, client, admin_user, test_project, admin_headers
    ):
        """A very large comment (100k chars) should be rejected."""
        task = await make_task(str(test_project.id), admin_user)

        resp = await client.post(
            f"{_task_url(str(test_project.id), str(task.id))}/comments",
            json={"content": "X" * 100_000},
            headers=admin_headers,
        )
        assert resp.status_code == 422


class TestMcpApiKeyAuthentication:
    """Test MCP API key management endpoint authentication."""

    async def test_missing_auth_returns_401_for_list(self, client):
        """GET /mcp-keys without auth token returns 401."""
        resp = await client.get("/api/v1/mcp-keys")
        assert resp.status_code == 401

    async def test_missing_auth_returns_401_for_create(self, client):
        """POST /mcp-keys without auth token returns 401."""
        resp = await client.post("/api/v1/mcp-keys", json={"name": "Test"})
        assert resp.status_code == 401

    async def test_missing_auth_returns_401_for_revoke(self, client):
        """DELETE /mcp-keys/:id without auth token returns 401."""
        resp = await client.delete("/api/v1/mcp-keys/000000000000000000000000")
        assert resp.status_code == 401

    async def test_invalid_token_returns_401(self, client):
        """An invalid/expired JWT token should return 401."""
        resp = await client.get(
            "/api/v1/mcp-keys",
            headers={"Authorization": "Bearer invalid.jwt.token"},
        )
        assert resp.status_code == 401

    async def test_non_admin_can_access_own_mcp_keys(
        self, client, regular_user, user_headers
    ):
        """Any authenticated user can manage their own API keys."""
        resp = await client.get("/api/v1/mcp-keys", headers=user_headers)
        assert resp.status_code == 200

        resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "My Key"},
            headers=user_headers,
        )
        assert resp.status_code == 201

    async def test_admin_can_create_and_use_key(
        self, client, admin_user, admin_headers
    ):
        """Admin can create a key and the raw key is returned only on creation."""
        resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "Security Test Key"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("mtodo_")

        # The key should be stored as a hash in DB
        key_doc = await McpApiKey.get(data["id"])
        assert key_doc is not None
        assert key_doc.key_hash == hash_api_key(data["key"])
        assert key_doc.key_hash != data["key"]

    async def test_revoked_key_not_in_active_list(
        self, client, admin_user, admin_headers
    ):
        """After revoking a key, it should not appear in the active keys list."""
        # Create
        create_resp = await client.post(
            "/api/v1/mcp-keys",
            json={"name": "To Revoke"},
            headers=admin_headers,
        )
        key_id = create_resp.json()["id"]

        # Revoke
        revoke_resp = await client.delete(
            f"/api/v1/mcp-keys/{key_id}", headers=admin_headers
        )
        assert revoke_resp.status_code == 204

        # Verify not in list
        list_resp = await client.get("/api/v1/mcp-keys", headers=admin_headers)
        ids = [k["id"] for k in list_resp.json()]
        assert key_id not in ids

        # Verify DB has is_active=False
        db_key = await McpApiKey.get(key_id)
        assert db_key.is_active is False
