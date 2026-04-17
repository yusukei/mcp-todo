# リモート操作ツール仕様書

`backend/app/mcp/tools/remote.py` のリモートコマンド実行・ファイル操作ツール（全15関数）を記載します。

## 概要

GitHub Actions / SSH トンネル経由でリモートマシンのコマンド実行・ファイル操作を実行します。秘密注入、監査ログ、リトライ機能を備えています。

## パラメータ命名ポリシー

- **`project_id` は必須**。複数プロジェクト（= 複数リモートワークスペース）の区別のため、自動解決は行わない。LLM 呼び出し側は常にコンテキストに応じて明示指定すること。
- **パス引数は `path`** で統一（ローカル `Read`/`Edit`/`Write` の `file_path` とは命名が異なるが、エイリアスは追加しない — スキーマが二重化しトークンが逆に増えるため）。
- **`format` パラメータ**は全ツール共通で `"text"` (既定) / `"json"` の2値。text は LLM フレンドリーな軽量応答、json は詳細メタデータ。

## 応答フォーマット設計の全体像

| ツール | text形式 | json形式 |
|---|---|---|
| `remote_exec` | bash互換 (stdout + `[stderr]`/`[exit N]` マーカー) | `{exit_code, stdout, stderr, duration_ms, ...}` |
| `remote_read_file` | `cat -n` (`N\t<line>\n`) | `{content, size, path, encoding, is_binary, total_lines, truncated}` |
| `remote_grep` | ripgrep (`path:line:text`) | `{matches, count, truncated, ...}` |
| `remote_list_dir` | `ls -p` (1行1エントリ、dir に `/`) | `{entries, count, path}` |
| `remote_glob` | 1行1パス (mtime desc) | `{matches, count, base, truncated}` |
| `remote_write_file` | `wrote N bytes to <path>` | `{success, bytes_written, path}` |
| `remote_edit_file` | `edited <path> (N replacements)` | `{success, path, replacements}` |

## ツール一覧

| ツール | 用途 |
|--------|------|
| `list_remote_agents` | リモートエージェント一覧 |
| `remote_exec` | コマンド実行（シェル） |
| `remote_exec_batch` | **複数コマンドを1回でまとめて実行** |
| `remote_read_file` | ファイル読み込み |
| `remote_read_files` | **複数ファイルを1回でまとめて読み込み** |
| `remote_write_file` | ファイル作成・上書き |
| `remote_edit_file` | ファイル編集（find & replace） |
| `remote_list_dir` | ディレクトリ一覧 |
| `remote_stat` | ファイル情報取得 |
| `remote_file_exists` | ファイル存在確認 |
| `remote_mkdir` | ディレクトリ作成 |
| `remote_delete_file` | ファイル削除 |
| `remote_move_file` | ファイル移動・リネーム |
| `remote_copy_file` | ファイル複製 |
| `remote_glob` | パターンマッチでファイル検索 |
| `remote_grep` | grep 検索 |

**合計: 16 ツール関数**

### バッチツール概要

- **`remote_read_files(paths: list[str])`** — 最大 20 ファイル、合計 1 MB まで。応答は `=== <path> ===` ヘッダ付き text（または json）。1 ファイルの読み取り失敗は inline で `[error: ...]` 表示し他のファイルに影響しない。
- **`remote_exec_batch(commands: list[str], stop_on_error=False)`** — 最大 10 コマンド。応答は `$ <command>` ヘッダ付き text。`stop_on_error=True` で初回非ゼロ exit で中断。

---

## コマンド実行

### remote_exec

**概要**: リモートコマンド実行

**シグネチャ**:
```python
async def remote_exec(
    project_id: str,
    command: str,
    timeout: int = 60,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    inject_secrets: bool = False,
    run_in_background: bool | None = None,
    format: str = "text",
) -> dict | str
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `project_id` | str | ○ | — | プロジェクト ID または名前 |
| `command` | str | ○ | — | 実行するコマンド（bash） |
| `timeout` | int | — | 60 | タイムアウト（秒、1-3600） |
| `cwd` | str | — | None | ワーキングディレクトリ |
| `env` | dict | — | None | 環境変数追加（秘密値は避ける） |
| `inject_secrets` | bool | — | False | **True で環境変数にシークレット自動注入** |
| `run_in_background` | bool | — | None | True なら非同期実行し job_id を即返す |
| `format` | str | — | `"text"` | 応答形式。`"text"` は bash 互換のプレーンテキスト。`"json"` は詳細 dict |

**inject_secrets = True の動作**:
- プロジェクトの全シークレット (set_secret で登録) を環境変数として注入
- 秘密値をコマンドラインに expose しない
- 監査ログには環境変数 key のみ記録（値は非記録）

**戻り値 — `format="text"` (既定)**:

`str` — bash 互換のプレーンテキスト。トークン効率のためメタデータを省略し、必要なものだけ追記する。

```
<stdout がそのまま>
[stderr]           # stderr 非空時のみ
<stderr>
[exit N]           # exit_code != 0 時のみ
[stdout truncated at M bytes]  # 切り詰め時のみ
[stderr truncated at M bytes]  # 切り詰め時のみ
```

例 (exit=0, stdout="hello\n", stderr=""):
```
hello
```

例 (exit=2, stdout="", stderr="boom\n"):
```
[stderr]
boom
[exit 2]
```

**戻り値 — `format="json"`**:

`dict` — 詳細メタデータを含む従来形式。

```json
{
  "exit_code": 0,
  "stdout": "command output...",
  "stderr": "",
  "stdout_truncated": false,
  "stderr_truncated": false,
  "stdout_total_bytes": 19,
  "stderr_total_bytes": 0,
  "duration_ms": 1230
}
```

**戻り値 — `run_in_background=True`**:

`format` に関わらず `dict`:
```json
{"job_id": "<id>", "status": "running", "started_at": "..."}
```

**エラー**:
- `exit_code != 0` でも例外ではなく結果を返す（テキストでは `[exit N]`、JSONでは `exit_code`）
- タイムアウト → `ToolError`
- リモートエージェント接続失敗 → `ToolError`
- `format` が `"text"`/`"json"` 以外 → `ToolError`

**WHEN TO USE**:
- npm install, make build, docker compose up
- デプロイ・テスト実行
- データベース migration

**セキュリティ推奨**:
```python
# 推奨
await remote_exec(
    project_id="...",
    command="curl -H 'Authorization: Bearer $DB_TOKEN' ...",
    inject_secrets=True  # DB_TOKEN は環境変数から取得
)

# 非推奨（秘密値が stdout に出力される可能性）
await remote_exec(
    project_id="...",
    command="curl -H 'Authorization: Bearer my-secret-token' ..."
)
```

---

## ファイル操作

### remote_read_file

**概要**: ファイル読み込み（`if_not_hash` で差分応答対応）

**シグネチャ**:
```python
async def remote_read_file(
    project_id: str,
    path: str,
    offset: int | None = None,
    limit: int | None = None,
    encoding: str = "utf-8",
    format: str = "text",
    if_not_hash: str | None = None,
) -> dict | str
```

**差分応答 (`if_not_hash`)**:

同じファイルを繰り返し読むセッションで、トークン消費を劇的に削減するオプション。

- 呼び出し側は前回応答末尾の `[sha256:<hash>]` または `hash` フィールドをキャッシュ
- 次回 `if_not_hash=<前回のhash>` を渡す
- ハッシュが現在のファイル内容と一致 → `unchanged sha256:<hash>` の超短応答
- 不一致 → 新しい内容 + 新ハッシュが返り、キャッシュを更新できる

```python
# 1回目: フル取得（末尾に [sha256:abc...] が付く）
text = await remote_read_file(project_id="p", path="f.txt", if_not_hash="")

# 2回目: 変更なしなら超短い応答
text2 = await remote_read_file(
    project_id="p", path="f.txt", if_not_hash="abc...",
)
# → "unchanged sha256:abc...\n"
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `project_id` | str | ○ | — | プロジェクト ID または名前 |
| `path` | str | ○ | — | ファイルパス |
| `offset` | int | — | None | 開始行番号（1始まり、0も1扱い） |
| `limit` | int | — | None | 読み取る行数 |
| `encoding` | str | — | `"utf-8"` | エンコーディング。バイナリは `"binary"`/`"base64"` |
| `format` | str | — | `"text"` | 応答形式。`"text"` は `cat -n` 互換、`"json"` は詳細 dict |

**戻り値 — `format="text"` (既定)**:

`str` — ローカル `Read` と互換の `N<TAB><line>\n` 形式。

```
1	first line
2	second line
3	third line
```

truncation 発生時は末尾に `[truncated at N total lines]` を追加。

**戻り値 — `format="json"` / バイナリファイル**:

`dict` — 詳細メタデータを含む従来形式（バイナリは format に関わらず dict）。

```json
{
  "content": "file contents",
  "size": 1024,
  "path": "path/to/file",
  "encoding": "utf-8",
  "is_binary": false,
  "total_lines": 42,
  "truncated": false
}
```

---

### remote_write_file

**概要**: ファイル作成・上書き

**シグネチャ**:
```python
async def remote_write_file(
    project_id: str,
    path: str,
    content: str,
    format: str = "text",
) -> dict | str
```

**戻り値 — `format="text"` (既定)**:

`str` — 1行の完了メッセージ。

```
wrote 1024 bytes to /work/f.txt
```

**戻り値 — `format="json"`**:

`dict` — 従来形式。

```json
{"success": true, "bytes_written": 1024, "path": "/work/f.txt"}
```

---

### remote_edit_file

**概要**: ファイル編集（find & replace、差分のみ送信）

**シグネチャ**:
```python
async def remote_edit_file(
    project_id: str,
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    format: str = "text",
) -> dict | str
```

**動作**:
- `old_string` にマッチする最初の部分を `new_string` に置換
- `replace_all=True` で全置換
- 一意マッチでない場合は `ToolError`

**戻り値 — `format="text"` (既定)**:

`str` — 1行の完了メッセージ。

```
edited /work/x.txt
edited /work/x.txt (3 replacements)
```

**戻り値 — `format="json"`**:

`dict` — 従来形式。

```json
{"success": true, "path": "/work/x.txt", "replacements": 1}
```

エラー時: `ToolError` に `old_string not found` / `non-unique match` 等のメッセージ。

---

### remote_list_dir

**概要**: ディレクトリ一覧

**シグネチャ**:
```python
async def remote_list_dir(
    project_id: str,
    path: str = ".",
    format: str = "text",
) -> dict | str
```

**戻り値 — `format="text"` (既定)**:

`str` — 1行1エントリ、ディレクトリは末尾に `/` 付与（`ls -p` 形式）。

```
README.md
src/
tests/
pyproject.toml
```

**戻り値 — `format="json"`**:

`dict` — 従来形式（各エントリのサイズ・種別・mtime を含む）。

```json
{
  "entries": [
    {"name": "file.txt", "type": "file", "size": 1024, "mtime": "2026-04-15T10:00:00+00:00"},
    {"name": "subdir", "type": "directory"}
  ],
  "count": 2,
  "path": "path/to/dir"
}
```

---

### remote_stat

**概要**: ファイル情報取得

**シグネチャ**:
```python
async def remote_stat(
    project_id: str,
    path: str,
) -> dict
```

**戻り値** (dict):
```json
{
  "path": "...",
  "type": "file" | "directory",
  "size_bytes": 1024,
  "permissions": "644",
  "modified_at": "2025-04-15T10:00:00+00:00",
  "created_at": "..."
}
```

---

### remote_file_exists

**概要**: ファイル存在確認

**シグネチャ**:
```python
async def remote_file_exists(
    project_id: str,
    path: str,
) -> dict
```

**戻り値** (dict):
```json
{
  "exists": true | false,
  "path": "..."
}
```

---

### remote_mkdir

**概要**: ディレクトリ作成

**シグネチャ**:
```python
async def remote_mkdir(
    project_id: str,
    path: str,
    parents: bool = True,
) -> dict
```

**パラメータ**:
- `parents`: True なら親ディレクトリも自動作成（mkdir -p）

---

### remote_delete_file

**概要**: ファイル削除

**シグネチャ**:
```python
async def remote_delete_file(
    project_id: str,
    path: str,
) -> dict
```

---

### remote_move_file

**概要**: ファイル移動・リネーム

**シグネチャ**:
```python
async def remote_move_file(
    project_id: str,
    src: str,
    dest: str,
) -> dict
```

---

### remote_copy_file

**概要**: ファイル複製

**シグネチャ**:
```python
async def remote_copy_file(
    project_id: str,
    src: str,
    dest: str,
) -> dict
```

---

## 検索操作

### remote_glob

**概要**: glob パターンでファイル検索（mtime 降順ソート）

**シグネチャ**:
```python
async def remote_glob(
    project_id: str,
    pattern: str,
    path: str = ".",
    format: str = "text",
) -> dict | str
```

**パラメータ**:
- `pattern`: glob パターン（例: `"*.py"`, `"src/**/*.ts"`）
- `path`: 検索開始ディレクトリ（既定 `"."`）
- `format`: `"text"` (既定) or `"json"`

**戻り値 — `format="text"` (既定)**:

`str` — 1行1パス、mtime 降順。

```
src/recently_edited.py
src/older.py
[truncated]
```

**戻り値 — `format="json"`**:

`dict` — マッチごとの size/mtime を含む。

```json
{
  "matches": [
    {"path": "src/main.py", "size": 100, "mtime": "..."}
  ],
  "count": 1,
  "base": "src",
  "truncated": false
}
```

---

### remote_grep

**概要**: 正規表現でのファイル内検索（ripgrep/Python フォールバック）

**シグネチャ**:
```python
async def remote_grep(
    project_id: str,
    pattern: str,
    path: str = ".",
    glob: str | None = None,
    case_insensitive: bool = False,
    max_results: int = 200,
    respect_gitignore: bool = False,
    context_lines: int = 0,
    output_mode: str = "content",
    format: str = "text",
) -> dict | str
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `project_id` | str | ○ | — | プロジェクト ID または名前 |
| `pattern` | str | ○ | — | 正規表現パターン |
| `path` | str | — | `"."` | 検索対象ディレクトリ |
| `glob` | str | — | None | ファイル名 glob フィルタ (例: `"*.py"`) |
| `case_insensitive` | bool | — | False | 大文字小文字を無視（ripgrep の `-i`） |
| `max_results` | int | — | 200 | 最大マッチ数（1-2000） |
| `respect_gitignore` | bool | — | False | `.gitignore` を尊重（ripgrep 使用時） |
| `context_lines` | int | — | 0 | 前後の文脈行数（0-20、`-C`） |
| `output_mode` | str | — | `"content"` | text 時の出力形式。`content`/`files_with_matches`/`count` |
| `format` | str | — | `"text"` | 応答形式。`"text"` は ripgrep 互換、`"json"` は詳細 dict |

**戻り値 — `format="text"` (既定)**:

`str` — ripgrep 生出力互換。`output_mode` で形式を切替。

- `content` (既定): `path:line:text` 形式。`context_lines > 0` のとき文脈行は `path-line-text`（セパレータが `-`）。
- `files_with_matches`: ユニークなファイルパスを1行ずつ。
- `count`: `path:N` 形式。

```
src/a.py:42:def foo():
src/a.py-41-# comment before
src/a.py-43-    return bar
[truncated at 200 matches]
```

**戻り値 — `format="json"`**:

`dict` — 詳細メタデータを含む従来形式。

```json
{
  "matches": [
    {"file": "src/a.py", "line": 42, "text": "def foo():",
     "context_before": [...], "context_after": [...]}
  ],
  "count": 1,
  "truncated": false,
  "files_scanned": 10,
  "files_skipped_binary": 0,
  "files_skipped_large": 0,
  "engine": "ripgrep"
}
```

---

## エージェント管理

### list_remote_agents

**概要**: リモートエージェント一覧

**シグネチャ**:
```python
async def list_remote_agents() -> list[dict]
```

**戻り値** (list):
```json
[
  {
    "id": "...",
    "name": "prod-github-actions",
    "binding": "github_actions",
    "transport": "ssh",
    "status": "connected" | "disconnected",
    "last_heartbeat": "2025-04-15T10:00:00+00:00"
  }
]
```

---

## 監査ログ

すべてのリモート操作は監査ログに記録：

```
{
  "operation": "remote_exec",
  "project_id": "...",
  "command": "npm install",  # grep/find は mask される
  "executed_by": "mcp:my-key",
  "executed_at": "...",
  "exit_code": 0,
  "duration_seconds": 12.3,
  "denied": false
}
```

秘密値は ログに露出しない。

---

## 使用パターン

### ビルド・デプロイ

```python
# 1. リポジトリ clone / pull
await remote_exec(
    project_id="...",
    command="git clone ... || git pull"
)

# 2. 依存インストール
await remote_exec(
    project_id="...",
    command="npm install",
    cwd="frontend"
)

# 3. ビルド
await remote_exec(
    project_id="...",
    command="npm run build",
    cwd="frontend"
)

# 4. デプロイ（秘密注入）
await remote_exec(
    project_id="...",
    command="docker push $REGISTRY_TOKEN",
    inject_secrets=True
)
```

### ファイル検索→読み込み

```
1. remote_glob(project_id, "src/**/*.py") → ファイル一覧
2. remote_read_file(project_id, "src/main.py") → コンテンツ確認
3. remote_edit_file(...) で修正
```

---

## エラーハンドリング

```python
result = await remote_exec(project_id="...", command="...")

if result["exit_code"] == 0:
    # 成功
    print(result["stdout"])
else:
    # 失敗
    print(f"Error: {result['stderr']}")
```

---

**ツール総数**: 14 / 14

**最終更新**: 2025-04-15
