from pathlib import Path
from urllib.parse import urlparse

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

    # ── Public URL configuration ──────────────────────────────
    #
    # Only two URLs are configured explicitly:
    #
    # - ``BASE_URL``    : the backend's public origin (used by the
    #                     MCP OAuth base, release download URLs, etc.)
    # - ``FRONTEND_URL``: the SPA's public origin (used by CORS, Google
    #                     OAuth redirect_uri, WebAuthn, and the agent
    #                     WebSocket Origin allowlist)
    #
    # In deployments where frontend and backend share an origin (nginx
    # reverse-proxies both), the two values will match — that's the
    # expected common case. Keep them as separate knobs so future
    # split-host deployments do not require a config migration.
    #
    # ``WEBAUTHN_ORIGIN`` / ``WEBAUTHN_RP_ID`` / ``WS_ALLOWED_ORIGINS``
    # used to be separate env vars. They were removed because they
    # were always set to the same thing as ``FRONTEND_URL`` in
    # practice, and the duplication made it easy to forget one
    # during a production URL change — a silent security regression.
    # They are now derived from ``FRONTEND_URL`` via the properties
    # below.
    BASE_URL: str = ""

    FRONTEND_URL: str = "http://localhost:3000"

    WEBAUTHN_RP_NAME: str = "MCP Todo"

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

    # ── MCP auth cache ────────────────────────────────────────
    # X-API-Key authentication result is cached in-process to avoid
    # hashing + DB lookup on every MCP call. Short TTL keeps the
    # window of stale "is_admin" flips small.
    MCP_AUTH_CACHE_TTL_SECONDS: int = 300

    # ── Remote agent: WebSocket keepalive ─────────────────────
    # Server-side ping cadence used to detect dead agent connections.
    # Defaults are intentionally conservative — most agents are on
    # stable LANs, but laptop agents over flaky Wi-Fi need this to
    # surface broken sockets within ~PING_INTERVAL+PING_TIMEOUT.
    AGENT_WS_PING_INTERVAL_SECONDS: int = 30
    AGENT_WS_PING_TIMEOUT_SECONDS: int = 10

    # ── Remote agent: MCP tool guards ─────────────────────────
    # Upper bounds applied by the backend MCP layer before forwarding
    # a request to the agent. The agent enforces its own equivalent
    # limits; these mirror them so we reject oversized payloads early
    # and so a misbehaving agent cannot drown the backend in output.
    REMOTE_MAX_OUTPUT_BYTES: int = 2 * 1024 * 1024  # 2 MB stdout/stderr cap
    REMOTE_MAX_FILE_BYTES: int = 5 * 1024 * 1024  # 5 MB single-file cap
    REMOTE_MAX_TIMEOUT_SECONDS: int = 300  # hard ceiling for remote_exec timeout
    REMOTE_DEFAULT_AGENT_WAIT_SECONDS: float = 5.0  # tolerate brief reconnects

    model_config = {"env_file": ("../.env", ".env"), "extra": "ignore"}

    # ── Derived URL properties ────────────────────────────────
    #
    # Kept as plain ``@property`` (not ``cached_property``) so tests
    # that mutate ``FRONTEND_URL`` via monkeypatch see the new value
    # immediately. The computation is trivial (string parse) so
    # caching would save microseconds at best.

    @property
    def webauthn_origin(self) -> str:
        """WebAuthn ``expected_origin`` — the SPA origin.

        WebAuthn binds credentials to the origin the user sees in
        their browser, which is always the frontend URL.
        """
        return self.FRONTEND_URL

    @property
    def webauthn_rp_id(self) -> str:
        """WebAuthn Relying Party ID — host component of ``FRONTEND_URL``.

        Per the WebAuthn spec, the RP ID is the effective domain
        (no scheme, no port). Sub-domain registration is possible by
        returning a parent domain here, but that is not the common
        case and is deliberately out of scope — operators who need
        it can set ``FRONTEND_URL`` to the parent and override this
        property.
        """
        host = urlparse(self.FRONTEND_URL).hostname
        return host or "localhost"

    @property
    def ws_allowed_origins(self) -> set[str]:
        """Agent WebSocket Origin allowlist.

        The agent ``/workspaces/agent/ws`` endpoint rejects browser
        connections whose ``Origin`` header is not in this set.
        Server-to-server agent callers send no ``Origin`` header and
        are unaffected; this is purely a CSWSH defense for browser
        callers.

        The allowlist is derived from ``FRONTEND_URL``; if you need
        multiple browser origins (uncommon), override this property.
        """
        return {self.FRONTEND_URL}


settings = Settings()
