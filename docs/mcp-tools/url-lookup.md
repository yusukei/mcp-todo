# URL Lookup ツール仕様書

`backend/app/mcp/tools/url_lookup.py` の URL 解決系 MCP ツール（全 3 関数）を記載します。

## 概要

mcp-todo の URL を受け取った Claude が、URL を解析してリソース本体を取得する経路を提供します。これにより:

- 共有された URL から **直接リソースを引き当てる** (中間で `get_task` / `get_document` 等を組み合わせる必要なし)
- frontend `buildUrl()` と backend `parse_url()` が **同じスキーマ** を使うことを CI で保証
- IDOR / 存在 oracle / rate limit / audit log のセキュリティ要件を一括で満たす

**仕様**: [`docs/api/url-contract.md`](../api/url-contract.md) に厳密準拠。

## ツール一覧

| ツール | 用途 |
|--------|------|
| `parse_url` | URL を解析し routing メタデータを返す（認可なし） |
| `get_resource` | kind / id からリソースを取得（認可あり） |
| `lookup_url` | URL を resolve してリソースを返す（薄ラッパー、rate limit + audit log 付き） |

**合計: 3 ツール関数**

---

## 1. parse_url

**概要**: URL を解析して routing メタデータ dict を返す（純関数表面、認可なし）。

**シグネチャ**:
```python
async def parse_url(url: str) -> dict
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `url` | str | ○ | — | full URL or path (e.g. `/projects/abc?task=def`) |

**戻り値** (dict):
```json
{
  "kind": "task",
  "project_id": "abc...",
  "resource_id": "def...",
  "had_unknown_params": false
}
```

`kind` は `task` / `document` / `document_full` / `bookmark` / `knowledge` / `docsite_page` / `project` / `unknown` のいずれか。

**特殊ケース**:
- legacy `/workbench/{pid}` → `redirect_to: "/projects/{pid}"` を含む
- `?view=` `?layout=` `?group=` などの個人 layout 帰属 query を含む URL → `had_unknown_params: true`
- 不正 URL / 未知 origin → `kind: "unknown"`

---

## 2. get_resource

**概要**: kind / id からリソースを取得（認可あり、IDOR / 存在 oracle 統一）。

**シグネチャ**:
```python
async def get_resource(
    kind: str,
    resource_id: str,
    project_id: str | None = None,
) -> dict
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `kind` | str | ○ | — | ResourceKind (`task` / `document` / `document_full` / `bookmark` / `knowledge` / `docsite_page` / `project`) |
| `resource_id` | str | ○ | — | 24 桁 hex (docsite_page 以外) |
| `project_id` | str \| None | — | None | task / document / document_full は **必須** |

**認可**:
- `project_id` がある系は `check_project_access` で membership 確認
- admin は bypass
- 失敗時は **存在 oracle 統一**: `{kind: "unknown", message: "Not found or access denied"}` を返す（**access denied** と **not found** が同じ応答）

**戻り値** (成功時):
```json
{
  "kind": "task",
  "project_id": "abc...",
  "resource_id": "def...",
  "resource": { "title": "...", "status": "todo", ... }
}
```

**戻り値** (失敗時):
```json
{ "kind": "unknown", "message": "Not found or access denied" }
```

---

## 3. lookup_url

**概要**: URL を resolve してリソースを返す。`parse_url` + `get_resource` の薄ラッパー。

**シグネチャ**:
```python
async def lookup_url(
    url: str,
    follow: bool = True,
) -> dict
```

**パラメータ**:

| 名前 | 型 | 必須 | デフォルト | 説明 |
|-----|---|----|---------|------|
| `url` | str | ○ | — | ターゲット URL (相対 or 絶対) |
| `follow` | bool | — | True | True なら resource 本体も inline、False なら routing metadata のみ |

**セキュリティ機能**:
1. **IDOR**: `parse_url` で特定された `project_id` に対し membership 必須
2. **存在 oracle 統一**: 不在 / 非 member / parse 不能はすべて `{kind: "unknown", message: "Not found or access denied"}`
3. **Rate limit**: 1 ユーザ / 100 reqs/min (Redis token-bucket、fail-closed)。超過時 `ToolError("Rate limit exceeded")` を raise
4. **Audit log**: 成功 / 失敗ともに `UrlLookupAuditLog` collection に記録 (`{user_id, url, kind, project_id, success, message, created_at}`)

**戻り値** (成功時、`follow=True`):
```json
{
  "url": "/projects/abc...?task=def...",
  "kind": "task",
  "project_id": "abc...",
  "resource_id": "def...",
  "had_unknown_params": false,
  "layout_query_keys": ["group", "layout", "view"],
  "resource": { "title": "...", "status": "todo", ... }
}
```

**戻り値** (`follow=False`):
```json
{
  "url": "/projects/abc...?task=def...",
  "kind": "task",
  "project_id": "abc...",
  "resource_id": "def...",
  "had_unknown_params": false,
  "layout_query_keys": ["group", "layout", "view"]
}
```

**戻り値** (失敗時):
```json
{
  "url": "/projects/abc...?task=def...",
  "kind": "unknown",
  "had_unknown_params": false,
  "message": "Not found or access denied",
  "layout_query_keys": ["group", "layout", "view"]
}
```

**Audit log の `message` タグ**:

| `message` | 意味 |
|-----------|------|
| `""` (空) | 成功時 |
| `not_found_or_denied` | リソースが存在しないか、アクセス権がない (URL-5 oracle) |
| `rate_limited` | rate limit 超過 (URL-6) |
| `parse_failed` | URL が parse 不能 (URL-4) |

---

## サポートされる URL パターン

[`docs/api/url-contract.md`](../api/url-contract.md) §2 と完全に一致:

| Kind | URL pattern | 例 |
|---|---|---|
| `task` | `/projects/{pid}?task={tid}` | `/projects/abc...?task=def...` |
| `document` | `/projects/{pid}?doc={did}` | `/projects/abc...?doc=xyz...` |
| `document_full` | `/projects/{pid}/documents/{did}` | `/projects/abc.../documents/xyz...` |
| `bookmark` | `/bookmarks/{bid}` | `/bookmarks/bm123...` |
| `knowledge` | `/knowledge/{kid}` | `/knowledge/kn456...` |
| `docsite_page` | `/docsites/{sid}/{path}` | `/docsites/site1.../intro/getting-started` |
| `project` | `/projects/{pid}` | `/projects/abc...` |

legacy `/workbench/{id}` は `redirect_to: '/projects/{id}'` で resolve されます。

---

## 使用例

### 例 1: タスクへの URL を resolve

```python
result = await lookup_url("/projects/abc.../?task=def...")
# →
# {
#   "url": "...",
#   "kind": "task",
#   "project_id": "abc...",
#   "resource_id": "def...",
#   "had_unknown_params": false,
#   "layout_query_keys": ["group", "layout", "view"],
#   "resource": { "id": "def...", "title": "Auth flow", "status": "todo", ... }
# }
```

### 例 2: 個人 layout query 付き URL（自動的に握り潰される）

```python
result = await lookup_url("/projects/abc.../?task=def...&view=board&layout=tasks-only")
# →
# kind: "task" は同じ。had_unknown_params: true。
# resource は普通に取得される (view / layout は parse 段階で握り潰し)。
```

### 例 3: routing metadata のみ取得（リソースは fetch しない）

```python
result = await lookup_url("/projects/abc.../?task=def...", follow=False)
# →
# {
#   "url": "...",
#   "kind": "task",
#   "project_id": "abc...",
#   "resource_id": "def...",
#   "had_unknown_params": false,
#   "layout_query_keys": [...]
#   # "resource" フィールドは含まれない
# }
```

### 例 4: legacy /workbench/{id} の redirect

```python
result = await lookup_url("/workbench/abc...")
# →
# {
#   "url": "/workbench/abc...",
#   "kind": "project",
#   "project_id": "abc...",
#   "redirect_to": "/projects/abc...",
#   ...
# }
```

---

## エラーハンドリング

| 状況 | 挙動 |
|---|---|
| URL が parse 不能 | `kind: "unknown"`、`message: "Not found or access denied"` |
| 認証失敗 | `McpAuthError("Authentication required")` (= `ToolError`) |
| Project 非 member | `kind: "unknown"` (oracle 統一、message 固定) |
| リソース不在 | `kind: "unknown"` (oracle 統一、message 固定) |
| Rate limit 超過 | `ToolError("Rate limit exceeded (100/min). Retry after Ns.")` |
| Redis 不通 | rate limiter が fail-closed (`ToolError`) |

---

## 関連仕様

- [`docs/api/url-contract.md`](../api/url-contract.md) — URL Contract 本体
- [`docs/api/url-contract.fixtures.json`](../api/url-contract.fixtures.json) — frontend / backend 共有 fixture
- 不変条件 URL-1〜URL-7 (詳細は url-contract.md §8 参照)
- frontend 側 `frontend/src/lib/urlContract.ts` (URL S5 で新設予定)

---

## 変更履歴

- v1 (2026-04-27): 新設 (URL S4 で 3 MCP tool を実装)
