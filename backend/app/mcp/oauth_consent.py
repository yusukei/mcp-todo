"""MCP OAuth 同意画面エンドポイント

/api/v1/mcp/oauth/consent で同意画面を表示し、ユーザーの許可を得て
TodoAuthorizationCode（user_id 付き）を発行する。

認証は JWT cookie (access_token) を使用する。
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import AnyUrl

from ..core.security import decode_access_token
from ..models import User
from .oauth_provider import (
    PENDING_AUTH_PREFIX,
    TodoAuthorizationCode,
    get_mcp_redis,
)

logger = logging.getLogger(__name__)

router = APIRouter()

CONSENT_TOKEN_PREFIX = "todo:mcp:consent_token:"  # noqa: S105
CONSENT_TOKEN_TTL = 600  # 10 分


def _get_provider():
    from .server import _oauth_provider

    return _oauth_provider


async def _get_current_user_from_jwt(request: Request) -> User | None:
    """JWT cookie または Authorization ヘッダーからユーザーを取得"""
    token = request.cookies.get("access_token")
    if not token:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        return None

    payload = decode_access_token(token)
    if not payload:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    user = await User.get(user_id)
    if not user or not user.is_active:
        return None
    return user


@router.get("/api/v1/mcp/oauth/consent", response_model=None)
async def consent_page(request: Request, pending: str):
    """同意画面を表示する。未ログインならログイン画面にリダイレクト。"""
    logger.info("MCP consent GET: received")

    r = get_mcp_redis()
    raw = await r.get(f"{PENDING_AUTH_PREFIX}{pending}")
    if not raw:
        logger.warning("MCP consent GET: pending expired or not found")
        return HTMLResponse(
            _render_error_html("認可リクエストが期限切れです。もう一度接続してください。"),
            status_code=400,
        )

    user = await _get_current_user_from_jwt(request)
    if user is None:
        logger.info("MCP consent GET: no session, redirecting to login")
        return_to = f"/api/v1/mcp/oauth/consent?pending={pending}"
        return RedirectResponse(
            f"/login?returnTo={quote(return_to, safe='')}",
            status_code=302,
        )

    logger.info("MCP consent GET: user=%s, showing consent page", user.id)

    # consent_token を生成（CSRF 保護）
    consent_token = secrets.token_urlsafe(32)
    await r.set(
        f"{CONSENT_TOKEN_PREFIX}{consent_token}",
        pending,
        ex=CONSENT_TOKEN_TTL,
    )

    params = json.loads(raw)
    display_name = user.name or user.email

    return HTMLResponse(
        _render_consent_html(
            client_id=params.get("client_id", ""),
            display_name=display_name,
            pending=pending,
            consent_token=consent_token,
        )
    )


@router.post("/api/v1/mcp/oauth/consent", response_model=None)
async def consent_submit(request: Request):
    """同意画面の結果を処理し、認可コードを発行する。"""
    logger.info("MCP consent POST: received")

    user = await _get_current_user_from_jwt(request)
    if user is None:
        return HTMLResponse(
            _render_error_html("セッションが期限切れです。再度ログインしてください。"),
            status_code=401,
        )

    form = await request.form()
    pending_id = str(form.get("pending", ""))
    consent_token = str(form.get("consent_token", ""))
    action = str(form.get("action", ""))

    logger.info("MCP consent POST: action=%s", action)

    r = get_mcp_redis()
    # consent_token を検証（ワンタイム）
    stored_pending = await r.getdel(f"{CONSENT_TOKEN_PREFIX}{consent_token}")
    if not stored_pending or stored_pending != pending_id:
        logger.warning("MCP consent POST: token validation FAILED")
        return HTMLResponse(
            _render_error_html("無効なリクエストです。"), status_code=400
        )

    # pending auth を消費（ワンタイム）
    raw = await r.getdel(f"{PENDING_AUTH_PREFIX}{pending_id}")
    if not raw:
        logger.warning("MCP consent POST: pending expired")
        return HTMLResponse(
            _render_error_html("認可リクエストが期限切れです。"),
            status_code=400,
        )

    params = json.loads(raw)
    redirect_uri = params["redirect_uri"]
    state = params.get("state")
    user_id = str(user.id)

    if action == "deny":
        logger.info("MCP consent POST: user denied, client=%s", params.get("client_id", ""))
        qs = urlencode({"error": "access_denied", "state": state or ""})
        return RedirectResponse(f"{redirect_uri}?{qs}", status_code=302)

    # 許可: TodoAuthorizationCode を発行
    code_value = f"todo_auth_{secrets.token_hex(16)}"
    auth_code = TodoAuthorizationCode(
        code=code_value,
        client_id=params["client_id"],
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=params.get(
            "redirect_uri_provided_explicitly", True
        ),
        scopes=params.get("scopes", []),
        expires_at=time.time() + 300,
        code_challenge=params["code_challenge"],
        resource=params.get("resource"),
        user_id=user_id,
    )

    provider = _get_provider()
    await provider.store_authorization_code(auth_code)

    logger.info(
        "MCP consent POST: auth code issued, client=%s, user=%s",
        params.get("client_id", ""),
        user_id,
    )

    qs = urlencode({"code": code_value, "state": state or ""})
    callback_url = f"{redirect_uri}?{qs}"
    logger.info("MCP consent POST: redirecting to callback: %s", callback_url[:100])
    # 302 リダイレクト（nginx CSP form-action に claude.ai を追加済み）
    return RedirectResponse(callback_url, status_code=302)


def _render_consent_html(
    client_id: str,
    display_name: str,
    pending: str,
    consent_token: str,
) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP Todo - アクセス許可</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; display: flex; justify-content: center;
       align-items: center; min-height: 100vh; margin: 0; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 2rem; max-width: 420px;
         width: 90%; box-shadow: 0 4px 24px rgba(0,0,0,0.3); }}
h2 {{ margin-top: 0; color: #38bdf8; font-size: 1.3rem; }}
p {{ line-height: 1.6; color: #94a3b8; }}
.user {{ color: #4ade80; font-weight: bold; }}
.client {{ color: #fb923c; font-weight: bold; }}
.buttons {{ display: flex; gap: 1rem; margin-top: 1.5rem; }}
button {{ flex: 1; padding: 0.75rem; border: none; border-radius: 8px; font-size: 1rem;
          cursor: pointer; font-weight: bold; }}
.allow {{ background: #38bdf8; color: #0f172a; }}
.allow:hover {{ background: #0ea5e9; }}
.deny {{ background: #334155; color: #e2e8f0; }}
.deny:hover {{ background: #475569; }}
</style>
</head>
<body>
<div class="card">
  <h2>MCP Todo へのアクセス許可</h2>
  <p>アプリケーション <span class="client">{html.escape(client_id)}</span> が
     <span class="user">{html.escape(display_name)}</span> として
     MCP Todo のデータにアクセスしようとしています。</p>
  <p>許可すると、このアプリケーションはあなたの権限でタスク・プロジェクトの操作を行えるようになります。</p>
  <form method="post" action="/api/v1/mcp/oauth/consent">
    <input type="hidden" name="pending" value="{html.escape(pending)}">
    <input type="hidden" name="consent_token" value="{html.escape(consent_token)}">
    <div class="buttons">
      <button type="submit" name="action" value="allow" class="allow">許可する</button>
      <button type="submit" name="action" value="deny" class="deny">拒否する</button>
    </div>
  </form>
</div>
</body>
</html>"""


def _render_redirect_html(url: str) -> str:
    """meta refresh でリダイレクト。CSP form-action が外部 302 をブロックするため。"""
    safe_url = html.escape(url, quote=True)
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="0;url={safe_url}">
<title>リダイレクト中...</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; display: flex; justify-content: center;
       align-items: center; min-height: 100vh; margin: 0; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 2rem; max-width: 420px;
         width: 90%; box-shadow: 0 4px 24px rgba(0,0,0,0.3); text-align: center; }}
</style>
</head>
<body>
<div class="card">
  <p>リダイレクト中... <a href="{safe_url}">自動で遷移しない場合はこちら</a></p>
</div>
</body>
</html>"""


def _render_error_html(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MCP Todo - エラー</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       background: #0f172a; color: #e2e8f0; display: flex; justify-content: center;
       align-items: center; min-height: 100vh; margin: 0; }}
.card {{ background: #1e293b; border-radius: 12px; padding: 2rem; max-width: 420px;
         width: 90%; box-shadow: 0 4px 24px rgba(0,0,0,0.3); text-align: center; }}
h2 {{ color: #f87171; }}
</style>
</head>
<body>
<div class="card">
  <h2>エラー</h2>
  <p>{html.escape(message)}</p>
</div>
</body>
</html>"""
