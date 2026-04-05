"""
/health エンドポイントのテスト

MongoDB ping と Redis ping の結果に応じて 200 / 503 を返すことを検証する。
conftest.py の test_app には /health が含まれないため、
main.py の health() を直接インポートし、依存関数をモックする。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestHealthEndpoint:
    """GET /health のテスト"""

    async def test_health_ok_when_both_services_healthy(self):
        """MongoDB・Redis 両方が正常なら 200 + status=ok"""
        # conftest の test_app には /health が無いため、
        # main.py の health 関数を直接呼び出す
        from app.main import health

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with (
            patch("app.main.get_mongo_client", return_value=mock_client),
            patch("app.main.get_redis", return_value=mock_redis),
        ):
            response = await health()

        assert response.status_code == 200
        body = response.body.decode()
        import json
        data = json.loads(body)
        assert data["status"] == "ok"
        assert data["mongo"] == "ok"
        assert data["redis"] == "ok"

    async def test_health_503_when_mongo_down(self):
        """MongoDB が down なら 503 + mongo=down"""
        from app.main import health

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(side_effect=Exception("connection refused"))

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with (
            patch("app.main.get_mongo_client", return_value=mock_client),
            patch("app.main.get_redis", return_value=mock_redis),
        ):
            response = await health()

        assert response.status_code == 503
        import json
        data = json.loads(response.body.decode())
        assert data["status"] == "unhealthy"
        assert data["mongo"] == "down"
        assert data["redis"] == "ok"

    async def test_health_503_when_redis_down(self):
        """Redis が down なら 503 + redis=down"""
        from app.main import health

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("connection refused"))

        with (
            patch("app.main.get_mongo_client", return_value=mock_client),
            patch("app.main.get_redis", return_value=mock_redis),
        ):
            response = await health()

        assert response.status_code == 503
        import json
        data = json.loads(response.body.decode())
        assert data["status"] == "unhealthy"
        assert data["mongo"] == "ok"
        assert data["redis"] == "down"

    async def test_health_503_when_both_down(self):
        """MongoDB・Redis 両方が down なら 503 + 両方 down"""
        from app.main import health

        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(side_effect=Exception("mongo down"))

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=Exception("redis down"))

        with (
            patch("app.main.get_mongo_client", return_value=mock_client),
            patch("app.main.get_redis", return_value=mock_redis),
        ):
            response = await health()

        assert response.status_code == 503
        import json
        data = json.loads(response.body.decode())
        assert data["status"] == "unhealthy"
        assert data["mongo"] == "down"
        assert data["redis"] == "down"

    async def test_health_ok_when_mongo_client_is_none(self):
        """get_mongo_client() が None を返しても mongo=ok (ping スキップ)"""
        from app.main import health

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        with (
            patch("app.main.get_mongo_client", return_value=None),
            patch("app.main.get_redis", return_value=mock_redis),
        ):
            response = await health()

        assert response.status_code == 200
        import json
        data = json.loads(response.body.decode())
        assert data["status"] == "ok"
        assert data["mongo"] == "ok"
        assert data["redis"] == "ok"
