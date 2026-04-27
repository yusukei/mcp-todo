"""URL Contract — 純関数 URL parser。

仕様書: ``docs/api/url-contract.md``

Frontend ``frontend/src/lib/urlContract.ts`` (URL S5 で新設予定) と
**完全に同じスキーマ** を実装する。CI で
``docs/api/url-contract.fixtures.json`` を両側から読むことで
round-trip 不変条件 (URL-1) を保証。

本モジュールは認可チェックを **一切しない**。MCP layer
(``app.mcp.tools.url_lookup``) が認証 / project membership / rate
limit / audit log を被せる。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse, parse_qsl

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

ALLOWED_KINDS_FOR_BUILD: tuple[ResourceKind, ...] = (
    "task",
    "document",
    "document_full",
    "bookmark",
    "knowledge",
    "docsite_page",
    "project",
)

# 個人 layout に帰属するため URL 上は無視 + had_unknown_params=True にするキー。
# 仕様書 §3 / §6 と整合.
LAYOUT_QUERY_KEYS: frozenset[str] = frozenset({"view", "layout", "group"})

# `?task=` / `?doc=` は parser が解釈する正規 query。それ以外 (LAYOUT_*
# 含む) は had_unknown_params をたてる。
RECOGNISED_QUERY_KEYS: frozenset[str] = frozenset({"task", "doc"})

# 仕様書 §2.1: ObjectId 24 桁 hex (小文字のみ).
_OBJECT_ID_RE = re.compile(r"^[a-f0-9]{24}$")

# 仕様書 §4.3.1: production / dev origin allowlist.
_ALLOWED_HOSTS: tuple[str, ...] = ("todo.vtech-studios.com",)


@dataclass
class ParsedUrl:
    """parse_url の戻り値。仕様書 §4.3 と 1:1。"""

    kind: ResourceKind
    project_id: str | None = None
    resource_id: str | None = None
    path: str | None = None
    site_id: str | None = None
    had_unknown_params: bool = False
    redirect_to: str | None = None

    def to_dict(self) -> dict:
        """MCP tool 戻り値用 (camelCase 寄りだが key 名は spec の Python 形)。"""
        return {
            "kind": self.kind,
            "project_id": self.project_id,
            "resource_id": self.resource_id,
            "path": self.path,
            "site_id": self.site_id,
            "had_unknown_params": self.had_unknown_params,
            "redirect_to": self.redirect_to,
        }


def _is_object_id(value: str) -> bool:
    return bool(_OBJECT_ID_RE.match(value))


def _origin_allowed(parsed) -> bool:
    """absolute URL の origin が allowlist 内か。

    - host が ``todo.vtech-studios.com`` (プロダクション)
    - host が ``localhost`` (any port — dev)
    - 以外は False (`kind: "unknown"` 経路へ)
    """
    host = (parsed.hostname or "").lower()
    if host in _ALLOWED_HOSTS:
        return True
    if host == "localhost":
        return True
    return False


def _normalise_path(path: str) -> list[str] | None:
    """trailing slash を落として segment list を返す。

    - `..` / `.` / 空セグメントが混じる場合 None (仕様書 §2.2 / §4.3 §3)。
    - URL decode は呼び出し側 (``parse_qsl`` / ``parse_url``) に任せる。
    """
    if path == "" or path == "/":
        return []
    # leading slash を剥がしてから split
    stripped = path.lstrip("/")
    # trailing slash 正規化 (空 segment にしない)
    if stripped.endswith("/"):
        stripped = stripped.rstrip("/")
    if stripped == "":
        return []
    segments = stripped.split("/")
    for s in segments:
        if s in ("", ".", ".."):
            return None
    return segments


def _split_query(query: str) -> tuple[dict[str, str], bool]:
    """query string を dict にしつつ未知 key の有無を判定。

    - parse_qsl(strict_parsing=False, keep_blank_values=True) で寛容に。
    - LAYOUT_QUERY_KEYS は had_unknown_params を立てる (仕様書 §3 / §6)。
    - RECOGNISED_QUERY_KEYS 以外もすべて had_unknown_params。
    - 同じ key が複数ある場合は最後を採用 (urlparse の挙動と整合)。
    """
    pairs = parse_qsl(query, keep_blank_values=True)
    out: dict[str, str] = {}
    had_unknown = False
    for k, v in pairs:
        if k in RECOGNISED_QUERY_KEYS:
            out[k] = v
        else:
            had_unknown = True
    return out, had_unknown


def parse_url(url: str) -> ParsedUrl:
    """URL を解析して routing メタデータを返す。

    認可チェックなし。仕様書 §4.3 §3 解析優先順位:

      1. 絶対 URL の origin allowlist (deny → unknown)
      2. path-only 化 (origin を剥がす)
      3. path matching (`/projects/.../documents/...` → document_full,
         `/projects/...` + ?task → task, ?doc → document, query なし → project,
         `/bookmarks/...`, `/knowledge/...`, `/docsites/...`)
      4. legacy redirect: `/workbench/{id}` → redirect_to: `/projects/{id}`
      5. それ以外 → unknown
    """
    if not isinstance(url, str) or url.strip() == "":
        return ParsedUrl(kind="unknown")

    parsed = urlparse(url)
    # absolute URL は origin allowlist チェック
    if parsed.scheme:
        if not _origin_allowed(parsed):
            return ParsedUrl(kind="unknown")
        path = parsed.path
        query = parsed.query
    else:
        # path-only として再 parse
        # `?task=foo` のような形でも urlparse(`/foo?bar`).query が取れる
        path = parsed.path
        query = parsed.query

    query_dict, had_unknown_params = _split_query(query)

    segments = _normalise_path(path)
    if segments is None:
        return ParsedUrl(kind="unknown", had_unknown_params=had_unknown_params)

    # legacy: /workbench/{pid} → kind=project + redirect_to=/projects/{pid}
    if len(segments) == 2 and segments[0] == "workbench":
        pid = segments[1]
        if _is_object_id(pid):
            return ParsedUrl(
                kind="project",
                project_id=pid,
                redirect_to=f"/projects/{pid}",
                had_unknown_params=had_unknown_params,
            )
        return ParsedUrl(kind="unknown", had_unknown_params=had_unknown_params)

    # /projects/{pid}/documents/{did} → document_full
    if (
        len(segments) == 4
        and segments[0] == "projects"
        and segments[2] == "documents"
    ):
        pid, did = segments[1], segments[3]
        if not _is_object_id(pid) or not _is_object_id(did):
            return ParsedUrl(
                kind="unknown", had_unknown_params=had_unknown_params
            )
        return ParsedUrl(
            kind="document_full",
            project_id=pid,
            resource_id=did,
            had_unknown_params=had_unknown_params,
        )

    # /projects/{pid} (+ optional ?task / ?doc)
    if len(segments) == 2 and segments[0] == "projects":
        pid = segments[1]
        if not _is_object_id(pid):
            return ParsedUrl(
                kind="unknown", had_unknown_params=had_unknown_params
            )
        # ?task= が優先 (仕様書 §4.3 §3)
        if "task" in query_dict:
            tid = query_dict["task"]
            if _is_object_id(tid):
                return ParsedUrl(
                    kind="task",
                    project_id=pid,
                    resource_id=tid,
                    had_unknown_params=had_unknown_params,
                )
            # task ID が形式違反 → kind=project に degrade、had_unknown=True
            return ParsedUrl(
                kind="project",
                project_id=pid,
                had_unknown_params=True,
            )
        if "doc" in query_dict:
            did = query_dict["doc"]
            if _is_object_id(did):
                return ParsedUrl(
                    kind="document",
                    project_id=pid,
                    resource_id=did,
                    had_unknown_params=had_unknown_params,
                )
            return ParsedUrl(
                kind="project",
                project_id=pid,
                had_unknown_params=True,
            )
        return ParsedUrl(
            kind="project",
            project_id=pid,
            had_unknown_params=had_unknown_params,
        )

    # /bookmarks/{bid}
    if len(segments) == 2 and segments[0] == "bookmarks":
        bid = segments[1]
        if not _is_object_id(bid):
            return ParsedUrl(
                kind="unknown", had_unknown_params=had_unknown_params
            )
        return ParsedUrl(
            kind="bookmark",
            resource_id=bid,
            had_unknown_params=had_unknown_params,
        )

    # /knowledge/{kid}
    if len(segments) == 2 and segments[0] == "knowledge":
        kid = segments[1]
        if not _is_object_id(kid):
            return ParsedUrl(
                kind="unknown", had_unknown_params=had_unknown_params
            )
        return ParsedUrl(
            kind="knowledge",
            resource_id=kid,
            had_unknown_params=had_unknown_params,
        )

    # /docsites/{sid}/{rest...} → docsite_page
    if len(segments) >= 3 and segments[0] == "docsites":
        sid = segments[1]
        if not _is_object_id(sid):
            return ParsedUrl(
                kind="unknown", had_unknown_params=had_unknown_params
            )
        # rest は normalise 済 (`..`/`.` は弾かれている)。spec §2.2 の
        # path 正規形は先頭末尾スラッシュ無し。
        sub_path = "/".join(segments[2:])
        return ParsedUrl(
            kind="docsite_page",
            site_id=sid,
            path=sub_path,
            had_unknown_params=had_unknown_params,
        )

    return ParsedUrl(kind="unknown", had_unknown_params=had_unknown_params)


# ── build_url (frontend と round-trip するための reference 実装) ─

_PATH_TRAVERSAL_RE = re.compile(r"(^|/)(\.|\.\.)(/|$)")


def build_url(
    kind: ResourceKind,
    *,
    project_id: str | None = None,
    resource_id: str | None = None,
    path: str | None = None,
    site_id: str | None = None,
) -> str:
    """ParsedUrl → URL 復元 (round-trip 用 reference 実装)。

    Frontend ``buildUrl`` (URL S5) と **完全に同じ shape** を出すこと。
    """
    if kind not in ALLOWED_KINDS_FOR_BUILD:
        raise ValueError(f"build_url: unsupported kind: {kind!r}")

    def _require(value: str | None, name: str) -> str:
        if not value:
            raise ValueError(
                f"build_url: missing required {name} for kind={kind!r}"
            )
        if name in {"project_id", "resource_id", "site_id"} and not _is_object_id(
            value
        ):
            raise ValueError(
                f"build_url: invalid id format for {name}: {value!r}"
            )
        return value

    if kind == "task":
        pid = _require(project_id, "project_id")
        rid = _require(resource_id, "resource_id")
        return f"/projects/{pid}?task={rid}"
    if kind == "document":
        pid = _require(project_id, "project_id")
        rid = _require(resource_id, "resource_id")
        return f"/projects/{pid}?doc={rid}"
    if kind == "document_full":
        pid = _require(project_id, "project_id")
        rid = _require(resource_id, "resource_id")
        return f"/projects/{pid}/documents/{rid}"
    if kind == "bookmark":
        rid = _require(resource_id, "resource_id")
        return f"/bookmarks/{rid}"
    if kind == "knowledge":
        rid = _require(resource_id, "resource_id")
        return f"/knowledge/{rid}"
    if kind == "docsite_page":
        sid = _require(site_id, "site_id")
        if not path:
            raise ValueError(
                "build_url: missing required path for kind='docsite_page'"
            )
        if _PATH_TRAVERSAL_RE.search(path) or path.startswith("/"):
            raise ValueError(
                f"build_url: invalid docsite path (traversal/leading slash): {path!r}"
            )
        return f"/docsites/{sid}/{path}"
    if kind == "project":
        pid = _require(project_id, "project_id")
        return f"/projects/{pid}"
    raise AssertionError(f"unreachable: kind={kind!r}")
