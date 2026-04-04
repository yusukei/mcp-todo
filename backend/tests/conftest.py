"""
テスト共通フィクスチャ

TEST_MODE 環境変数で mock/real を切り替える:
  mock (デフォルト): mongomock-motor + fakeredis で外部依存ゼロ
  real             : 実 MongoDB/Redis (docker-compose.test.yml 参照)

pytest-env が pyproject.toml の [tool.pytest.ini_options].env で
SECRET_KEY 等を事前設定するため、main.py の sys.exit チェックは安全にパスできる。
"""

import os

TEST_MODE = os.environ.get("TEST_MODE", "mock")

# --- Mock モード用: インポート時に同期的に生成 ---
if TEST_MODE == "mock":
    from mongomock_motor import AsyncMongoMockClient as _MockMongoClient
    import fakeredis.aioredis as _fakeredis_aioredis

    _mock_mongo = _MockMongoClient()
    _fake_redis = _fakeredis_aioredis.FakeRedis(decode_responses=True)

    # Redis グローバルクライアントを即時パッチ (init_redis() の代わりに fakeredis を注入)
    import app.core.redis as _redis_module
    _redis_module._client = _fake_redis

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models import AllowedEmail, Bookmark, BookmarkCollection, DocPage, DocSite, DocumentVersion, Knowledge, McpApiKey, Project, ProjectDocument, Task, User
from app.models.project import ProjectMember
from app.models.user import AuthType
from app.core.redis import get_redis
from app.core.security import create_access_token, hash_password


# ---------------------------------------------------------------------------
# Session スコープ: DB / Redis の初期化
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="session", autouse=True)
async def _setup_infra():
    """DB と Redis をテストセッション開始時に一度だけ初期化する"""
    from beanie import init_beanie

    if TEST_MODE == "mock":
        db = _mock_mongo["claude_todo_test"]
    else:
        import motor.motor_asyncio
        import redis.asyncio as aioredis
        import app.core.redis as redis_module

        mongo_uri = os.environ.get("MONGO_URI", "mongodb://localhost:27018")
        redis_uri = os.environ.get("REDIS_URI", "redis://localhost:6380/1")

        _real_mongo = motor.motor_asyncio.AsyncIOMotorClient(mongo_uri)
        _real_redis = aioredis.from_url(redis_uri, decode_responses=True)
        redis_module._client = _real_redis
        db = _real_mongo["claude_todo_test"]

    await init_beanie(
        database=db,
        document_models=[User, AllowedEmail, Project, Task, McpApiKey, Knowledge, ProjectDocument, DocumentVersion, DocSite, DocPage, Bookmark, BookmarkCollection],
    )
    yield

    if TEST_MODE != "mock":
        import app.core.redis as redis_module
        if redis_module._client:
            await redis_module._client.aclose()
            redis_module._client = None


# ---------------------------------------------------------------------------
# Session スコープ: HTTP クライアント
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_app(_setup_infra):
    """テスト用 FastAPI アプリ (lifespan なし、ルーターのみ)"""
    from app.api.v1.endpoints import attachments, auth, bookmark_assets, bookmarks, docsites, documents, events, knowledge, mcp_keys, projects, tasks, users

    app = FastAPI()
    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(users.router, prefix="/api/v1")
    app.include_router(projects.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(mcp_keys.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(attachments.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    app.include_router(docsites.router, prefix="/api/v1")
    app.include_router(bookmarks.coll_router, prefix="/api/v1")
    app.include_router(bookmarks.bm_router, prefix="/api/v1")
    app.include_router(bookmark_assets.router, prefix="/api/v1")
    return app


@pytest_asyncio.fixture(scope="session")
async def client(test_app):
    """セッション全体で共有する httpx AsyncClient"""
    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Function スコープ: 各テスト前にコレクションをクリア (autouse)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def reset_db(_setup_infra):
    """各テスト前に全コレクションを空にし、Redis もフラッシュする"""
    for model in [User, Project, Task, AllowedEmail, McpApiKey, Knowledge, ProjectDocument, DocumentVersion, DocSite, DocPage, Bookmark, BookmarkCollection]:
        await model.find({}).delete()
    redis = get_redis()
    await redis.flushdb()
    yield


# ---------------------------------------------------------------------------
# Function スコープ: テスト用ユーザー / トークン / プロジェクト
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_user():
    user = User(
        email="admin@test.com",
        name="Admin User",
        auth_type=AuthType.admin,
        password_hash=hash_password("adminpass"),
        is_admin=True,
        is_active=True,
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def regular_user():
    user = User(
        email="user@test.com",
        name="Regular User",
        auth_type=AuthType.google,
        is_admin=False,
        is_active=True,
    )
    await user.insert()
    return user


@pytest_asyncio.fixture
async def inactive_user():
    user = User(
        email="inactive@test.com",
        name="Inactive User",
        auth_type=AuthType.admin,
        password_hash=hash_password("pass"),
        is_admin=False,
        is_active=False,
    )
    await user.insert()
    return user


@pytest.fixture
def admin_token(admin_user):
    return create_access_token(str(admin_user.id))


@pytest.fixture
def user_token(regular_user):
    return create_access_token(str(regular_user.id))


@pytest.fixture
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture
def user_headers(user_token):
    return {"Authorization": f"Bearer {user_token}"}


@pytest_asyncio.fixture
async def test_project(admin_user, regular_user):
    """admin + regular_user がメンバーのテストプロジェクト"""
    project = Project(
        name="Test Project",
        description="Test description",
        color="#6366f1",
        created_by=admin_user,
        members=[
            ProjectMember(user_id=str(admin_user.id)),
            ProjectMember(user_id=str(regular_user.id)),
        ],
    )
    await project.insert()
    return project
