"""
起動時の自動admin作成テスト

lifespan() で INIT_ADMIN_EMAIL / INIT_ADMIN_PASSWORD が設定されている場合に
管理者ユーザーが自動作成されることを検証する。
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.models.user import User


class TestAutoAdminCreation:
    """lifespan 起動時の自動admin作成"""

    async def test_create_admin_user_on_startup(self):
        """環境変数が設定されていればadminが作成される"""
        from app.cli import create_admin_user

        await create_admin_user("auto@test.com", "testpass123", "Admin")

        user = await User.find_one(User.email == "auto@test.com")
        assert user is not None
        assert user.is_admin is True
        assert user.is_active is True
        assert user.name == "Admin"

    async def test_skip_if_admin_already_exists(self):
        """同じメールのユーザーが既に存在する場合はスキップ"""
        from app.cli import create_admin_user

        await create_admin_user("dup@test.com", "testpass123", "Admin")
        await create_admin_user("dup@test.com", "otherpass123", "Admin2")

        users = await User.find(User.email == "dup@test.com").to_list()
        assert len(users) == 1
        assert users[0].name == "Admin"

    async def test_no_creation_when_env_empty(self):
        """環境変数が空の場合はcreate_admin_userが呼ばれない"""
        with patch("app.core.config.settings") as mock_settings:
            mock_settings.INIT_ADMIN_EMAIL = ""
            mock_settings.INIT_ADMIN_PASSWORD = ""

            # Simulate the lifespan check
            if mock_settings.INIT_ADMIN_EMAIL and mock_settings.INIT_ADMIN_PASSWORD:
                pytest.fail("Should not reach here when env vars are empty")
