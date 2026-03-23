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
- `/api/v1/events?token=<jwt>` — SSE (token in URL because EventSource can't send custom headers)
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
`users`, `projects` (with embedded `members`), `tasks` (with embedded `comments`), `allowed_emails`, `mcp_api_keys`

### Task Management（必須）
- **タスク管理は必ず mcp-todo MCP サーバーを使用すること**（TodoWrite ではなく MCP ツールを使う）
- 作業で発生したタスクは mcp-todo アプリに MCP サーバー経由 (`/mcp/`) で登録すること
- 完了したタスクも MCP サーバー経由で status を `done` に更新すること
- MCP 呼び出し手順: `initialize` → `tools/call` (JSON-RPC over Streamable HTTP)
- 認証: `X-API-Key` ヘッダーに MCP API キーを付与
- 一括登録には `batch_create_tasks`、一括更新には `batch_update_tasks`、単体完了には `complete_task` を使用

#### セッション開始時のワークフロー（推奨）
セッション開始時にユーザーから作業指示がない場合、`get_work_context` を呼んで現状を把握すること：
- **approved**: 承認済みで実装待ちのタスク
- **in_progress**: 進行中のタスク
- **overdue**: 期限超過タスク
- **needs_detail**: 調査が必要なタスク

タスクの詳細コンテキストが必要な場合は `get_task_context` を使用すること（get_task + get_subtasks + get_task_activity の3回呼び出しを1回に削減）。

#### MCP 接続が利用できない場合の対処（必須）
セッション開始時に mcp-todo MCP サーバーのツールが利用できない場合、以下を必ず実施すること：
1. `.mcp.json` の設定を確認（URL・API キーが正しいか）
2. サーバーの稼働状態を確認（`curl -s https://todo.vtech-studios.com/health` または `docker compose ps`）
3. サーバーが停止中なら `docker compose up -d` で起動
4. nginx レートリミット（30r/m）に引っかかっていないか確認
5. 上記で解決しない場合、ユーザーにセッション再起動（`/mcp` で状態確認後 `/exit` → 再起動）を提案
6. **接続問題を放置して TodoWrite 等で代替しないこと**

### Git
- コミット時に `Co-Authored-By` トレーラーを付与しない

### Testing
- **Backend**: pytest-asyncio with `mongomock-motor` + `fakeredis` (mock mode, default). Set `TEST_MODE=real` for real DB tests.
- **conftest.py**: Session-scoped DB init, function-scoped collection cleanup, pre-built fixtures (`admin_user`, `regular_user`, `admin_token`, `test_project`)
- **Frontend**: Vitest + Testing Library + MSW for API mocking
