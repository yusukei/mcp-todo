# MCP Todo

Claude Code 向けの統合作業環境。Web UI でプロジェクト・タスク・ドキュメント・ナレッジ・ブックマーク・エラーを管理し、MCP サーバ経由で Claude Code から同じデータに直接アクセスできる。リモートエージェント（Supervisor + Agent）を介してリモート Windows / Linux / macOS マシンに対するシェル実行・ファイル操作・Web ターミナルも提供する。

## アーキテクチャ

```
                         ┌──────────────────────────────────┐
   ┌─────────┐  /mcp     │  Traefik (8080:80)               │
   │  Claude │──────────▶│   ├─ /mcp        → backend (sticky)
   │   Code  │           │   ├─ /api,/install → backend
   └─────────┘           │   └─ /            → frontend
                         └────────┬─────────────────────────┘
   ┌─────────┐  HTTPS             │
   │ Browser │───────────────────▶│
   └─────────┘                    ▼
                  ┌────────────────────────┐   ┌──────────────────────┐
                  │  backend (FastAPI ×4)  │   │  backend-indexer     │
                  │  - REST API            │   │  (sidecar, ×1)       │
                  │  - MCP server          │   │  - Tantivy writer    │
                  │  - WebSocket (PTY/SSE) │   │  - Bookmark clip Q   │
                  │  - Agent/Supervisor WS │   │  - Error tracker W   │
                  └─────────┬──────────────┘   └─────────┬────────────┘
                            │                            │
              ┌─────────────┼────────────────────────────┼──────────────┐
              ▼             ▼                            ▼              ▼
         ┌─────────┐   ┌─────────┐               ┌──────────────┐  ┌─────────┐
         │ MongoDB │   │  Redis  │               │   Tantivy    │  │ Backups │
         │  (data) │   │ (pubsub │               │ (search idx, │  │  cron   │
         │         │   │  + RPC) │               │  Lindera 日) │  │ (mongo) │
         └─────────┘   └─────────┘               └──────────────┘  └─────────┘
                            ▲
                            │ WebSocket
              ┌─────────────┴───────────────┐
              │                             │
        ┌──────────┐                  ┌──────────┐
        │Supervisor│  spawns / mgmt   │Supervisor│
        │ (Rust)   │ ───────────────▶ │ (Rust)   │
        └────┬─────┘                  └────┬─────┘
             ▼                              ▼
        ┌──────────┐                  ┌──────────┐
        │ Agent    │  PTY / fs / exec │ Agent    │
        │ (Rust)   │                  │ (Rust)   │
        └──────────┘                  └──────────┘
        Windows / Linux / macOS workstations
```

MCP サーバは backend に埋め込まれており、Beanie ORM で MongoDB に直接アクセスする（内部 HTTP は不要）。書き込みヘビーな処理（Tantivy 全文検索、ブックマークの Web クリップ、エラーイベントの取り込み）は `backend-indexer` サイドカーが単独で担当し、API ワーカは多重化して `WEB_CONCURRENCY=4` で動作する。詳細は [`docs/architecture/multi-worker-sidecar.md`](docs/architecture/multi-worker-sidecar.md)。

| サービス | 役割 / 技術 | コンテナ | ポート |
|---|---|---|---|
| `traefik` | リバースプロキシ + sticky cookie + 圧縮 / セキュリティヘッダ | `todo-traefik` | `8080:80` |
| `docker-socket-proxy` | Traefik に Docker API を安全公開 | `todo-docker-socket-proxy` | — |
| `backend` | API / MCP / WebSocket / chat (FastAPI + FastMCP, 4 workers) | `todo-backend` | 8000 |
| `backend-indexer` | Tantivy writer + bookmark clip queue + error tracker worker (1 worker) | `todo-backend-indexer` | 8000 |
| `frontend` | React 18 + TypeScript + Vite + Tailwind | `todo-frontend` | 3000 |
| `mongo` | MongoDB 7 — タスク / プロジェクト / ドキュメント本体 | `todo-mongo` | 27017 |
| `redis` | Redis 7 — pub/sub・MCP セッション・supervisor RPC・rate limit | `todo-redis` | 6379 |
| `backup` | mongodump cron + `bookmark_assets` / `docsite_assets` のスナップ | `todo-backup` | — |

## 主な機能

### タスク管理
- Kanban / リスト / タイムラインのビュー切り替え
- ドラッグ&ドロップによる並び替え・ステータス変更
- サブタスク、コメント、タグ、依存関係（`blocks` / `blocked_by`）
- 決定タスク（`task_type=decision`）— 背景・選択肢・推奨を構造化
- `needs_detail` / `approved` フラグによるレビューワークフロー
- 一括操作（作成・更新・完了・アーカイブ）と複製
- Markdown / PDF エクスポート（カバーページ付き）
- 担当者 / 期限切れ / 承認待ち別の作業コンテキスト取得

### Workbench（プロジェクト IDE）
- `/projects/:projectId` の split-pane 構成（Tasks / Documents / Files / Errors / Terminal / Doc / TaskDetail）
- レイアウトはサーバ側に永続化（`workbench_layouts`）+ SSE で他デバイスに伝搬
- 子ルート（`settings` / `documents/:id`）はオーバーレイ表示で WorkbenchPage が unmount せず、Web Terminal の WS / xterm が生存
- ペイン構成・分割幅は localStorage と server saver の二重永続化

### ドキュメント管理
- プロジェクトスコープ（`category`: `spec`, `design`, `api`, `guide`, `notes`）
- バージョン管理（更新ごとに自動スナップ + 任意の `change_summary`）
- Markdown + Mermaid ダイアグラム
- 全文検索（Tantivy + Lindera 日本語形態素解析）
- 表示順 / 折りたたみ状態の永続化、Markdown / PDF エクスポート

### ナレッジベース
- プロジェクト横断の技術ナレッジ（`recipe` / `reference` / `tip` / `troubleshooting` / `architecture`）
- Tantivy 全文検索 + プロジェクト横断のメタタグ

### ブックマーク
- URL 登録時に Playwright で本文・サムネイル・OG 情報を非同期クリップ（`backend-indexer` のクリップキューが処理）
- コレクション、タグ、`clip_status` 別フィルタ、再クリップ
- Markdown 化された本文を Tantivy で全文検索

### DocSites
- 外部ドキュメントサイト（Astro Starlight 生成物等）をプロジェクトに紐付け
- ページ単位で全文検索 / 要約 / 編集（クロール後の差分マージ）
- アセットはホスト側 `docsite_assets/` にバインドマウント

### エラートラッカー
- Sentry 互換のイベント取り込み（DSN は `rotate_error_dsn` で発行）
- Issue 集約・解決・無視・再オープン、自動でタスク作成（`configure_error_auto_task`）
- 関連タスクへのリンク、24h / 7d 統計、`backend-indexer` 側でフィンガープリント計算

### リモート操作（Supervisor + Agent）
- ワンライナー install（`irm <BASE_URL>/install/in_xxx | iex`）で Windows に Supervisor を配備
- Supervisor は WebSocket で backend に常時接続、Agent プロセスを spawn / 監視 / 自動更新
- Web UI の Workspace から Agent / Supervisor のリネーム・削除・トークン rotation・手動アップデート
- MCP ツール `remote_exec` / `remote_read_file` / `remote_grep` / `remote_glob` / `remote_tree` / `remote_write_file` 等で Claude Code から直接操作（CSWSH 防御 + 監査ログ）
- Web Terminal — xterm.js + WebSocket で PTY を提供、predictive echo（Mosh 風）でキー押下のレイテンシ体感を低減

### URL コントラクト / 共有
- `/projects/{pid}?task=` `?doc=` 等の deep-link を全リソースに統一
- フロント `buildUrl` / `parseUrl` lib + バックエンド MCP ツール `parse_url` / `get_resource` / `lookup_url`
- 各カードに Copy URL ボタン（WCAG 1.4.13 / 2.1.1 / 4.1.2 対応）

### シークレット
- プロジェクトスコープの暗号化シークレットストア
- `remote_exec(inject_secrets=True)` で会話に値を露出せず env に注入
- 全アクセスを監査ログに記録

### 認証
- メール / パスワード or **パスキー（WebAuthn）** → JWT（access 60 分 + refresh 7 日）
- パスキー登録後はパスワード認証を無効化可能
- Google OAuth（`allowed_emails` ホワイトリスト制御）
- MCP / API は `X-API-Key` ヘッダ → ユーザの有効性も毎回検証
- Refresh token は Redis で one-time use、ログアウト時に JTI 失効

### 管理機能
- ユーザ管理 / Google OAuth 許可リスト編集
- ユーザごとの API キー管理（アカウント設定から発行・rotation）
- バックアップ / リストア（`mongodump` + `bookmark_assets` / `docsite_assets` の `.agz` アーカイブ）
- プロジェクトロック（変更禁止）
- 管理者ダッシュボード（メンバー一覧 / ユーザ詳細）

## セットアップ

### 1. 環境変数

```bash
cp .env.example .env
```

最低限変更が必要なもの:

| 変数 | 説明 |
|---|---|
| `SECRET_KEY` | JWT 署名鍵（`openssl rand -hex 32` で生成） |
| `REFRESH_SECRET_KEY` | Refresh token 鍵（同じく `openssl rand -hex 32`） |
| `MONGO_PASSWORD` | MongoDB 認証パスワード |
| `REDIS_PASSWORD` | Redis 認証パスワード |
| `BASE_URL` | バックエンドの公開 origin（MCP OAuth ベース、リリース URL、install URL）|
| `FRONTEND_URL` | SPA の公開 origin（CORS / Google OAuth redirect / WebAuthn RP / Agent WS の Origin allowlist）|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google OAuth（任意） |
| `WEBAUTHN_RP_NAME` | パスキー UI に表示される RP 名（任意、既定 `MCP Todo`） |
| `INIT_ADMIN_EMAIL` / `INIT_ADMIN_PASSWORD` | 初期管理者作成用（任意） |

WebAuthn の `RP ID` と `expected_origin` は `FRONTEND_URL` から自動導出。

オプション（compose 起動時のみ参照）:

| 変数 | 既定 | 説明 |
|---|---|---|
| `DOCKER_DATA_ROOT` | `D:/docker-data/claude-todo` | mongo / redis / search index / bookmark_assets / docsite_assets / backups のホスト側バインドマウント先 |
| `WEB_CONCURRENCY` | `4` | backend (API) ワーカ数。`backend-indexer` は常に `1` |
| `BACKUP_RETENTION_DAYS` / `BACKUP_CRON` | `7` / `0 3 * * *` | backup コンテナ |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_SECONDS` | `20` / `300` | ログイン失敗のレート制限 |

### 2. 起動

```bash
docker compose up -d
```

初回はホスト側 `${DOCKER_DATA_ROOT}` 配下に `mongo_data/` `redis_data/` `search_index/` `bookmark_assets/` `docsite_assets/` `agent_releases/` `supervisor_releases/` `backups/` が自動生成される。

### 3. 初期管理者作成

```bash
# インタラクティブ（メール・パスワードを対話入力）
docker compose exec backend uv run python -m app.cli init-admin

# 引数指定
docker compose exec backend uv run python -m app.cli init-admin \
  --email admin@example.com --password 'yourpass8+'

# .env の INIT_ADMIN_EMAIL / INIT_ADMIN_PASSWORD を使用
docker compose exec backend uv run python -m app.cli init-admin
```

### 4. アクセス

| URL | 用途 |
|---|---|
| `http://localhost:8080` | Web UI（Traefik 経由） |
| `http://localhost:8080/api/v1/docs` | API ドキュメント（Swagger） |
| `http://localhost:8080/mcp` | MCP HTTP エンドポイント |
| `http://localhost:8080/install/{token}` | Supervisor ワンライナー install スクリプト |

本番では Traefik の前段に TLS 終端（cloudflared / nginx / Caddy 等）を置く想定。

## Claude Code 設定

プロジェクトの `.mcp.json` または `~/.claude.json`:

```json
{
  "mcpServers": {
    "mcp-todo": {
      "type": "http",
      "url": "https://todo.example.com/mcp",
      "headers": {
        "X-API-Key": "mtodo_xxxx"
      }
    }
  }
}
```

API キーは Web UI のアカウント設定（`/settings`）で発行。初回セットアップ時は MCP ツール `get_setup_guide` で CLAUDE.md スニペットを取得できる。

## MCP ツール

13 モジュール構成。詳細仕様は [`docs/mcp-tools/README.md`](docs/mcp-tools/README.md) と各モジュールファイルを参照。

| モジュール | ファイル | 主なツール |
|---|---|---|
| プロジェクト | [`projects.md`](docs/mcp-tools/projects.md) | `list_projects` / `get_project` / `create_project` / `update_project` / `delete_project` / `get_project_summary` |
| タスク | [`tasks.md`](docs/mcp-tools/tasks.md) | `list_tasks` / `get_task_context` / `get_work_context` / `create_task` / `update_task` / `complete_task` / `add_comment` / `link_tasks` / `search_tasks` / `batch_*` / `bulk_*` |
| ドキュメント | [`documents.md`](docs/mcp-tools/documents.md) | `create_document` / `update_document` / `list_documents` / `search_documents` / `get_document_history` / `get_document_version` |
| ナレッジ | [`knowledge.md`](docs/mcp-tools/knowledge.md) | `create_knowledge` / `search_knowledge` / `list_knowledge` / `update_knowledge` |
| ブックマーク | [`bookmarks.md`](docs/mcp-tools/bookmarks.md) | `create_bookmark` / `clip_bookmark` / `search_bookmarks` / `batch_bookmark_action` / `*_bookmark_collection` |
| DocSites | [`docsites.md`](docs/mcp-tools/docsites.md) | `list_docsites` / `get_docpage` / `search_docpages` / `create_docpage` / `update_docpage` / `upload_docsite_asset` |
| エラートラッカー | [`error-tracker.md`](docs/mcp-tools/error-tracker.md) | `list_error_issues` / `resolve_error_issue` / `link_error_to_task` / `create_task_from_error` / `get_error_stats` / `rotate_error_dsn` / `configure_error_auto_task` |
| シークレット | [`secrets.md`](docs/mcp-tools/secrets.md) | `list_secrets` / `set_secret` / `get_secret` / `delete_secret` |
| リモート操作 | [`remote.md`](docs/mcp-tools/remote.md) | `remote_exec` / `remote_exec_batch` / `remote_read_file` / `remote_write_file` / `remote_edit_file` / `remote_grep` / `remote_glob` / `remote_tree` / `remote_list_dir` / `remote_stat` / `remote_*` |
| Supervisor | — | `supervisor_status` / `supervisor_restart` / `supervisor_logs` / `supervisor_upgrade` / `supervisor_config_reload` |
| URL ルックアップ | [`url-lookup.md`](docs/mcp-tools/url-lookup.md) | `parse_url` / `get_resource` / `lookup_url` |
| API フィードバック | [`feedback.md`](docs/mcp-tools/feedback.md) | `request_api_improvement` / `list_api_feedback` |
| セットアップ | [`setup.md`](docs/mcp-tools/setup.md) | `get_setup_guide` |

すべてのツールは `X-API-Key` 認証必須・120 req/min/IP のレート制限。タイトル 255 / 説明 10,000 / コメント 10,000 文字制限。詳細は共通仕様の章を参照。

## バックアップ / リストア

`backup` コンテナが `BACKUP_CRON`（既定: 毎日 03:00）で自動取得。手動でも実行可能:

### CLI

```bash
cd backend
uv run python -m app.cli backup                    # backup_YYYY-MM-DD_HH-MM-SS.agz に出力
uv run python -m app.cli backup -o my_backup.agz   # 出力パス指定
uv run python -m app.cli restore backup.agz --confirm  # リストア（全データ置換）
```

### API（管理者のみ）

| エンドポイント | 説明 |
|---|---|
| `POST /api/v1/backup/export` | `.agz` バックアップファイルをダウンロード |
| `POST /api/v1/backup/import` | `.agz` ファイルをアップロードしてリストア |

Web UI の管理画面（`/admin`）からも操作可能。

## リモートエージェント運用

### Supervisor 配備（Windows 例）

1. Web UI → Workspace → "Install token を発行"
2. 表示された 1 行を対象 Windows の PowerShell で実行:
   ```powershell
   irm https://todo.example.com/install/in_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx | iex
   ```
3. Supervisor がインストールされ、自動的に backend に WebSocket 接続。Agent も併せて配信される。
4. 以後、Supervisor 側で `auto_update=true` の場合は新リリースを backend が push（`update_channel`: `stable` / `beta` / `canary`）。手動 push は Workspace UI の「アップデート確認」ボタン。

### Web Terminal

Workbench の Terminal pane / `/workspaces/terminal/{agentId}` から PTY を起動。Predictive echo によりキー押下と同時にローカル予測描画 → サーバ echo で確定する Mosh 風挙動。詳細は `frontend/src/components/workspace/PredictiveEngine.ts` と仕様書 `docs/...`。

## 開発

### Backend

```bash
cd backend
uv sync
uv run pytest                    # テスト実行（モックモード）
uv run pytest --cov              # カバレッジ付き（最低 70%）
TEST_MODE=real uv run pytest     # 実 DB 接続テスト
```

### Frontend

```bash
cd frontend
npm install
npm run dev                      # 開発サーバ (Vite)
npm test                         # vitest（jsdom）
npm run build                    # プロダクションビルド
```

### Frontend の `/dev/preview`

`VITE_DEV_PREVIEW=1` ビルドの場合のみ `http://localhost:8080/dev/preview` にデザインシステムプレビューが出る（ステージング検証用）。本番ビルドでは route 自体が消える。

### CI

GitHub Actions で `main` / `master` への push・PR 時に自動実行:
- Backend テスト + カバレッジ（Python 3.12）
- Frontend 型チェック + テスト + ビルド（Node 20）
- セキュリティ監査（pip-audit, npm audit）

## ドキュメント

- アーキテクチャ: [`docs/architecture/`](docs/architecture/)
  - `multi-worker-sidecar.md` — backend / backend-indexer の役割分担
  - `mcp-stateless-transport.md` — MCP HTTP transport（sticky cookie）
  - `e2e-strategy.md` — E2E テスト戦略
- ランブック: [`docs/runbook/multi-worker.md`](docs/runbook/multi-worker.md)
- API: [`docs/api/`](docs/api/)（`url-contract.md` / `endpoints.md`）
- データモデル: [`docs/data-models.md`](docs/data-models.md)
- MCP ツール: [`docs/mcp-tools/`](docs/mcp-tools/)
- パフォーマンス測定: [`docs/perf/`](docs/perf/)
- フロントエンド: [`docs/frontend/`](docs/frontend/)
- 直近のレビュー: [`docs/review/`](docs/review/)

## ライセンス

Private
