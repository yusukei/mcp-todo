# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Todo is a task management system with a Claude Code MCP server integration. The MCP server is embedded in the backend (single process):

- **backend/** — Python FastAPI REST API + MCP server (port 8000)
- **frontend/** — React + TypeScript SPA (port 3000)
- **nginx/** — Reverse proxy routing (port 80)

## Commands

### Backend (Python / uv)
```bash
cd backend
uv sync                          # Install dependencies
uv run pytest                    # Run all tests (mock mode, no external deps)
uv run pytest tests/test_auth.py # Run single test file
uv run pytest -k "test_login"    # Run tests matching pattern
uv run pytest --cov              # Run with coverage (70% minimum)
uv run pytest tests/test_audit.py # Run pip-audit vulnerability check
TEST_MODE=real uv run pytest     # Run against real MongoDB/Redis (requires docker-compose.test.yml)
```

### Frontend (Node / npm)
```bash
cd frontend
npm install
npm run dev                      # Dev server (Vite, port 3000)
npm run build                    # tsc + vite build
npm test                         # vitest run
npm run test:watch               # vitest in watch mode
npm run test:coverage            # vitest with coverage
```

### Initial Admin Setup
```bash
cd backend
# Interactive (prompts for email/password)
uv run python -m app.cli init-admin

# With arguments
uv run python -m app.cli init-admin --email admin@example.com --password 'yourpass8+'

# Via env vars (INIT_ADMIN_EMAIL / INIT_ADMIN_PASSWORD in .env)
uv run python -m app.cli init-admin

# Docker
docker compose exec backend uv run python -m app.cli init-admin
```

### Backup / Restore (mongodump/mongorestore)
```bash
cd backend
uv run python -m app.cli backup                    # Export to backup_YYYY-MM-DD_HH-MM-SS.agz
uv run python -m app.cli backup -o my_backup.agz   # Custom output path
uv run python -m app.cli restore backup.agz --confirm  # Restore (replaces all data)
```
API endpoints (admin-only):
- `POST /api/v1/backup/export` — download .agz backup file
- `POST /api/v1/backup/import` — upload .agz file to restore (multipart form, field: `file`)

### DocSite Import (external documentation)
```bash
cd backend
# Import a documentation site from a local directory
uv run python -m app.cli import-docsite ./tmp/PICO/docs_ja \
  --name "PICO Developer Docs" \
  --source-url "https://developer.picoxr.com" \
  --description "PICO XR developer documentation (Japanese)"

# Docker
docker compose exec backend uv run python -m app.cli import-docsite /path/to/docs \
  --name "Site Name"
```

### Docker Compose (full stack)
```bash
cp .env.example .env             # Configure SECRET_KEY, Google OAuth
docker compose up -d             # Start all services
docker compose down              # Stop
```

## Architecture

### Authentication Flow
- **Admin users**: Email/password with bcrypt → JWT (access 60min + refresh 7 days)
- **Regular users**: Google OAuth → requires pre-registered email in `allowed_emails` collection
- **MCP tools**: `X-API-Key` header → validated directly against `mcp_api_keys` collection (no internal HTTP)

### API Routes
- `/api/v1/auth/` — Login, Google OAuth, token refresh
- `/api/v1/users/`, `/projects/`, `/tasks/`, `/mcp_keys/` — CRUD
- `POST /api/v1/events/ticket` — Issue short-lived SSE ticket (JWT Bearer auth)
- `/api/v1/events?ticket=<ticket>` — SSE (uses one-time ticket to avoid JWT exposure in URLs)
- `/api/v1/docsites/` — DocSite listing/detail, page content, search
- `/api/v1/docsites/{id}/assets/{path}` — DocSite static assets (images)
- `/mcp` — MCP stateful HTTP endpoint (embedded in backend, proxied by nginx)
- `/.well-known/oauth-*` — MCP OAuth discovery metadata (manually registered)

### Backend Patterns
- **ORM**: Beanie documents (MongoDB) with custom `save_updated()` for auto-timestamps
- **Redis**: Pub/sub for SSE events (`todo:events` channel), DB 0 for app, DB 1 for MCP sessions
- **Response**: `ORJSONResponse` as default response class
- **Config**: `pydantic-settings` BaseSettings, reads from env vars / `.env`
- **main.py**: Exits on startup if `SECRET_KEY` or `REFRESH_SECRET_KEY` are default values

### Frontend Patterns
- **State**: Zustand for auth, React Query for server state
- **API**: Axios with interceptors for JWT auto-attach and token refresh
- **Routing**: React Router v6 with `ProtectedRoute` and `AdminRoute` guards
- **Styling**: Tailwind CSS, icons from lucide-react

### MCP Server (embedded in backend)
- **Location**: `backend/app/mcp/` package
- **Framework**: FastMCP 2.3+ with stateful HTTP transport
- **Mounting**: FastMCP app mounted at `/mcp` in backend's `lifespan()`
- **Session persistence**: `RedisEventStore` (Redis DB 1) for SSE session resumption
- **Session recovery**: `ResilientSessionManager` re-creates transports for unknown session IDs after restart
- **Authentication**: `authenticate()` in `mcp/auth.py` validates X-API-Key directly against DB (no internal HTTP)
- **Tools access DB directly**: MCP tools use Beanie models, no intermediate HTTP calls
- **Trailing slash**: `McpTrailingSlashMiddleware` handles `/mcp` → `/mcp/` (307 drops auth headers)
- **Well-known**: Manually registered at root level via `get_well_known_routes()`
- **PROHIBITED**: `stateless_http=True` を使用しないこと。stateful モード + RedisEventStore を維持する

### Database Collections
`users`, `projects` (with embedded `members`), `tasks` (with embedded `comments`), `allowed_emails`, `mcp_api_keys`, `doc_sites` (with embedded `sections` tree), `doc_pages`

### Task Management
- Task management uses the mcp-todo MCP server (see MCP instructions for tool usage details)
- At session start with no specific instructions, call `get_work_context` to check approved/in_progress/overdue/needs_detail tasks
- Use `get_task_context` for detailed task context (combines get_task + get_subtasks + get_task_activity)
- When MCP connection is unavailable, this project-specific troubleshooting applies:
  1. Check `.mcp.json` config (URL and API key)
  2. `curl -s https://todo.vtech-studios.com/health` or `docker compose ps`
  3. If server is down: `docker compose up -d`
  4. If unresolved, restart session (`/mcp` to check status, then `/exit` and restart)
  6. **Never fall back to TodoWrite — fix the connection**

### Development Workflow
Before modifying code or configuration files:
1. **Task first** — Ensure a task exists via `create_task` (exception: trivial typo/formatting fixes)
2. **Docs first** — Search project documents (`search_documents`) and update relevant specs BEFORE implementation
3. **Implement** — Follow the updated specs; record significant decisions as task comments
4. **Test** — Run the test suite and verify all tests pass
5. **Spec review** — Compare the diff against project documents; fix discrepancies before completing
6. **Complete** — Mark the task done via `complete_task` with a completion report

### Coding Rules

These rules apply to **all** code in this repository (backend, frontend,
agent). Violations are treated as bugs even if tests pass.

#### No silent fallbacks
**Fallbacks are prohibited as a default design choice.** If a primary
mechanism fails, surface the failure as an error — do **not** quietly
substitute a slower, less correct, or less capable alternative and
pretend the request succeeded.

- Do **not** add a "fallback" branch unless the user has explicitly
  approved it for that specific case. When you think a fallback is
  unavoidable, **stop and ask the user first** before writing it.
- Reasons fallbacks are harmful here:
  - They mask the real failure, making diagnosis impossible. The
    user sees "it worked, but slowly" instead of "X is broken, fix it."
  - They keep dead code paths alive that nobody tests in production.
  - They turn loud, recoverable failures into silent, persistent
    degradation.
- If a dependency (binary, service, library) is required for a feature
  to work correctly, **require it**. Document the requirement, log
  loudly at startup if it is missing, and return a clear error from
  the affected operations until it is installed/restored.

#### No error hiding
**`try`/`except` blocks that swallow or downgrade errors are
prohibited.** Errors must be handled as errors.

- Do **not** write `except Exception: pass`, `except Exception: return
  None`, `except Exception: continue`, or any equivalent that turns a
  failure into silence.
- Do **not** catch broad `Exception` just to log and move on. If you
  cannot meaningfully recover, let it propagate.
- Acceptable patterns:
  - Catch a **specific** exception type that represents a known,
    expected failure mode, and handle it explicitly (return a
    structured error, retry with backoff, etc.).
  - Catch at a **boundary** (HTTP handler, WebSocket dispatcher, MCP
    tool entry point) where the error must be converted into a
    protocol-level response — and in that case log the full traceback
    via `logger.exception(...)`, never `logger.error(...)` without
    `exc_info`.
- Never use `errors="ignore"` on text decoding, `json.JSONDecodeError:
  continue` in a parse loop, or similar patterns that throw away data
  on the floor. If the input is malformed, that is information the
  operator needs.
- Frontend equivalents apply: no empty `.catch(() => {})`, no
  `try { ... } catch { /* ignore */ }` in TS/JS.

When in doubt: **let it crash and surface the real problem.** A loud
failure that points at the cause is always better than a quiet
degradation that hides it.

### Git
- Do not add `Co-Authored-By` trailer to commits
- Include the task ID in commit messages for traceability (e.g., `feat: add versioning to documents [task:69c22641]`)

### Testing
- **Backend**: pytest-asyncio with `mongomock-motor` + `fakeredis` (mock mode, default). Set `TEST_MODE=real` for real DB tests.
- **conftest.py**: Session-scoped DB init, function-scoped collection cleanup, pre-built fixtures (`admin_user`, `regular_user`, `admin_token`, `test_project`)
- **Frontend**: Vitest + Testing Library + MSW for API mocking
