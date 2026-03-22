# レビュー結果 & 修正計画 — Claude Todo MCP Server

**レビュー実施日**: 2026-03-22
**レビューチーム**: Backend Architect / Frontend Architect / Security Engineer / Quality Engineer / DevOps Architect

---

## 総合評価

MVP としての完成度は高い。FastAPI + Beanie + Redis Pub/Sub + FastMCP の技術選定は適切で、認証フロー（管理者パスワード / Google OAuth / MCP APIキー）の3層設計も要件に合っている。

---

## Phase 1 — 本番デプロイ前（必須）✅ 完了

### Backend
- [x] 1.1 `python-jose` → `PyJWT` 移行（CVE-2024-33663/33664 対応）
- [x] 1.2 `REFRESH_SECRET_KEY` 分離 + jti 追加
- [x] 1.3 internal.py に Pydantic モデル適用
- [x] 1.4 内部APIキー認証を GET → POST に変更
- [x] 1.6 プロジェクト削除をソフトデリート化
- [x] 1.7 ユーザー削除をソフトデリート化
- [x] 1.8 コメント削除に作者チェック追加
- [x] 1.9 MCP シークレットの定数時間比較 (`hmac.compare_digest`)
- [x] 1.10 ログインレート制限（Redis, 5回/15分）
- [x] 1.11 MongoDB 接続のグレースフルシャットダウン
- [x] 1.12 ヘルスチェック強化（Mongo + Redis ping, 503 返却）
- [x] 1.13 CORS methods/headers 制限強化
- [x] 1.14 SSE プロジェクトフィルタリング
- [x] 1.15 Task 複合インデックス追加
- [x] 1.16 pyproject.toml 依存関係更新

### MCP
- [x] 1.4 APIキー認証を POST に変更 (auth.py)
- [x] 1.5 全ツールにプロジェクトスコープ検証追加
- [x] 1.17 APIクライアントにリトライロジック追加（3回, exponential backoff）
- [x] 1.18 N+1 修正 (`asyncio.gather`)
- [x] 1.19 `update_task` 入力バリデーション（status/priority enum 検証）
- [x] 1.20 起動時シークレット検証 + ヘルスチェック強化

### Frontend
- [x] 1.21 型定義 (`types/index.ts`)
- [x] 1.22 定数統合 (`constants/task.ts`)
- [x] 1.23 SSE 再接続ロジック（exponential backoff, max 20回）
- [x] 1.24 トークンリフレッシュ排他制御（mutex pattern）
- [x] 1.25 auth store `isInitialized` フラグ
- [x] 1.26 ProtectedRoute ローディング状態
- [x] 1.27 App.tsx コード分割 (`React.lazy`) + 初期化完了通知
- [x] 1.28 全コンポーネントの `any` 排除 + a11y（`role="dialog"`, Escape key 等）

### Infrastructure
- [x] 1.29 Dockerfile マルチステージビルド + 非root ユーザー
- [x] 1.30 .dockerignore 作成（backend/mcp/frontend）
- [x] 1.31 nginx セキュリティヘッダー + レート制限 + gzip
- [x] 1.32 docker-compose healthcheck + DB 認証（MongoDB/Redis パスワード）
- [x] 1.33 .env.example 追記（REFRESH_SECRET_KEY, DB認証情報）
- [x] 1.34 .gitignore 拡充

---

## Phase 2 — テスト拡充

### 2.1 MCPサーバーのテスト新規作成（最重要）

- [ ] `mcp/tests/test_auth.py` — authenticate(), check_project_access()
- [ ] `mcp/tests/test_api_client.py` — backend_request(), リトライロジック
- [ ] `mcp/tests/test_session_store.py` — RedisEventStore
- [ ] `mcp/tests/test_tools_tasks.py` — 全11ツール
- [ ] `mcp/tests/test_tools_projects.py` — 全3ツール

### 2.2 Backend欠落テスト

- [ ] `backend/tests/integration/test_users.py` — ユーザCRUD + AllowedEmail管理
- [ ] `backend/tests/integration/test_mcp_keys.py` — MCPキー発行・一覧・無効化
- [ ] `backend/tests/integration/test_internal.py` — 内部API全パス

### 2.3 Frontend欠落テスト

- [ ] `frontend/src/__tests__/components/AdminRoute.test.tsx`
- [ ] `frontend/src/__tests__/components/TaskList.test.tsx`
- [ ] `frontend/src/__tests__/components/TaskDetail.test.tsx`
- [ ] `frontend/src/__tests__/pages/ProjectsPage.test.tsx`
- [ ] `frontend/src/__tests__/pages/ProjectPage.test.tsx`
- [ ] `frontend/src/__tests__/api/client.test.ts` — リフレッシュ排他制御

### 2.4 CI/CDパイプライン

- [ ] `.github/workflows/ci.yml` 新規作成
```
├── lint + type-check（並列）
│   ├── Backend: ruff check + mypy
│   └── Frontend: tsc --noEmit
├── unit tests（並列）
│   ├── Backend: pytest tests/unit/ --cov
│   └── Frontend: vitest run --coverage
├── integration tests
│   └── Backend: docker-compose.test.yml + pytest tests/integration/
└── MCP tests（新規追加が必要）
```

---

## Phase 3 — 継続改善

| 項目 | 概要 |
|------|------|
| SSE専用短寿命トークン | `/auth/sse-ticket` エンドポイントで30秒TTLワンタイムチケット発行 |
| ページネーション | PaginationParams を全リストエンドポイントに適用 |
| リフレッシュトークン HttpOnly Cookie | XSS対策。リフレッシュトークンをCookieに移行 |
| レスポンスモデル定義 | Pydantic response_model で OpenAPI 仕様を完全化 |
| 構造化ログ | python-json-logger で JSON ログ出力 |
| 分散トレーシング | OpenTelemetry 導入 |
| Redis Streams 移行 | EventStore を List → Streams (XADD/XRANGE) に移行 |
| E2Eテスト | Playwright で SSE / OAuth / カンバンDnD を検証 |
| ErrorBoundary | ルート + ページレベルでの例外捕捉 |

---

## 参照

- [HANDOFF.md](./HANDOFF.md) — プロジェクト設計・構成の詳細
