# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Claude Todo is a task management system with a Claude Code MCP server integration. Three independent services communicate via HTTP/Redis:

- **backend/** — Python FastAPI REST API (port 8000)
- **frontend/** — React + TypeScript SPA (port 3000)
- **mcp/** — FastMCP server for Claude Code tool access (port 8001)
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

### Docker Compose (full stack)
```bash
cp .env.example .env             # Configure SECRET_KEY, MCP_INTERNAL_SECRET, Google OAuth
docker compose up -d             # Start all services
docker compose down              # Stop
```

## Architecture

### Authentication Flow
- **Admin users**: Email/password with bcrypt → JWT (access 60min + refresh 7 days)
- **Regular users**: Google OAuth → requires pre-registered email in `allowed_emails` collection
- **MCP server**: `X-API-Key` header → validated against `mcp_api_keys` collection
- **MCP→Backend internal calls**: `X-MCP-Internal-Secret` shared secret

### API Routes
- `/api/v1/auth/` — Login, Google OAuth, token refresh
- `/api/v1/users/`, `/projects/`, `/tasks/`, `/mcp_keys/` — CRUD
- `/api/v1/events?token=<jwt>` — SSE (token in URL because EventSource can't send custom headers)
- `/api/v1/internal/` — MCP-to-backend endpoints secured by `X-MCP-Internal-Secret`
- `/mcp` — MCP stateful HTTP endpoint (proxied by nginx)

### Backend Patterns
- **ORM**: Beanie documents (MongoDB) with custom `save_updated()` for auto-timestamps
- **Redis**: Pub/sub for SSE events (`todo:events` channel), separate DBs for app/mcp/sse
- **Response**: `ORJSONResponse` as default response class
- **Config**: `pydantic-settings` BaseSettings, reads from env vars / `.env`
- **main.py**: Exits on startup if `SECRET_KEY` or `MCP_INTERNAL_SECRET` are default values

### Frontend Patterns
- **State**: Zustand for auth, React Query for server state
- **API**: Axios with interceptors for JWT auto-attach and token refresh
- **Routing**: React Router v6 with `ProtectedRoute` and `AdminRoute` guards
- **Styling**: Tailwind CSS, icons from lucide-react

### MCP Server Patterns
- **Framework**: FastMCP 2.3+ with stateful HTTP transport
- **Session persistence**: `RedisEventStore` for SSE session resumption (`Last-Event-ID`)
- **Single worker**: Required for stateful SSE (do not increase uvicorn workers)
- **Backend communication**: `backend_request()` helper using `X-MCP-Internal-Secret`
- **Trailing slash**: `McpTrailingSlashMiddleware` handles `/mcp` → `/mcp/` redirect

### Database Collections
`users`, `projects` (with embedded `members`), `tasks` (with embedded `comments`), `allowed_emails`, `mcp_api_keys`

### Testing
- **Backend**: pytest-asyncio with `mongomock-motor` + `fakeredis` (mock mode, default). Set `TEST_MODE=real` for real DB tests.
- **conftest.py**: Session-scoped DB init, function-scoped collection cleanup, pre-built fixtures (`admin_user`, `regular_user`, `admin_token`, `test_project`)
- **Frontend**: Vitest + Testing Library + MSW for API mocking
