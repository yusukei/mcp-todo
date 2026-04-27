"""URL Contract — pure parser tests.

仕様書: ``docs/api/url-contract.md``
共有 fixture: ``docs/api/url-contract.fixtures.json``

Round-trip 不変条件 (URL-1 / URL-2 / URL-3 / URL-4):
    - parse_url(build_url(kind, opts)) → 同じ {kind, ids}
    - build_url の出力に ?view= ?layout= ?group= が含まれない (snapshot)
    - legacy /workbench/{id} は redirect_to を返す
    - 未知 URL は kind: "unknown"

MCP 認可関連 (URL-5/URL-6/URL-7) は test_mcp_url_lookup.py に分離。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.lib.url_contract import (
    LAYOUT_QUERY_KEYS,
    ParsedUrl,
    build_url,
    parse_url,
)

# 仕様書 §9: ``docs/api/url-contract.fixtures.json`` を frontend / backend が
# 共有することで spec drift を防ぐ。host (リポジトリ root) と container
# (``/app/docs/...`` に bind-mount) の両環境で読めるよう、ancestor を
# 遡って ``docs/api/url-contract.fixtures.json`` を探す。
def _find_fixture_path() -> Path:
    current = Path(__file__).resolve()
    for ancestor in (current, *current.parents):
        candidate = ancestor / "docs" / "api" / "url-contract.fixtures.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "shared URL fixture not found in any ancestor of "
        f"{Path(__file__).resolve()}. Expected "
        "``docs/api/url-contract.fixtures.json`` under repo root or "
        "``/app/docs/...`` (container bind-mount)."
    )


def _load_fixtures() -> dict:
    with _find_fixture_path().open("r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def fixtures() -> dict:
    return _load_fixtures()


# ── URL-4: 未知 URL → kind: "unknown" ──────────────────────────────


def test_empty_string_is_unknown():
    assert parse_url("").kind == "unknown"


def test_whitespace_is_unknown():
    assert parse_url("   ").kind == "unknown"


def test_non_string_is_unknown():
    # 仕様書 §4 の頑健性。production では型は str だが
    # 防御的に。
    assert parse_url(None).kind == "unknown"  # type: ignore[arg-type]


def test_random_path_is_unknown(fixtures):
    for case in fixtures["invalid"]:
        result = parse_url(case["url"])
        expected = case["parsed"]
        assert result.kind == expected["kind"], (
            f"{case['name']}: expected kind={expected['kind']}, got {result.kind} for url={case['url']!r}"
        )
        if "hadUnknownParams" in expected:
            assert result.had_unknown_params == expected["hadUnknownParams"], (
                f"{case['name']}: had_unknown_params mismatch"
            )


# ── URL-1: round-trip 不変 (build_url → parse_url) ────────────────


def _to_camel_opts(build_opts: dict) -> dict:
    """fixtures は frontend 寄りの camelCase。build_url は snake_case。"""
    mapping = {
        "projectId": "project_id",
        "resourceId": "resource_id",
        "siteId": "site_id",
        "path": "path",
    }
    return {mapping[k]: v for k, v in build_opts.items() if k in mapping}


def test_round_trip_for_each_valid_fixture(fixtures):
    for case in fixtures["valid"]:
        kind = case["kind"]
        opts = _to_camel_opts(case["buildOpts"])
        built = build_url(kind, **opts)
        assert built == case["url"], (
            f"{case['name']}: build_url mismatch — expected {case['url']}, got {built}"
        )
        parsed = parse_url(built)
        assert parsed.kind == kind, f"{case['name']}: parsed kind mismatch"
        for k in ("project_id", "resource_id", "site_id", "path"):
            if k in opts:
                assert getattr(parsed, k) == opts[k], (
                    f"{case['name']}: {k} mismatch on round-trip"
                )
        assert parsed.had_unknown_params is False, case["name"]


# ── URL-2: 個人 layout 帰属 query は parse 時に had_unknown_params ──


def test_layout_query_keys_trigger_had_unknown_params(fixtures):
    for case in fixtures["with_unknown_params"]:
        parsed = parse_url(case["url"])
        expected = case["parsed"]
        assert parsed.kind == expected["kind"], case["name"]
        assert parsed.had_unknown_params is True, (
            f"{case['name']}: expected had_unknown_params=True, got False"
        )


def test_build_url_never_emits_layout_query_keys():
    """URL-2: build_url の出力には layout 系 query が含まれない。"""
    samples = [
        ("task", {"project_id": "a" * 24, "resource_id": "b" * 24}),
        ("document", {"project_id": "a" * 24, "resource_id": "b" * 24}),
        ("project", {"project_id": "a" * 24}),
        ("bookmark", {"resource_id": "c" * 24}),
        ("knowledge", {"resource_id": "d" * 24}),
        (
            "docsite_page",
            {"site_id": "e" * 24, "path": "intro/getting-started"},
        ),
    ]
    for kind, opts in samples:
        url = build_url(kind, **opts)  # type: ignore[arg-type]
        for k in LAYOUT_QUERY_KEYS:
            assert f"{k}=" not in url, (
                f"build_url('{kind}', ...) leaked layout query key: {k!r} → {url}"
            )


# ── URL-3: legacy /workbench/{id} → redirect_to /projects/{id} ────


def test_legacy_workbench_redirects(fixtures):
    for case in fixtures["legacy"]:
        parsed = parse_url(case["url"])
        expected = case["parsed"]
        assert parsed.kind == expected["kind"]
        assert parsed.project_id == expected["projectId"]
        assert parsed.redirect_to == expected["redirectTo"]


def test_legacy_workbench_with_invalid_id_is_unknown():
    assert parse_url("/workbench/short-id").kind == "unknown"


# ── absolute URL の origin allowlist ──────────────────────────────


def test_absolute_urls_match_path_only_results(fixtures):
    for case in fixtures["absolute_url"]:
        parsed = parse_url(case["url"])
        expected = case["parsed"]
        assert parsed.kind == expected["kind"], case["name"]
        if "projectId" in expected:
            assert parsed.project_id == expected["projectId"]
        if "resourceId" in expected:
            assert parsed.resource_id == expected["resourceId"]


def test_unknown_origin_is_unknown():
    assert (
        parse_url(
            "https://evil.example.com/projects/" + "a" * 24
        ).kind
        == "unknown"
    )


# ── trailing slash 正規化 ────────────────────────────────────────


def test_trailing_slash_is_normalised(fixtures):
    for case in fixtures["trailing_slash"]:
        parsed = parse_url(case["url"])
        expected = case["parsed"]
        assert parsed.kind == expected["kind"], case["name"]
        if "projectId" in expected:
            assert parsed.project_id == expected["projectId"]
        if "resourceId" in expected:
            assert parsed.resource_id == expected["resourceId"]


# ── docsite_page の path traversal 防御 ──────────────────────────


def test_docsite_path_with_dotdot_is_unknown():
    pid = "a" * 24
    assert parse_url(f"/docsites/{pid}/intro/../secret").kind == "unknown"


def test_docsite_path_with_dot_segment_is_unknown():
    pid = "a" * 24
    assert parse_url(f"/docsites/{pid}/./intro").kind == "unknown"


def test_docsite_multi_segment_path():
    sid = "1" * 24
    parsed = parse_url(f"/docsites/{sid}/a/b/c")
    assert parsed.kind == "docsite_page"
    assert parsed.site_id == sid
    assert parsed.path == "a/b/c"


# ── build_url の入力 validation ──────────────────────────────────


def test_build_url_rejects_missing_required_id():
    with pytest.raises(ValueError, match="missing required"):
        build_url("task", project_id="a" * 24)  # resource_id missing


def test_build_url_rejects_invalid_object_id():
    with pytest.raises(ValueError, match="invalid id format"):
        build_url("task", project_id="too-short", resource_id="b" * 24)


def test_build_url_rejects_unsupported_kind():
    with pytest.raises(ValueError, match="unsupported kind"):
        build_url("unknown", project_id="a" * 24)  # type: ignore[arg-type]


def test_build_url_rejects_docsite_path_with_traversal():
    with pytest.raises(ValueError, match="invalid docsite path"):
        build_url("docsite_page", site_id="1" * 24, path="intro/../etc")


def test_build_url_rejects_docsite_path_with_leading_slash():
    with pytest.raises(ValueError, match="invalid docsite path"):
        build_url("docsite_page", site_id="1" * 24, path="/intro")


# ── ParsedUrl.to_dict ────────────────────────────────────────────


def test_parsed_url_to_dict_preserves_all_fields():
    pid = "a" * 24
    rid = "b" * 24
    parsed = parse_url(f"/projects/{pid}?task={rid}")
    d = parsed.to_dict()
    assert d == {
        "kind": "task",
        "project_id": pid,
        "resource_id": rid,
        "path": None,
        "site_id": None,
        "had_unknown_params": False,
        "redirect_to": None,
    }


def test_parsed_url_dataclass_default_values():
    p = ParsedUrl(kind="unknown")
    assert p.project_id is None
    assert p.resource_id is None
    assert p.path is None
    assert p.site_id is None
    assert p.had_unknown_params is False
    assert p.redirect_to is None
