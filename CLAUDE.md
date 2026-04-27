# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MCP Todo is a task management system with a Claude Code MCP server integration. Production runs a multi-worker topology with a sidecar for index writes:

- **backend/** — Python FastAPI REST API + MCP server (multi-worker, port 8000)
- **backend-indexer** — Sidecar: Tantivy index writes + clip queue (single-worker, same image)
- **frontend/** — React + TypeScript SPA (port 3000)
- **traefik/** — Reverse proxy routing (port 80). `traefik/traefik.yml` が静的設定、`traefik/dynamic/middlewares.yml` がミドルウェア定義。

## 仕様書 (docs/)

**コードを変更する前に必ず該当する仕様書を読むこと。** 仕様書と実装が乖離している場合、それは仕様書を更新すべきタイミング — 黙って実装を変えてはいけない。

| 対象 | 仕様書 | 変更が影響する範囲 |
|---|---|---|
| データモデル (MongoDB / Beanie) | [docs/data-models.md](docs/data-models.md) | API・MCPツール・フロントの型・テスト全て |
| REST API エンドポイント | [docs/api/endpoints.md](docs/api/endpoints.md) | フロント API クライアント、MCPツール、外部統合 |
| MCP ツール | [docs/mcp-tools/README.md](docs/mcp-tools/README.md) | Claude Code エージェントの挙動、`.mcp.json` 利用者全て |
| フロントエンド | [docs/frontend/guide.md](docs/frontend/guide.md) | UI 変更、ストア・hooks・ルーティング |
| マルチワーカー構成 | [docs/architecture/multi-worker-sidecar.md](docs/architecture/multi-worker-sidecar.md) | デプロイ・インデクサ・clip queue |
| 運用 (multi-worker) | [docs/runbook/multi-worker.md](docs/runbook/multi-worker.md) | 障害対応、ロールバック |
| エラートラッカー | [docs/error-tracker/README.md](docs/error-tracker/README.md) | エラー収集、PII スクラブ、DSN 管理 |
| URL Contract (frontend/backend 共通) | [docs/api/url-contract.md](docs/api/url-contract.md) | URL ↔ resource 解決、Copy URL UX、`lookup_url` MCP tool |

各仕様書の末尾には「変更時のチェックリスト」がある。コード変更時は該当チェックリストを上から順に確認すること。

## Commands

### Backend (Python / uv)
```bash
cd backend
uv sync                          # Install dependencies (ローカル開発環境セットアップのみ)
```

### Backend テスト (必ずコンテナで実行すること)

**ローカルで `uv run pytest` を直接実行することは禁止。** 必ず以下の Docker コマンドを使う。

```bash
# イメージのビルド (初回 / Dockerfile.test や pyproject.toml 変更時)
docker compose -f backend/docker-compose.test.yml build test

# テスト実行 — mock モード (外部 DB 不要、通常はこちらを使う)
docker compose -f backend/docker-compose.test.yml run --rm test

# テスト実行 — real モード (実 MongoDB / Redis を使う統合テスト)
docker compose -f backend/docker-compose.test.yml run --rm test-real

# 特定ファイルのみ実行
docker compose -f backend/docker-compose.test.yml run --rm test \
    uv run pytest tests/test_auth.py -v

# カバレッジ付き実行
docker compose -f backend/docker-compose.test.yml run --rm test \
    uv run pytest --cov --tb=short

# クリーンアップ (real モード用コンテナの停止)
docker compose -f backend/docker-compose.test.yml down -v
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
- **MCP tools — DUAL AUTH MANDATORY**: the MCP transport MUST accept and validate **both**:
  - `X-API-Key` header → validated against `mcp_api_keys` collection (used by Claude Code via `.mcp.json`)
  - OAuth 2.1 `Authorization: Bearer ...` → validated by `TodoOAuthProvider` (used by Claude Desktop / OAuth-capable clients)
  - Removing or breaking either path is a regression. Any change to MCP authentication code MUST be tested against both modes before merging.
  - `BASE_URL` env var MUST point at the public HTTPS origin (not localhost) so OAuth metadata advertises reachable URLs to Claude Desktop. A wrong `BASE_URL` causes `connection timed out after 30000ms` in OAuth-capable clients even when X-API-Key clients still work.
  - Per MCP spec, missing/invalid credentials → `401` with `WWW-Authenticate: Bearer ...` header so OAuth clients can discover the auth flow via `/.well-known/oauth-protected-resource/mcp/`.
  - See `backend/app/mcp/auth.py` module docstring and `docs/architecture/mcp-stateless-transport.md` §"Authentication" for details.

### API Routes

**完全な仕様: [docs/api/endpoints.md](docs/api/endpoints.md)** (HTTP 約90本 + WebSocket 2本)

カテゴリ別の概観のみ:
- `/api/v1/auth/` — ログイン、Google OAuth、トークンリフレッシュ
- `/api/v1/users/`, `/projects/`, `/tasks/`, `/mcp_keys/` — CRUD
- `/api/v1/knowledge/`, `/documents/`, `/bookmarks/`, `/bookmark_collections/` — コンテンツ管理
- `/api/v1/docsites/` — DocSite 一覧・ページ・検索・静的アセット
- `/api/v1/secrets/` — プロジェクトシークレット
- `/api/v1/error_tracker/` — エラートラッキング (Sentry SDK 互換 ingest は `/api/{project_id}/envelope/` でルートレベル)
- `/api/v1/remote/` — リモートエージェント管理 + WebSocket
- `/api/v1/chat/` — チャット + WebSocket
- `/api/v1/events?ticket=<ticket>` — SSE (one-time ticket で JWT を URL に露出させない)
- `POST /api/v1/events/ticket` — 短命 SSE チケット発行 (JWT Bearer)
- `/api/v1/backup/` — admin 専用バックアップ/リストア
- `/mcp` — MCP stateful HTTP (Traefik が `_sticky_mcp` cookie でルーティング)
- `/.well-known/oauth-*` — MCP OAuth discovery (ルートレベルに手動登録)

### Backend Patterns
- **ORM**: Beanie documents (MongoDB) with custom `save_updated()` for auto-timestamps
- **Redis**: Pub/sub for SSE events (`todo:events` channel), Streams for index notifications (`index:tasks`), DB 0 for app, DB 1 for MCP sessions
- **Response**: `ORJSONResponse` as default response class
- **Config**: `pydantic-settings` BaseSettings, reads from env vars / `.env`
- **main.py**: Exits on startup if `SECRET_KEY` or `REFRESH_SECRET_KEY` are default values

### Frontend Patterns

**完全な仕様: [docs/frontend/guide.md](docs/frontend/guide.md)** (ルーティング、ストア、hooks、SSE、共有コンポーネント、テスト方針)

- **State**: Zustand for auth, React Query for server state
- **API**: Fetch ベースのクライアント (JWT 自動付与 + クロスタブリフレッシュ)
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
- **PROHIBITED**: FastMCP の `stateless_http=True` kwarg を使用しないこと（per-request session id minting モードでセッション概念が失われる）。**ただし例外**: MCP SDK の `Server.run(read_stream, write_stream, init_options, stateless=True)` は *dispatch primitive* として許可（既に initialize 済みの session に対する単発 JSON-RPC 処理用）。v3 stateless transport (`docs/architecture/mcp-stateless-transport.md`) でセッション状態を Redis に外部化するため、SDK レベルの `stateless=True` を活用する

### Deployment Topology (Multi-Worker Sidecar)

The backend runs two containers from the same Docker image, differentiated by env vars:

| Container | `ENABLE_API` | `ENABLE_INDEXERS` | `ENABLE_CLIP_QUEUE` | `WEB_CONCURRENCY` |
|---|---|---|---|---|
| `backend` | 1 | 0 | 0 | 4 |
| `backend-indexer` | 0 | 1 | 1 | 1 |

- **Index writes** flow through Redis Streams (`index:tasks`): API workers publish via `XADD`, the indexer consumes via `XREADGROUP` and re-reads from MongoDB before updating Tantivy
- **Clip queue**: bookmark clipping runs only in the indexer sidecar
- **Auth cache**: `MCP_AUTH_CACHE_TTL_SECONDS=30` (short TTL for cross-worker consistency)
- **Emergency rollback**: `ENABLE_INDEXERS=1 ENABLE_CLIP_QUEUE=1 WEB_CONCURRENCY=1 docker compose up -d` + stop `backend-indexer`
- **Design doc**: `docs/architecture/multi-worker-sidecar.md`
- **Operator runbook**: `docs/runbook/multi-worker.md`

### Database Collections

**完全な仕様: [docs/data-models.md](docs/data-models.md)** (19 コレクション / 27 モデルクラス)

主要コレクション:
- コアドメイン: `users`, `allowed_emails`, `projects` (embed `members`), `tasks` (embed `comments`)
- 認証: `mcp_api_keys`
- コンテンツ: `project_documents`, `document_versions`, `knowledge`, `doc_sites` (embed `sections` tree), `doc_pages`, `bookmark_collections`, `bookmarks`
- チャット: `chat_sessions`, `chat_messages`
- リモート: `remote_agents`, `remote_exec_logs`, `agent_releases`
- シークレット: `project_secrets`, `secret_access_logs`
- エラートラッカー: `error_projects`, `error_issues`, `error_releases`, `error_audit_log`, `error_events_YYYYMMDD` (日別パーティション、Motor 直接アクセス)
- MCP 計測: `mcp_tool_usage_buckets`, `mcp_tool_call_events`, `mcp_api_feedback`

### Task Management

**MCP ツール完全仕様: [docs/mcp-tools/README.md](docs/mcp-tools/README.md)**
特に [tasks.md](docs/mcp-tools/tasks.md) には `needs_detail` / `approved` フラグの遷移ルール、サブタスクへの BFS カスケード伝播など、コードを読まないと分からないビジネスルールを明文化してある。

- Task management uses the mcp-todo MCP server (see MCP instructions for tool usage details)
- At session start with no specific instructions, call `get_work_context(project_id=...)` to check approved/in_progress/overdue/needs_detail tasks for a specific project. Cross-project queries are not supported — loop over projects from `list_projects` if you need a multi-project view.
- Use `get_task_context` for detailed task context (combines get_task + get_subtasks + get_task_activity)
- When MCP connection is unavailable, this project-specific troubleshooting applies:
  1. Check `.mcp.json` config (URL and API key)
  2. `curl -s https://todo.vtech-studios.com/health` or `docker compose ps`
  3. If server is down: `docker compose up -d`
  4. If unresolved, restart session (`/mcp` to check status, then `/exit` and restart)
  6. **Never fall back to TodoWrite — fix the connection**

### URL Handling (URL を見たら lookup_url)

**MCP ツール仕様: [docs/mcp-tools/url-lookup.md](docs/mcp-tools/url-lookup.md)**
**URL Contract 本体: [docs/api/url-contract.md](docs/api/url-contract.md)**

- mcp-todo の URL (`/projects/{pid}?task={tid}` 等) を渡されたら **`lookup_url` ツール** を使ってリソースを resolve する。`get_task` / `get_document` を別々に呼ぶより、URL → resource を一発で引き当てる方が漏れ・意図ズレが少ない。
- routing メタデータだけ欲しい場合は `lookup_url(url, follow=False)` か `parse_url(url)`。
- legacy URL `/workbench/{id}` は自動で `redirect_to: '/projects/{id}'` を返す。
- セキュリティ要件 (IDOR / 存在 oracle 統一 / rate limit 100/min / audit log) は MCP 層で自動。失敗時の応答は `{kind: "unknown", message: "Not found or access denied"}` で oracle を排除しているので、message から「不在」「拒否」を判別しないこと。
- 個人 layout 帰属の query (`?view=` / `?layout=` / `?group=`) は `had_unknown_params: true` で握り潰される。これらは URL ではなく Phase B `workbench_layouts` で個人 sync される。

### Development Workflow
Before modifying code or configuration files:
1. **Task first** — Ensure a task exists via `create_task` (exception: trivial typo/formatting fixes)
2. **Docs first** — Search project documents (`search_documents`) and update relevant specs BEFORE implementation
3. **Implement** — Follow the updated specs; record significant decisions as task comments
4. **Test** — Run the test suite and verify all tests pass
5. **Spec review** — Compare the diff against project documents; fix discrepancies before completing
6. **Complete** — Mark the task done via `complete_task` with a completion report

### Definition of "Correctly Working" (8 axes)

A feature is **"correctly working"** if and only if all eight of the
axes below hold for every behavior the specification promises. A
single axis failure means the feature is **not** working — even if
every other axis is satisfied. We do not call this "almost working"
or "mostly fine." It is broken on whichever axis fails, and the
work is not done until that axis passes.

This frame replaces ad-hoc "looks fine to me" judgments. It also
defines what counts as a **bug**: a bug is the observed failure of
one or more named axes. "Fix the bug" means "identify which axis
failed and restore it" — never "tweak code until the symptom goes
away."

#### Process axes (developer-side)

1. **Specified.** A written, unambiguous description of the
   behavior — inputs, outputs, error modes, invariants, persistence
   guarantees, UI affordances — exists in `docs/` or a project
   document. "I know what I meant" is not a specification. If the
   spec doesn't say it, the system is not promising it.

2. **Tested.** Every invariant in the spec is encoded as an
   automated test that fails when the spec is violated. Tests
   written *after* the implementation do not satisfy this axis —
   they encode whatever the code happened to do, not the spec.
   Vacuous tests (`expect(true).toBe(true)`, mocks asserting
   themselves, render-only with no behavior assertion) are not
   counted.

3. **Implemented.** Code passes the tests. The tests must turn
   green for the right reason: because the code does what the spec
   says, not because the assertion was loosened.

4. **Shipped.** The deployed copy in the user's environment is the
   version where axes 1–3 hold. A passing test on a stale build is
   not protection. Verify the bundle / image hash you are testing
   against matches the user's runtime.

#### User axes (observable-side)

5. **Reachable.** Every entry point the spec promises (button,
   menu item, route, hotkey, empty-state CTA, tab, drag target)
   is present in the live UI, visible, enabled under the right
   conditions, and actually invokes the documented behavior.
   "The code path exists" is not enough — the user must be able
   to find and trigger it through normal interaction without
   inside knowledge.

6. **Operable.** Triggering the entry point produces the result
   the spec describes. A blank canvas, a flicker that resolves to
   nothing, a button that responds with no observable change, a
   redraw with the wrong content — all are axis-6 failures, not
   "minor visual issues." If the user expected to see a terminal
   prompt and sees an empty box, the feature is not operable.

7. **Persistent.** State changes the spec promises will survive
   (reload, navigation, project switch, cross-device sync, focus
   loss) actually do survive — for the time window the spec
   promises, with the cardinality the spec promises, and with no
   silent corruption. "It survives sometimes" or "survives if you
   wait 500 ms before reloading" are axis-7 failures.

8. **Recoverable.** Every failure mode the spec acknowledges
   (network drop, permission denied, missing prerequisite,
   server 500, agent offline) appears as a defined UI state with
   text the user can act on **and** a path forward (retry button,
   settings link, reload affordance, contact-admin hint). A
   missing prerequisite that produces a blank screen is an
   axis-8 failure. Silent fallbacks that hide the failure are
   forbidden — see the existing "No silent fallbacks" rule.

#### Order of operations (process axes 1 → 4)

The process axes are **strictly ordered**. Skipping a step is the
single most common cause of axis-6/7/8 regressions reaching the
user. Do not start the next axis until the previous one is
demonstrably complete.

   1. Write/update the spec (axis 1) in `docs/`. It must read as
      a contract someone else could verify against.
   2. Write tests (axis 2) that fail against the current code.
      Run them and confirm they are RED.
   3. Implement (axis 3). Make exactly the failing tests pass —
      no more, no less.
   4. Ship (axis 4). Verify the deployed artifact contains the
      change (grep the built bundle, check the container image,
      confirm the user-visible URL serves the new asset).

#### What "done" means

A task is **done** when:

- All 8 axes hold for every behavior the task promised.
- The task comment explicitly names each axis verification (link
  to spec section, test file, deployment artifact, manual UI
  check, recovery-path screenshot or note). "Tests pass and I
  built it" is insufficient — say which axes you verified and
  how.
- If any axis cannot be verified yet, the task is **not done**;
  it stays `in_progress` with the unverified axis listed.

#### Bug triage

When a user reports a problem:

1. Identify the failing axis (or axes). Do not start coding.
2. State the failure in axis terms in the task comment
   ("axis 6 Operable: Terminal pane mounts but renders blank
   when agent is bound — spec promised connect-status banner
   within 1 s").
3. Confirm axes 1 + 2 cover the case. If the spec or test does
   not name the failure, **add the spec line and the failing
   test first** before touching implementation. (This is the
   only way to prevent the same bug from recurring.)
4. Then proceed through axes 3 → 4 to ship the fix.

A "bug fix" that doesn't add the missing spec/test is a future
regression in waiting.

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

#### Environment variable discipline

**Only values that genuinely differ between environments belong in env
vars.** Everything else must be a `@property` (derived from other
settings) or a hardcoded constant in code.

Required `.env` keys for this project (5):

| Key | Purpose |
|---|---|
| `SECRET_KEY` | JWT access-token signing (≥32 chars) |
| `REFRESH_SECRET_KEY` | JWT refresh-token signing (≥32 chars) |
| `MONGO_URI` | MongoDB connection string (includes DB name) |
| `REDIS_URI` | Redis connection string (DB 0) |
| `FRONTEND_URL` | Public SPA URL (scheme+host, no trailing slash) |

Optional (only when the feature is used):
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — Google OAuth
- `BASE_URL` — backend public origin (defaults to `FRONTEND_URL`)
- `INIT_ADMIN_EMAIL` / `INIT_ADMIN_PASSWORD` — first-run bootstrap

Topology flags (`ENABLE_API`, `ENABLE_INDEXERS`, `ENABLE_CLIP_QUEUE`,
`ENABLE_ERROR_TRACKER_WORKER`, `WEB_CONCURRENCY`) belong **only in
`docker-compose.yml`**, not in `.env`.

Rules:
- Do **not** add a new env var for a value that can be derived from an
  existing one (e.g., parse the DB name from `MONGO_URI`, derive
  `REDIS_MCP_URI` by replacing the DB index).
- Do **not** add a new env var for a tuning constant (timeouts, rate
  limits, batch sizes). Hardcode it in `config.py` as a `@property`
  and document its value in a comment.
- Do **not** add a new env var for a file path that is fixed inside
  Docker (e.g., `/app/search_index`). These are deployment constants,
  not configuration.
- Never overload a single secret for multiple security purposes. If JWT
  signing and encryption both need a key, they must use **separate**
  named keys (`SECRET_KEY` vs `ENCRYPTION_KEY`). Overloading forces
  both systems to rotate together and creates silent data-loss bugs
  when one rotates without the other.

Before adding any new env var, ask: "Would a different deployment of
this same application ever need a different value for this?" If the
honest answer is "no", it is not an env var.

#### Frontend: useEffect は外部システム同期のみ

> **設計原則 (2026-04-27 制定)**: `useEffect` は **外部システム同期** (DOM API / network / browser API / third-party library / EventSource / WebSocket / ResizeObserver 等) のみに使用する。**state → state、state → 永続化、state → URL の同期は禁止** — それらは action handler 内で同期的に行う。

詳細・判断フローチャート・12 アンチパターン・PR チェックリストはナレッジ `69eedf52aadadfddd2f0e27a` (React useEffect 使用判断ガイド) を参照。背景は React 公式 [You Might Not Need an Effect](https://react.dev/learn/you-might-not-need-an-effect)。

**禁止パターン** (PR レビューで block 対象):
- `useEffect(() => { setX(derive(y)) }, [y])` — 派生 state は render 中に計算 (`useMemo`)
- `useEffect(() => { setSearchParams({...}) }, [state])` — URL writeback は user action handler 内で同期実行
- `useEffect(() => { localStorage.setItem(...) }, [state])` — 永続化は dispatcher の副作用ハンドラで
- `useEffect(() => { onChange(value) }, [value, onChange])` — 親への通知は event handler から直接
- `useRef` で `useEffect` の double-fire を抑止する pattern (`if (!hasInit.current)` 等) — effect は冪等であるべき
- `// eslint-disable-next-line react-hooks/exhaustive-deps` — 暗黙の前提が deps と食い違っている兆候

**判断ルール**: 「このコードはコンポーネントが画面に出たから走るのか? 外部システムと同期しているか?」 NO なら useEffect ではなく event handler / lazy initializer / `useMemo` / `useSyncExternalStore`。

新規 `useEffect` を追加する PR では、上記チェックリストの確認結果を PR description に記載すること。

### Git
- Do not add `Co-Authored-By` trailer to commits
- Include the task ID in commit messages for traceability (e.g., `feat: add versioning to documents [task:69c22641]`)

### Testing

#### 絶対禁止事項
**ローカルで `uv run pytest` を実行することは禁止。** テストは必ずコンテナ内で実行する。
理由: ローカル実行はホストの Python 環境・イベントループ・ファイルシステムに依存し、
再現性がなく、メモリリークや環境汚染のリスクがある（実際に 23GB 超のメモリ消費が発生した）。

#### テスト実行コマンド
上記「Backend テスト」セクションを参照。要約:
- **通常**: `docker compose -f backend/docker-compose.test.yml run --rm test`
- **統合 (実 DB)**: `docker compose -f backend/docker-compose.test.yml run --rm test-real`

#### テスト構成
- **Backend**: pytest-asyncio。`Dockerfile.test` でビルドし、`docker-compose.test.yml` の `test` サービスで実行する
  - **mock モード** (デフォルト): `mongomock-motor` + `fakeredis` — 外部 DB 不要
  - **real モード** (`TEST_MODE=real`): コンテナ内の実 MongoDB / Redis を使用
- **conftest.py**: Session-scoped DB init, function-scoped collection cleanup, pre-built fixtures (`admin_user`, `regular_user`, `admin_token`, `test_project`)
- **Frontend**: Vitest + Testing Library + MSW for API mocking

#### スキップされるテスト (自動)
以下のテストはデフォルトで除外される (Dockerfile.test の CMD に `--ignore` が指定済み):
- `tests/test_audit.py` — pip-audit はネットワークアクセスが必要
- `tests/integration/test_mcp_session_continuity.py` — testcontainers (Docker-in-Docker) が必要
- `tests/integration/test_agent_bus_realredis.py` — testcontainers (Docker-in-Docker) が必要


### E2E テスト (Playwright + コンテナ完結)

**仕様**: [docs/architecture/e2e-strategy.md](docs/architecture/e2e-strategy.md)

#### 目的

backend pytest (mock モード) / frontend vitest (MSW モック) では検出できない、
**スタックを貫通した動作** を機械的に保証する。
具体的には CLAUDE.md "Definition of Correctly Working" の以下の軸:

- axis 4 Shipped — 本番ビルド成果物が nginx 経由で配信される
- axis 5 Reachable — ボタン・route・フォームが UI に出ている
- axis 6 Operable — 触って期待した結果になる + console.error 0
- axis 7 Persistent — reload で状態が消えない
- axis 8 Recoverable — エラー UI と回復導線がある

#### 絶対禁止事項

- **mock を使ってはいけない**。E2E は実 MongoDB / 実 Redis / 本番ビルドの SPA を Traefik 経由で叩く。
- **DB 直叩きでシードしてはいけない**。シードは API 経由のみ (`fixtures/api.ts`)。
- **flaky を retry で隠してはいけない** (retries=0)。

#### 実行コマンド

```bash
# 全 E2E (推奨)
docker compose -f e2e/docker-compose.e2e.yml --env-file e2e/.env.e2e \
    run --rm --build e2e-runner

# 特定 spec のみ
docker compose -f e2e/docker-compose.e2e.yml --env-file e2e/.env.e2e \
    run --rm --build e2e-runner --grep login

# クリーンアップ
docker compose -f e2e/docker-compose.e2e.yml --env-file e2e/.env.e2e \
    down -v --remove-orphans
```

成果物 (失敗時の trace / video / screenshot / html-report / junit) は `e2e/results/` に書き出される。

E2E スタックは本番 `docker compose up -d` と完全分離 (network / volume / コンテナ名すべて `*-e2e`) のため同時実行可能。

#### テスト追加時の規約

詳細は [e2e/README.md](e2e/README.md) §「テストを追加するときの規約」を参照。要点:

1. test 名 prefix に軸ラベル (`[axis5][axis6] ...`)
2. timeout を毎回明示 (`toBeVisible({ timeout: 5_000 })`)
3. シードは API 経由 (`fixtures/api.ts`)
4. `attachConsoleErrorWatcher(page)` を仕込む — 画面が出ても console.error が出たら FAIL
