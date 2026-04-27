# URL Contract — frontend / backend / MCP の単一真理

> **目的**: mcp-todo を IDE として運用するため、URL を「個別リソースの共有チャネル」として仕様化する。
> frontend `buildUrl()` / backend `parse_url()` / MCP `lookup_url` が **同じ URL スキーマ** を使うことを保証し、将来の drift を防ぐ。
>
> **関連**: URL Sharing Epic `69eeea4071f37143d043d074` / 修正計画 `69eedfc871f37143d043d056` / Phase B 設計書 `69ed6f042835242574cad57c`

## 1. 設計原則

1. **URL = 共有可能な個別リソース選択**。layout / 個人設定は URL に含めない。
2. **個人 layout (タブ構成 / view / preset / group)** は Phase B の `workbench_layouts` (server + SSE) で **個人 cross-device sync** される。URL の役割ではない。
3. **frontend と backend は同じスキーマを使う**: 1 つの仕様書 (本書) を 2 言語で実装する。
4. **未知 URL は 200 + `kind: "unknown"`**: フロントは white-screen にせず default 動作、MCP は明示的な not-found 応答。
5. **存在 oracle を防ぐ**: アクセス不可と非存在を区別しない (バックエンドのみ)。

---

## 2. 対象リソースと URL パターン

| Kind | URL pattern | 説明 |
|---|---|---|
| `task` | `/projects/{pid}?task={tid}` | TaskDetail を表示 (slide-over または task-detail pane) |
| `document` | `/projects/{pid}?doc={did}` | DocPane に project document を表示 |
| `document_full` | `/projects/{pid}/documents/{did}` | フル editor route (`DocumentPage`) |
| `bookmark` | `/bookmarks/{bid}` | bookmark 詳細 (Common プロジェクト所属) |
| `knowledge` | `/knowledge/{kid}` | knowledge entry (cross-project) |
| `docsite_page` | `/docsites/{sid}/{path}` | docsite の特定ページ。`{path}` はサイト内相対パス、複数セグメント可 |
| `project` | `/projects/{pid}` | プロジェクト home (Workbench) |

### 2.1 `pid` / `tid` / `did` / `bid` / `kid` / `sid` の形式

- すべて MongoDB ObjectId 24 桁 hex (`[a-f0-9]{24}`)
- 現状は形式バリデーションのみ。存在チェックは backend の resource fetch 時に行う

### 2.2 `path` (docsite_page) の正規化

- 先頭末尾スラッシュなし: `intro/getting-started`
- URL encoding 必須 (日本語タイトル等)
- `..` `.` セグメントは reject (`kind: "unknown"`)

---

## 3. URL に **含まれない** query (個人 layout に帰属)

以下は **すべて URL から削除** され、Phase B `workbench_layouts` で個人 sync される：

| Query | 旧仕様 | 新仕様 |
|---|---|---|
| `?view=board\|list\|timeline` | TasksPane の viewMode | paneConfig.viewMode に保存、cross-device sync |
| `?group=<...>` | Timeline の groupBy | paneConfig.groupBy に保存 |
| `?layout=<preset>` | layout preset 切替 | Workbench メニューから選択、URL 不変 |

これらが URL に含まれていた場合は **無視 + console.warn** (フロント) / **`hadUnknownParams: true`** (backend)。

---

## 4. 共通スキーマ (TypeScript / Python 両言語実装)

### 4.1 ResourceKind

```ts
// TypeScript (frontend)
export type ResourceKind =
  | 'task'
  | 'document'
  | 'document_full'
  | 'bookmark'
  | 'knowledge'
  | 'docsite_page'
  | 'project'
  | 'unknown'  // parser only
```

```python
# Python (backend MCP)
ResourceKind = Literal[
    "task",
    "document",
    "document_full",
    "bookmark",
    "knowledge",
    "docsite_page",
    "project",
    "unknown",
]
```

### 4.2 buildUrl (frontend)

```ts
export interface BuildUrlOpts {
  projectId?: string
  resourceId?: string
  path?: string
  siteId?: string
  /** 絶対 URL (origin 付き) を返すか。クリップボードコピー用は true */
  absolute?: boolean
}

export function buildUrl(kind: ResourceKind, opts: BuildUrlOpts): string
```

#### 動作例

```ts
buildUrl('task', { projectId: 'abc', resourceId: 'def' })
// → '/projects/abc?task=def'

buildUrl('task', { projectId: 'abc', resourceId: 'def', absolute: true })
// → 'https://todo.vtech-studios.com/projects/abc?task=def'

buildUrl('document', { projectId: 'abc', resourceId: 'xyz' })
// → '/projects/abc?doc=xyz'

buildUrl('document_full', { projectId: 'abc', resourceId: 'xyz' })
// → '/projects/abc/documents/xyz'

buildUrl('bookmark', { resourceId: 'bm123' })
// → '/bookmarks/bm123'

buildUrl('knowledge', { resourceId: 'kn456' })
// → '/knowledge/kn456'

buildUrl('docsite_page', { siteId: 'site1', path: 'intro/getting-started' })
// → '/docsites/site1/intro/getting-started'

buildUrl('project', { projectId: 'abc' })
// → '/projects/abc'
```

#### 不正引数

- 必須 ID 不足 → throw `Error('buildUrl: missing required id for kind=...')`
- ObjectId 形式違反 → throw `Error('buildUrl: invalid id format')`

### 4.3 parseUrl (frontend / backend 共通スキーマ)

```ts
// TypeScript
export interface ParsedUrl {
  kind: ResourceKind
  projectId?: string
  resourceId?: string
  path?: string
  siteId?: string
  hadUnknownParams: boolean
  /** legacy redirect target if applicable */
  redirectTo?: string
}

export function parseUrl(url: string): ParsedUrl
```

```python
# Python
@dataclass
class ParsedUrl:
    kind: ResourceKind
    project_id: str | None = None
    resource_id: str | None = None
    path: str | None = None
    site_id: str | None = None
    had_unknown_params: bool = False
    redirect_to: str | None = None

def parse_url(url: str) -> ParsedUrl: ...
```

#### 解析優先順位

1. **絶対 URL の origin チェック**: `https://todo.vtech-studios.com` または `http://localhost:*` 以外は `kind: "unknown"`
2. **path-only 化**: origin を剥がして path + query で解析
3. **path matching**:
   - `/projects/{pid}/documents/{did}` → `document_full`
   - `/projects/{pid}` + `?task=` → `task`
   - `/projects/{pid}` + `?doc=` → `document`
   - `/projects/{pid}` (query なし) → `project`
   - `/bookmarks/{bid}` → `bookmark`
   - `/knowledge/{kid}` → `knowledge`
   - `/docsites/{sid}/{rest}` → `docsite_page`
4. **legacy redirect**:
   - `/workbench/{pid}` → `redirectTo: '/projects/{pid}'`、`kind: "project"`
5. それ以外 → `kind: "unknown"`

#### 未知 query 処理

- `?view=` `?group=` `?layout=` 含むその他の query は **すべて握り潰し** + `hadUnknownParams: true`
- フロント: `console.warn(...)` で開発者に通知
- backend: 応答に `hadUnknownParams: true` を含める

---

## 5. Round-trip 不変条件

frontend `buildUrl` → backend `parseUrl` → 同じ ids が再現されること：

```python
# pseudo-test
for kind, opts in test_cases:
    url = buildUrl(kind, opts)
    parsed = parse_url(url)
    assert parsed.kind == kind
    if 'projectId' in opts:
        assert parsed.project_id == opts['projectId']
    if 'resourceId' in opts:
        assert parsed.resource_id == opts['resourceId']
    # ...
```

CI で frontend テストと backend テストが**同じ fixture セット**を共有することで保証。

---

## 6. 互換性 / レガシー URL

| 旧 URL / query | 応答 |
|---|---|
| `/workbench/{id}` | `parsedUrl.redirectTo = '/projects/{id}'`、`kind: "project"` (D1-a で route 削除済み、redirect 自体は frontend で実施) |
| `?view=docs\|files\|errors` (旧 ProjectPage) | compatibility toast で該当 pane を追加 (v2.5 D4 と整合)。`hadUnknownParams: true` (将来削除) |
| `?view=board\|list\|timeline` | `hadUnknownParams: true` で握り潰し (個人 layout に帰属、Phase B sync で個人ごとに保存) |
| `?layout=<preset>` | 同上 |
| `?group=<...>` | 同上 |

旧クエリの compatibility 期間は **6 ヶ月** (v2.5 D4 と整合)、その後 frontend / backend ともに削除。

---

## 7. セキュリティ要件 (backend のみ)

frontend は permissionless でも URL 生成可。**backend MCP の `lookup_url` / `get_resource` で必ず以下を実施**：

### 7.1 IDOR 対策

```python
if parsed.project_id and not user_is_member(current_user, parsed.project_id):
    raise PermissionDenied(...)
```

### 7.2 存在 oracle 統一

「access denied」と「not found」を**同じ応答**に統一：

```python
if not resource_exists or not user_can_read(resource):
    return { "kind": "unknown", "url": url, "message": "Not found or access denied" }
```

### 7.3 Rate limit

- 1 ユーザあたり **100 reqs/min** (URL 列挙攻撃防止)
- 超過時 `429 Too Many Requests`

### 7.4 Audit log

- 失敗した lookup を `secret_access_logs` 同等のテーブルに記録
- スキーマ: `{user_id, url, kind, project_id, success, timestamp}`

---

## 8. 不変条件 (テストでカバー)

| ID | 不変条件 | テスト場所 |
|---|---|---|
| URL-1 | `parseUrl(buildUrl(kind, opts))` の round-trip が成立 | unit test (frontend / backend 同 fixture) |
| URL-2 | URL 内に `?view=` `?layout=` `?group=` が含まれない (生成側) | `buildUrl` snapshot test |
| URL-3 | レガシー URL が `redirectTo` で resolve | parser test |
| URL-4 | 未知 URL は `kind: "unknown"` を返す (white-screen にしない) | parser test |
| URL-5 | 別 user / 非 member の project を参照する URL は `kind: "unknown"` で応答 (oracle なし) | backend integration test |
| URL-6 | rate limit 超過で 429 | backend integration test |
| URL-7 | audit log に成功/失敗が記録 | backend integration test |

---

## 9. 実装の入り口

| レイヤ | ファイル | 責務 |
|---|---|---|
| frontend | `frontend/src/lib/urlContract.ts` | `buildUrl` / `parseUrl` を export |
| frontend | `frontend/src/components/common/CopyUrlButton.tsx` | UI affordance、内部で `buildUrl` を使う |
| frontend | `frontend/src/workbench/store/urlSync.ts` | user action → URL writeback、`buildUrl` を使う |
| backend | `backend/app/lib/url_contract.py` | `parse_url` 純関数 |
| backend MCP | `backend/app/mcp/tools/url_lookup.py` | `parse_url` + `get_resource` + `lookup_url` 3 ツール |
| backend test | `backend/tests/test_url_contract.py` | round-trip / IDOR / rate limit / audit log |
| frontend test | `frontend/src/__tests__/unit/urlContract.test.ts` | round-trip + buildUrl snapshot |

CI で frontend `urlContract.test.ts` と backend `test_url_contract.py` が**同じ JSON fixture** (`docs/api/url-contract.fixtures.json`) を読むことで仕様の同期を保証。

---

## 10. 変更履歴

- v1 (2026-04-27): 新設。URL Sharing Epic (`69eeea4071f37143d043d074`) に基づく初版

---

## 11. 関連仕様

- Phase B 設計書 `69ed6f042835242574cad57c` v2.1 — server-side layout sync
- Workbench / ProjectPage 統合設計 `69ecf44d2835242574cad431` v2.6 — URL contract 整合
- ナレッジ `69eedf52aadadfddd2f0e27a` — useEffect 判断ガイド (URL writeback の action 内同期実行)
- 修正計画 `69eedfc871f37143d043d056` — Action 駆動 + reducer 中央集権
- MCP tool: `docs/mcp-tools/url-lookup.md` (S4 で新設予定)
