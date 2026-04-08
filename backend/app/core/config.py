from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    UPLOADS_DIR: str = str(Path(__file__).resolve().parents[3] / "uploads")
    DOCSITE_ASSETS_DIR: str = str(Path(__file__).resolve().parents[3] / "docsite_assets")
    DOCSITE_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_docsites")
    BOOKMARK_ASSETS_DIR: str = str(Path(__file__).resolve().parents[3] / "bookmark_assets")
    BOOKMARK_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_bookmarks")
    SEARCH_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index")
    KNOWLEDGE_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_knowledge")
    DOCUMENT_INDEX_DIR: str = str(Path(__file__).resolve().parents[3] / "search_index_documents")
    AGENT_RELEASES_DIR: str = str(Path(__file__).resolve().parents[3] / "agent_releases")
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DBNAME: str = "claude_todo"

    REDIS_URI: str = "redis://localhost:6379/0"
    REDIS_MCP_URI: str = "redis://localhost:6379/1"

    SECRET_KEY: str = "change-me"
    REFRESH_SECRET_KEY: str = "change-me-refresh"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""

    BASE_URL: str = ""

    FRONTEND_URL: str = "http://localhost:3000"

    WEBAUTHN_RP_ID: str = "localhost"
    WEBAUTHN_RP_NAME: str = "MCP Todo"
    WEBAUTHN_ORIGIN: str = "http://localhost:3000"

    COOKIE_DOMAIN: str = ""  # empty = auto from request
    COOKIE_SECURE: bool = False  # True for production HTTPS
    COOKIE_SAMESITE: str = "lax"  # lax: OAuth consent flow requires cross-site navigation
    COOKIE_PATH: str = "/"

    INIT_ADMIN_EMAIL: str = ""
    INIT_ADMIN_PASSWORD: str = ""

    # ── Login rate limiting ───────────────────────────────────
    # Per-email failed-login counter stored in Redis. Used to slow down
    # password brute-forcing without blocking legitimate developers who
    # mistype a password a few times. Defaults are intentionally generous
    # for a small-team internal tool — tighten in production via env.
    LOGIN_MAX_ATTEMPTS: int = 20
    LOGIN_LOCKOUT_SECONDS: int = 300  # 5 minutes

    # ── MCP tool usage tracking ───────────────────────────────
    # Hybrid bucket + event-log measurement of MCP tool calls.
    # See "MCP サーバー仕様" project document for details.
    MCP_USAGE_TRACKING_ENABLED: bool = True
    MCP_USAGE_SAMPLING_RATE: float = 0.05  # 5% sampling for non-error/non-slow events
    MCP_USAGE_SLOW_CALL_MS: int = 2000  # threshold for "slow call" event capture

    model_config = {"env_file": ("../.env", ".env"), "extra": "ignore"}


settings = Settings()
