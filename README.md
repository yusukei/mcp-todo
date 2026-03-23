# MCP Todo

Claude Code 向けタスク管理システム。Web UI でプロジェクト・タスクを管理し、MCP サーバ経由で Claude Code から直接タスク操作が可能。

## アーキテクチャ

```
┌─────────┐         ┌──────────────────────┐
│  Claude  │────────▶│                      │
│   Code   │  /mcp   │   Backend (FastAPI)  │
└─────────┘         │                      │
                    │  ┌────────────────┐  │
┌─────────┐         │  │ MCP Server     │  │──▶ MongoDB
│ Browser  │────────▶│  │ (FastMCP 埋込) │  │──▶ Redis
│          │  /api   │  └────────────────┘  │
└─────────┘         └──────────────────────┘
       │
       ▼
┌──────────────┐
│   Frontend   │
│  (React SPA) │
└──────────────┘
```

MCP サーバは Backend に統合されており、単一プロセスで動作します。DB に直接アクセスするため、内部 HTTP 通信は不要です。

| サービス | 技術 | ポート |
|----------|------|--------|
| backend | Python 3.12 / FastAPI / Beanie / FastMCP | 8000 |
| frontend | React 18 / TypeScript / Vite / Tailwind CSS | 3000 |
| nginx | リバースプロキシ / レート制限 | 80 |
| mongo | MongoDB 7 | 27017 |
| redis | Redis 7 (pub/sub, MCP セッション) | 6379 |

## セットアップ

### 1. 環境変数

```bash
cp .env.example .env
```

`.env` で以下を必ず変更:

| 変数 | 説明 |
|------|------|
| `SECRET_KEY` | JWT署名鍵 (`openssl rand -hex 32` で生成) |
| `REFRESH_SECRET_KEY` | リフレッシュトークン鍵 |
| `MONGO_PASSWORD` | MongoDB 認証パスワード |
| `REDIS_PASSWORD` | Redis 認証パスワード |
| `GOOGLE_CLIENT_ID` | Google OAuth クライアントID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth シークレット |

### 2. 起動

```bash
docker compose up -d
```

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

### アクセス

| URL | 用途 |
|-----|------|
| http://localhost | Web UI (nginx経由) |
| http://localhost/api/v1/docs | API ドキュメント (Swagger) |
| http://localhost/mcp | MCP エンドポイント |

## Claude Code 設定

プロジェクトの `.mcp.json` または `~/.claude.json` に追加:

```json
{
  "mcpServers": {
    "mcp-todo": {
      "type": "http",
      "url": "http://localhost/mcp",
      "headers": {
        "X-API-Key": "mtodo_xxxx"
      }
    }
  }
}
```

API キーは Web UI の管理画面で発行。

## MCP ツール一覧

### プロジェクト

| ツール | 説明 |
|--------|------|
| `list_projects` | アクセス可能なプロジェクト一覧 |
| `get_project` | プロジェクト詳細取得 |
| `get_project_summary` | ステータス別タスク数・完了率 |

### タスク

| ツール | 説明 |
|--------|------|
| `list_tasks` | タスク一覧（ステータス/優先度/担当者/タグ/ページネーション） |
| `get_task` | タスク詳細取得 |
| `create_task` | タスク作成 |
| `update_task` | タスク更新 |
| `delete_task` | タスク削除 |
| `complete_task` | タスク完了 |
| `add_comment` | コメント追加 |
| `search_tasks` | キーワード検索（タイトル・説明文） |
| `list_overdue_tasks` | 期限超過タスク一覧 |
| `list_users` | ユーザ一覧（担当者選択用） |
| `batch_create_tasks` | タスク一括作成 |
| `list_review_tasks` | レビューフラグ別タスク一覧 |
| `batch_update_tasks` | タスク一括更新 |

## 認証

| 対象 | 方式 |
|------|------|
| 管理者 | メール/パスワード → JWT (アクセス60分 + リフレッシュ7日) |
| 一般ユーザ | Google OAuth → `allowed_emails` に事前登録が必要 |
| MCP サーバ | `X-API-Key` ヘッダ → `mcp_api_keys` コレクションで直接検証 |

## 開発

### Backend

```bash
cd backend
uv sync
uv run pytest                    # テスト実行（モックモード）
uv run pytest --cov              # カバレッジ付き（最低70%）
TEST_MODE=real uv run pytest     # 実DB接続テスト
```

### Frontend

```bash
cd frontend
npm install
npm run dev                      # 開発サーバ (Vite)
npm test                         # テスト実行
npm run build                    # プロダクションビルド
```

### CI

GitHub Actions で `main` / `master` への push・PR 時に自動実行:
- Backend テスト + カバレッジ (Python 3.12)
- Frontend 型チェック + テスト + ビルド (Node 20)

## ライセンス

Private
