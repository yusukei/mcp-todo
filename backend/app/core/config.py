"""Application settings.

Only values that genuinely differ between environments are exposed as
environment variables.  Everything else is a derived property or a
hardcoded constant — removing the temptation to configure what should
never be configured.

Required .env keys (5):
    SECRET_KEY          HMAC / JWT signing key  (≥32 chars)
    REFRESH_SECRET_KEY  Refresh-token signing key (≥32 chars)
    MONGO_URI           MongoDB connection string (includes DB name)
    REDIS_URI           Redis connection string for DB 0
    FRONTEND_URL        Public URL of the SPA (scheme+host, no trailing slash)

Optional .env keys:
    GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET   (only if Google OAuth is used)
    BASE_URL            Backend public origin; defaults to FRONTEND_URL
    INIT_ADMIN_EMAIL / INIT_ADMIN_PASSWORD    First-run admin bootstrap

Topology flags (set in docker-compose.yml, not in .env):
    ENABLE_API / ENABLE_INDEXERS / ENABLE_CLIP_QUEUE / ENABLE_ERROR_TRACKER_WORKER
"""

import re
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Required ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me"
    REFRESH_SECRET_KEY: str = "change-me-refresh"
    MONGO_URI: str = "mongodb://localhost:27017/claude_todo"
    REDIS_URI: str = "redis://localhost:6379/0"
    FRONTEND_URL: str = "http://localhost:3000"

    # ── Optional ──────────────────────────────────────────────────────────
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    # Backend public origin; falls back to FRONTEND_URL when empty
    # (correct for single-host deployments where both share the same URL).
    BASE_URL: str = ""
    INIT_ADMIN_EMAIL: str = ""
    INIT_ADMIN_PASSWORD: str = ""

    # ── Topology flags (docker-compose only, not in .env) ─────────────────
    # These gate which subsystems start in a given container.
    # Defaults are all True so a plain `python -m uvicorn app.main:app`
    # starts everything (single-process development mode).
    ENABLE_API: bool = True
    ENABLE_INDEXERS: bool = True
    ENABLE_CLIP_QUEUE: bool = True
    ENABLE_ERROR_TRACKER_WORKER: bool = True

    # ── Error tracker self-capture enrichment ─────────────────────────────
    ENVIRONMENT: str = "production"
    RELEASE: str = ""

    model_config = {"env_file": ("../.env", ".env"), "extra": "ignore"}

    # ── Derived: database ─────────────────────────────────────────────────

    @property
    def MONGO_DBNAME(self) -> str:
        """DB name parsed from MONGO_URI path component."""
        path = urlparse(self.MONGO_URI).path.lstrip("/")
        name = path.split("?")[0] or "claude_todo"
        return name

    @property
    def REDIS_MCP_URI(self) -> str:
        """Redis DB 1 for MCP sessions — derived from REDIS_URI by
        replacing the database number."""
        return re.sub(r"/\d+$", "/1", self.REDIS_URI)

    # ── Derived: file paths (Docker = /app/...) ───────────────────────────
    # Defaults point to the well-known Docker volume paths.
    # Override in .env only for non-Docker local development.

    @property
    def SEARCH_INDEX_DIR(self) -> str:
        return "/app/search_index"

    @property
    def DOCSITE_ASSETS_DIR(self) -> str:
        return "/app/docsite_assets"

    @property
    def DOCSITE_INDEX_DIR(self) -> str:
        return "/app/search_index_docsites"

    @property
    def BOOKMARK_ASSETS_DIR(self) -> str:
        return "/app/bookmark_assets"

    @property
    def BOOKMARK_INDEX_DIR(self) -> str:
        return "/app/search_index_bookmarks"

    @property
    def KNOWLEDGE_INDEX_DIR(self) -> str:
        return "/app/search_index_knowledge"

    @property
    def DOCUMENT_INDEX_DIR(self) -> str:
        return "/app/search_index_documents"

    @property
    def AGENT_RELEASES_DIR(self) -> str:
        return "/app/agent_releases"

    @property
    def UPLOADS_DIR(self) -> str:
        return "/app/uploads"

    # ── Derived: auth & cookies ───────────────────────────────────────────

    @property
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        return 60

    @property
    def REFRESH_TOKEN_EXPIRE_DAYS(self) -> int:
        return 7

    @property
    def COOKIE_SECURE(self) -> bool:
        """True when FRONTEND_URL uses HTTPS (i.e. production)."""
        return self.FRONTEND_URL.startswith("https://")

    @property
    def COOKIE_DOMAIN(self) -> str:
        return ""  # empty = set from request host automatically

    @property
    def COOKIE_SAMESITE(self) -> str:
        return "lax"

    @property
    def COOKIE_PATH(self) -> str:
        return "/"

    @property
    def WEBAUTHN_RP_NAME(self) -> str:
        return "MCP Todo"

    # ── Derived: WebAuthn (from FRONTEND_URL) ─────────────────────────────

    @property
    def webauthn_origin(self) -> str:
        return self.FRONTEND_URL

    @property
    def webauthn_rp_id(self) -> str:
        host = urlparse(self.FRONTEND_URL).hostname
        return host or "localhost"

    @property
    def ws_allowed_origins(self) -> set[str]:
        return {self.FRONTEND_URL}

    # ── Derived: login rate limiting ──────────────────────────────────────

    @property
    def LOGIN_MAX_ATTEMPTS(self) -> int:
        return 20

    @property
    def LOGIN_LOCKOUT_SECONDS(self) -> int:
        return 300

    # ── Derived: MCP auth cache ───────────────────────────────────────────

    @property
    def MCP_AUTH_CACHE_TTL_SECONDS(self) -> int:
        return 30

    # ── Derived: MCP usage tracking ───────────────────────────────────────

    @property
    def MCP_USAGE_TRACKING_ENABLED(self) -> bool:
        return True

    @property
    def MCP_USAGE_SAMPLING_RATE(self) -> float:
        return 0.05

    @property
    def MCP_USAGE_SLOW_CALL_MS(self) -> int:
        return 2000

    # ── Derived: remote agent ─────────────────────────────────────────────

    @property
    def REMOTE_MAX_OUTPUT_BYTES(self) -> int:
        return 2 * 1024 * 1024  # 2 MB

    @property
    def REMOTE_MAX_FILE_BYTES(self) -> int:
        return 5 * 1024 * 1024  # 5 MB

    @property
    def REMOTE_MAX_TIMEOUT_SECONDS(self) -> int:
        return 300

    @property
    def REMOTE_DEFAULT_AGENT_WAIT_SECONDS(self) -> float:
        return 5.0

    @property
    def AGENT_SHUTDOWN_DRAIN_TIMEOUT_SECONDS(self) -> float:
        return 60.0

    # ── Derived: error tracker ────────────────────────────────────────────

    @property
    def ERROR_TRACKER_STREAM_MAXLEN(self) -> int:
        return 100_000

    @property
    def ERROR_TRACKER_MAX_ENVELOPE_KB(self) -> int:
        return 1024

    @property
    def ERROR_TRACKER_WORKER_BATCH(self) -> int:
        return 64

    @property
    def ERROR_TRACKER_WORKER_BLOCK_MS(self) -> int:
        return 1000


settings = Settings()
