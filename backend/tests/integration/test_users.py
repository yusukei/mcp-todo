"""ユーザー管理エンドポイント (/api/v1/users/*) の統合テスト"""

import pytest
import pytest_asyncio

from app.models import AllowedEmail, Project, User
from app.models.mcp_api_key import McpApiKey
from app.models.project import MemberRole, ProjectMember
from app.models.user import AuthType
from app.core.security import create_access_token, hash_password


class TestListUsers:
    async def test_admin_can_list_users(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.get("/api/v1/users", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "skip" in data
        emails = [u["email"] for u in data["items"]]
        assert "admin@test.com" in emails
        assert "user@test.com" in emails

    async def test_non_admin_gets_403(
        self, client, regular_user, user_headers
    ):
        resp = await client.get("/api/v1/users", headers=user_headers)
        assert resp.status_code == 403

    async def test_unauthenticated_returns_401(self, client):
        resp = await client.get("/api/v1/users")
        assert resp.status_code == 401


class TestCreateUser:
    async def test_create_user_with_email_name_password(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "new@test.com", "name": "New User", "password": "newpass123"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new@test.com"
        assert data["name"] == "New User"
        assert data["is_active"] is True

    async def test_duplicate_email_returns_409(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "admin@test.com", "name": "Duplicate"},
            headers=admin_headers,
        )
        assert resp.status_code == 409

    async def test_create_admin_user(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "admin2@test.com", "name": "Admin 2", "is_admin": True},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["is_admin"] is True

    async def test_non_admin_cannot_create_user(
        self, client, regular_user, user_headers
    ):
        resp = await client.post(
            "/api/v1/users",
            json={"email": "x@test.com", "name": "X"},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestGetUser:
    async def test_admin_can_get_user(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.get(
            f"/api/v1/users/{regular_user.id}", headers=admin_headers
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "user@test.com"

    async def test_nonexistent_user_returns_404(self, client, admin_user, admin_headers):
        resp = await client.get(
            "/api/v1/users/000000000000000000000000", headers=admin_headers
        )
        assert resp.status_code == 404

    async def test_non_admin_cannot_get_user(
        self, client, admin_user, regular_user, user_headers
    ):
        resp = await client.get(
            f"/api/v1/users/{admin_user.id}", headers=user_headers
        )
        assert resp.status_code == 403


class TestUpdateUser:
    async def test_update_user_name(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/users/{regular_user.id}",
            json={"name": "Updated Name"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated Name"

    async def test_update_is_active(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/users/{regular_user.id}",
            json={"is_active": False},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

    async def test_update_is_admin(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.patch(
            f"/api/v1/users/{regular_user.id}",
            json={"is_admin": True},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["is_admin"] is True

    async def test_update_nonexistent_user_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.patch(
            "/api/v1/users/000000000000000000000000",
            json={"name": "X"},
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_non_admin_cannot_update_user(
        self, client, admin_user, regular_user, user_headers
    ):
        resp = await client.patch(
            f"/api/v1/users/{admin_user.id}",
            json={"name": "Hacked"},
            headers=user_headers,
        )
        assert resp.status_code == 403


class TestDeleteUser:
    async def test_permanently_deletes_user(
        self, client, admin_user, regular_user, admin_headers
    ):
        resp = await client.delete(
            f"/api/v1/users/{regular_user.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        # ユーザーが DB から完全に削除されている
        db_user = await User.get(regular_user.id)
        assert db_user is None

    async def test_delete_removes_from_project_members(
        self, client, admin_user, admin_headers
    ):
        # 削除対象ユーザーを作成
        target = User(
            email="target-del@test.com",
            name="Target",
            auth_type=AuthType.admin,
            password_hash=hash_password("pass"),
        )
        await target.insert()

        # プロジェクトにメンバーとして追加
        project = Project(
            name="Del Test Project",
            created_by=admin_user,
            members=[
                ProjectMember(user_id=str(admin_user.id), role=MemberRole.owner),
                ProjectMember(user_id=str(target.id), role=MemberRole.member),
            ],
        )
        await project.insert()

        resp = await client.delete(
            f"/api/v1/users/{target.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        # プロジェクトのメンバーから除外されている
        db_project = await Project.get(project.id)
        member_ids = [m.user_id for m in db_project.members]
        assert str(target.id) not in member_ids
        assert str(admin_user.id) in member_ids

    async def test_delete_deactivates_api_keys(
        self, client, admin_user, admin_headers
    ):
        # 削除対象ユーザーを作成
        target = User(
            email="target-key@test.com",
            name="Key Owner",
            auth_type=AuthType.admin,
            password_hash=hash_password("pass"),
        )
        await target.insert()

        # APIキーを作成
        api_key = McpApiKey(
            key_hash="fake_hash_for_test",
            name="Test Key",
            created_by=target,
            is_active=True,
        )
        await api_key.insert()

        resp = await client.delete(
            f"/api/v1/users/{target.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        # APIキーが無効化されている
        db_key = await McpApiKey.get(api_key.id)
        assert db_key.is_active is False

    async def test_cannot_delete_yourself(
        self, client, admin_user, admin_headers
    ):
        resp = await client.delete(
            f"/api/v1/users/{admin_user.id}", headers=admin_headers
        )
        assert resp.status_code == 400

    async def test_delete_nonexistent_user_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.delete(
            "/api/v1/users/000000000000000000000000", headers=admin_headers
        )
        assert resp.status_code == 404

    async def test_non_admin_cannot_delete_user(
        self, client, admin_user, regular_user, user_headers
    ):
        resp = await client.delete(
            f"/api/v1/users/{admin_user.id}", headers=user_headers
        )
        assert resp.status_code == 403


class TestResetPassword:
    async def test_admin_can_reset_password(
        self, client, admin_user, admin_headers
    ):
        # admin_user は auth_type=admin
        resp = await client.post(
            f"/api/v1/users/{admin_user.id}/reset-password",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "new_password" in data
        assert len(data["new_password"]) > 0

    async def test_admin_can_reset_with_custom_password(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            f"/api/v1/users/{admin_user.id}/reset-password",
            json={"password": "CustomPass123!"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["new_password"] == "CustomPass123!"

    async def test_cannot_reset_google_user_password(
        self, client, admin_user, regular_user, admin_headers
    ):
        # regular_user は auth_type=google
        resp = await client.post(
            f"/api/v1/users/{regular_user.id}/reset-password",
            headers=admin_headers,
        )
        assert resp.status_code == 400

    async def test_reset_nonexistent_user_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/users/000000000000000000000000/reset-password",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_non_admin_cannot_reset_password(
        self, client, admin_user, regular_user, user_headers
    ):
        resp = await client.post(
            f"/api/v1/users/{admin_user.id}/reset-password",
            headers=user_headers,
        )
        assert resp.status_code == 403

    async def test_short_password_returns_422(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            f"/api/v1/users/{admin_user.id}/reset-password",
            json={"password": "short"},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    async def test_reset_clears_password_disabled(
        self, client, admin_user, admin_headers
    ):
        # password_disabled を True に設定
        admin_user.password_disabled = True
        await admin_user.save_updated()

        resp = await client.post(
            f"/api/v1/users/{admin_user.id}/reset-password",
            headers=admin_headers,
        )
        assert resp.status_code == 200

        # password_disabled が False にリセットされている
        db_user = await User.get(admin_user.id)
        assert db_user.password_disabled is False


class TestAllowedEmails:
    async def test_admin_can_list_allowed_emails(
        self, client, admin_user, admin_headers
    ):
        await AllowedEmail(email="allowed@test.com").insert()

        resp = await client.get("/api/v1/users/allowed-emails/", headers=admin_headers)
        assert resp.status_code == 200
        emails = [e["email"] for e in resp.json()]
        assert "allowed@test.com" in emails

    async def test_admin_can_add_allowed_email(
        self, client, admin_user, admin_headers
    ):
        resp = await client.post(
            "/api/v1/users/allowed-emails/",
            json={"email": "new-allowed@test.com"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["email"] == "new-allowed@test.com"
        assert "id" in data
        assert "created_at" in data

    async def test_duplicate_allowed_email_returns_409(
        self, client, admin_user, admin_headers
    ):
        await AllowedEmail(email="dup@test.com").insert()

        resp = await client.post(
            "/api/v1/users/allowed-emails/",
            json={"email": "dup@test.com"},
            headers=admin_headers,
        )
        assert resp.status_code == 409

    async def test_admin_can_delete_allowed_email(
        self, client, admin_user, admin_headers
    ):
        entry = AllowedEmail(email="remove@test.com")
        await entry.insert()

        resp = await client.delete(
            f"/api/v1/users/allowed-emails/{entry.id}", headers=admin_headers
        )
        assert resp.status_code == 204

        # DB から削除されている
        db_entry = await AllowedEmail.get(entry.id)
        assert db_entry is None

    async def test_delete_nonexistent_allowed_email_returns_404(
        self, client, admin_user, admin_headers
    ):
        resp = await client.delete(
            "/api/v1/users/allowed-emails/000000000000000000000000",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    async def test_non_admin_cannot_access_allowed_emails(
        self, client, regular_user, user_headers
    ):
        resp = await client.get("/api/v1/users/allowed-emails/", headers=user_headers)
        assert resp.status_code == 403
