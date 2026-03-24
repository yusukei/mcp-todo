# MCP Todo

Claude Code 向けタスク管理システム。Web UI でプロジェクト・タスク・ドキュメント・ナレッジを管理し、MCP サーバ経由で Claude Code から直接操作が可能。

## アーキテクチャ

```
┌─────────┐         ┌──────────────────────┐
│  Claude  │────────▶│                      │
│   Code   │  /mcp   │   Backend (FastAPI)  │
└─────────┘         │                      │
                    │  ┌────────────────┐  │
┌─────────┐         │  │ MCP Server     │  │──▶ MongoDB
│ Browser  │────────▶│  │ (FastMCP 埋込) │  │──▶ Redis
│          │  /api   │  └────────────────┘  │──▶ Tantivy (全文検索)
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

## 主な機能

### タスク管理
- Kanban ボード / リストビューの切り替え
- ドラッグ&ドロップによる並び替え・ステータス変更
- サブタスク、コメント、タグ
- 決定タスク（decision type）: 背景・論点・選択肢を構造化
- needs_detail / approved フラグによるレビューワークフロー
- タスク一括操作（一括作成・更新・完了・アーカイブ）
- Markdown / PDF エクスポート（カバーページ付き）

### ドキュメント管理
- プロジェクトスコープのドキュメント（spec, design, api, guide, notes）
- バージョン管理（更新時に自動スナップショット）
- Markdown + Mermaid ダイアグラム対応
- 全文検索（日本語形態素解析対応）
- ドラッグ&ドロップによる表示順変更
- Markdown / PDF エクスポート

### ナレッジベース
- プロジェクト横断の技術知見共有（recipe, reference, tip, troubleshooting, architecture）
- 全文検索（Tantivy + Lindera による日本語対応）

### 検索
- Tantivy ベースの全文検索エンジン
- 日本語形態素解析（Lindera）対応
- タスク・ドキュメント・ナレッジを横断検索

### 管理機能
- ユーザ管理・Google OAuth メール許可リスト
- MCP API キー管理
- バックアップ / リストア（mongodump/mongorestore）
- プロジェクトロック（変更禁止）

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

初回セットアップ時は MCP ツール `get_setup_guide` で CLAUDE.md のテンプレートを取得可能。

## MCP ツール一覧

### プロジェクト

| ツール | 説明 |
|--------|------|
| `list_projects` | アクセス可能なプロジェクト一覧 |
| `get_project` | プロジェクト詳細取得 |
| `create_project` | プロジェクト作成 |
| `update_project` | プロジェクト更新（名前・説明・色・ステータス・ロック） |
| `delete_project` | プロジェクトアーカイブ（タスクも一括ソフト削除） |
| `get_project_summary` | ステータス別タスク数・完了率 |

### タスク

| ツール | 説明 |
|--------|------|
| `list_tasks` | タスク一覧（フィルタ・ソート・ページネーション） |
| `get_task` | タスク詳細取得 |
| `get_task_context` | タスク詳細 + サブタスク + 変更履歴を一括取得 |
| `get_work_context` | セッション開始時の作業コンテキスト取得 |
| `get_task_activity` | タスクの変更履歴 |
| `create_task` | タスク作成 |
| `update_task` | タスク更新 |
| `delete_task` | タスク削除（ソフト削除） |
| `complete_task` | タスク完了（完了レポート付き） |
| `reopen_task` | 完了/キャンセルしたタスクを再開 |
| `archive_task` | タスクをアーカイブ |
| `unarchive_task` | アーカイブ解除 |
| `duplicate_task` | タスク複製 |
| `add_comment` | コメント追加 |
| `delete_comment` | コメント削除 |
| `search_tasks` | 全文検索（タイトル・説明・タグ・コメント） |
| `list_overdue_tasks` | 期限超過タスク一覧 |
| `list_review_tasks` | レビューフラグ別タスク一覧 |
| `list_approved_tasks` | 承認済みタスク一覧 |
| `get_subtasks` | サブタスク一覧 |
| `list_tags` | プロジェクト内タグ一覧 |
| `list_users` | ユーザ一覧（担当者選択用） |
| `batch_create_tasks` | タスク一括作成 |
| `batch_update_tasks` | タスク一括更新 |
| `bulk_complete_tasks` | タスク一括完了 |
| `bulk_archive_tasks` | タスク一括アーカイブ |

### ドキュメント

| ツール | 説明 |
|--------|------|
| `create_document` | ドキュメント作成 |
| `get_document` | ドキュメント取得 |
| `update_document` | ドキュメント更新（自動バージョニング） |
| `delete_document` | ドキュメント削除（ソフト削除） |
| `list_documents` | ドキュメント一覧（カテゴリ・タグフィルタ） |
| `search_documents` | ドキュメント全文検索 |
| `get_document_history` | バージョン履歴取得 |
| `get_document_version` | 特定バージョンの内容取得 |

### ナレッジ

| ツール | 説明 |
|--------|------|
| `create_knowledge` | ナレッジエントリ作成 |
| `get_knowledge` | ナレッジ取得 |
| `update_knowledge` | ナレッジ更新 |
| `delete_knowledge` | ナレッジ削除（ソフト削除） |
| `list_knowledge` | ナレッジ一覧（カテゴリ・タグフィルタ） |
| `search_knowledge` | ナレッジ全文検索 |

### セットアップ

| ツール | 説明 |
|--------|------|
| `get_setup_guide` | CLAUDE.md テンプレート取得 |

## 認証

| 対象 | 方式 |
|------|------|
| 管理者 | メール/パスワード → JWT (アクセス60分 + リフレッシュ7日) |
| 一般ユーザ | Google OAuth → `allowed_emails` に事前登録が必要 |
| MCP サーバ | `X-API-Key` ヘッダ → `mcp_api_keys` コレクションで直接検証 |

## バックアップ / リストア

### CLI

```bash
cd backend
uv run python -m app.cli backup                    # backup_YYYY-MM-DD_HH-MM-SS.agz に出力
uv run python -m app.cli backup -o my_backup.agz   # 出力パス指定
uv run python -m app.cli restore backup.agz --confirm  # リストア（全データ置換）
```

### API（管理者のみ）

| エンドポイント | 説明 |
|----------------|------|
| `POST /api/v1/backup/export` | .agz バックアップファイルをダウンロード |
| `POST /api/v1/backup/import` | .agz ファイルをアップロードしてリストア |

Web UI の管理画面からも操作可能。

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
