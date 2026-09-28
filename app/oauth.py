"""The app as its own OAuth 2.1 authorization server for the Claude connector.

Every route here lives outside /api on purpose: a top-level browser navigation and a plain form post
cannot carry the X-HRS header the API requires, so POST /oauth/authorize checks Sec-Fetch-Site instead.
Codes and tokens are secrets.token_urlsafe(32) — 256 bits — and are stored only as SHA-256 hex.
Never log a code or a token.
"""
from __future__ import annotations

import html
import json
import logging
import secrets
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .auth import COOKIE_NAME, token_hash, user_for_token
from .config import get_config
from .db import connect, now_iso, tx

log = logging.getLogger("house_run_sheet")

SCOPE = "menus"
CODE_TTL = 600             # authorization codes: 10 minutes, single use
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
REVOKED_KEEP = 7 * 86400   # keep revoked rows a week so refresh-token reuse is still detectable
MAX_CLIENTS = 200
MAX_FORM_BYTES = 64 * 1024
CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class BodyTooLarge(Exception):
    """A request body over the cap the caller set. The caller answers in its own shape."""


async def read_body(request: Request, max_bytes: int) -> bytes:
    """The raw body, refused before it is read when Content-Length says it is over max_bytes, and
    after when it lies."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise BodyTooLarge()
    raw = await request.body()
    if len(raw) > max_bytes:
        raise BodyTooLarge()
    return raw


async def form_body(request: Request) -> dict[str, str]:
    """Parse an application/x-www-form-urlencoded body with the standard library.

    Never call request.form(): Starlette 1.0 asserts python-multipart is installed before parsing any
    form body, urlencoded included, and it is not in requirements.txt.
    """
    raw = await read_body(request, MAX_FORM_BYTES)
    pairs = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in pairs.items() if v}


def oauth_error(code: str, description: str, status_code: int = 400) -> JSONResponse:
    """An RFC 6749 §5.2 / RFC 7591 §3.2.2 error body. 400 unless the caller says otherwise."""
    return JSONResponse({"error": code, "error_description": description}, status_code=status_code)


def _iso_ago(seconds: int) -> str:
    """An ISO stamp in the same format as now_iso(), `seconds` in the past. Comparable as text."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sweep(conn: sqlite3.Connection) -> None:
    """Drop expired codes and tokens. Revoked rows are kept a week so reuse detection still works."""
    now = int(time.time())
    conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM oauth_tokens WHERE revoked_at IS NOT NULL AND revoked_at < ?",
                 (_iso_ago(REVOKED_KEEP),))


def _client(conn: sqlite3.Connection, client_id: str) -> sqlite3.Row | None:
    if not client_id:
        return None
    return conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()


def _client_uris(client: sqlite3.Row) -> list[str]:
    try:
        uris = json.loads(client["redirect_uris"])
    except ValueError:
        log.warning("OAuth client %s has unreadable redirect_uris", client["client_id"])
        return []
    return [u for u in uris if isinstance(u, str)]


# ---------- metadata (public: no cookie, no bearer) ----------
async def protected_resource_metadata(request: Request) -> Response:
    """RFC 9728. `resource` must equal the URL Owen typed into Claude, path included."""
    base = get_config().public_url
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base],
                         "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
                         "resource_name": "Galleon"})


async def authorization_server_metadata(request: Request) -> Response:
    """RFC 8414. Claude requires S256 in code_challenge_methods_supported and "none" as an auth method."""
    base = get_config().public_url
    return JSONResponse({"issuer": base,
                         "authorization_endpoint": f"{base}/oauth/authorize",
                         "token_endpoint": f"{base}/oauth/token",
                         "registration_endpoint": f"{base}/oauth/register",
                         "response_types_supported": ["code"],
                         "grant_types_supported": ["authorization_code", "refresh_token"],
                         "code_challenge_methods_supported": ["S256"],
                         "token_endpoint_auth_methods_supported": ["none"],
                         "scopes_supported": [SCOPE]})


# ---------- dynamic client registration (RFC 7591) ----------
def _redirect_allowed(uri: str) -> bool:
    """Claude's hosted callback, or a loopback callback for Claude Code (any port, any path)."""
    if uri == CLAUDE_REDIRECT:
        return True
    try:
        p = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    return (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1")
            and not p.query and not p.fragment)


async def register(request: Request) -> Response:
    try:
        raw = await read_body(request, MAX_FORM_BYTES)
    except BodyTooLarge:
        return oauth_error("invalid_client_metadata", "That registration request is too large.")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return oauth_error("invalid_client_metadata", "The registration request must be a JSON object.")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and u for u in uris):
        return oauth_error("invalid_client_metadata", "redirect_uris must be a non-empty array of strings.")
    for uri in uris:
        if not _redirect_allowed(uri):
            return oauth_error("invalid_redirect_uri", f"{uri} is not a redirect URI this server accepts.")
    name = body.get("client_name")
    name = name.strip()[:120] if isinstance(name, str) else ""
    issued = int(time.time())
    # Everything else RFC 7591 allows (client_uri, logo_uri, scope, contacts, software_id…) is accepted
    # and ignored: this server has one resource, one scope and one kind of client.

    def run() -> Response:
        conn = connect()
        try:
            client_id = secrets.token_urlsafe(24)  # an identifier, not a credential: stored in the clear
            with tx(conn):
                if conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0] > MAX_CLIENTS:
                    # Re-adding the connector registers again, so old unused rows accumulate. Anything
                    # a month old with no tokens behind it is dead weight.
                    conn.execute("DELETE FROM oauth_clients WHERE created_at < ? "
                                 "AND client_id NOT IN (SELECT client_id FROM oauth_tokens)",
                                 (_iso_ago(30 * 86400),))
                conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) "
                             "VALUES (?, ?, ?, ?)", (client_id, name, json.dumps(uris), now_iso()))
            log.info("registered OAuth client %s (%s)", client_id, name or "unnamed")
            return JSONResponse({"client_id": client_id, "client_id_issued_at": issued, "client_name": name,
                                 "redirect_uris": uris, "token_endpoint_auth_method": "none",
                                 "grant_types": ["authorization_code", "refresh_token"],
                                 "response_types": ["code"]}, status_code=201)
        finally:
            conn.close()

    return await run_in_threadpool(run)


# ---------- the consent page ----------
# Its CSS is inline (style-src allows 'unsafe-inline'); its script cannot be, so it lives at
# /oauth.js — frontend/public/oauth.js, which Vite copies verbatim into static/.
STYLE = """<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: #f6f6f4; color: #1b1b1a; display: flex;
       justify-content: center; font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { width: 100%; max-width: 26rem; background: #fff; border: 1px solid #e2e1dd; border-radius: 14px; padding: 20px; }
h1 { font-size: 1.25rem; margin: 0 0 8px; }
p { margin: 8px 0; }
.muted { color: #5f5e59; font-size: 0.9rem; }
label { display: block; margin: 12px 0 4px; font-weight: 600; font-size: 0.9rem; }
input { width: 100%; padding: 10px; font-size: 1rem; border: 1px solid #c8c7c1; border-radius: 8px;
        background: #fff; color: inherit; }
button { margin-top: 12px; width: 100%; padding: 11px; font-size: 1rem; border-radius: 8px;
         border: 1px solid #c8c7c1; background: #fff; color: inherit; cursor: pointer; }
button.primary { background: #1b5e4b; border-color: #1b5e4b; color: #fff; font-weight: 600; }
.row { display: flex; gap: 8px; }
.row button { flex: 1; }
.alert { margin-top: 12px; padding: 10px; border-radius: 8px; background: #fdecea; color: #7f1d1d; font-size: 0.9rem; }
.alert:empty { display: none; }
details { margin-top: 16px; }
summary { cursor: pointer; font-size: 0.9rem; }
@media (prefers-color-scheme: dark) {
  body { background: #191918; color: #ececea; }
  main { background: #232322; border-color: #3a3a38; }
  input, button { background: #191918; border-color: #4a4a47; }
  button.primary { background: #2f7a62; border-color: #2f7a62; }
  .alert { background: #4a1d1b; color: #ffd9d6; }
  .muted { color: #a9a8a3; }
}
</style>"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Claude · Galleon</title>
{style}
</head>
<body>
<main>
<h1>{heading}</h1>
{body}
<div class="alert" role="alert"></div>
</main>
<script src="/oauth.js" defer></script>
</body>
</html>
"""

LOGIN_BODY = """<p class="muted">Sign in to connect Claude to Galleon.</p>
<form id="smsForm">
  <label for="phone">Mobile number</label>
  <input id="phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="(310) 555-1234">
  <button class="primary" id="sendCode" type="submit">Text me a code</button>
</form>
<form id="codeForm" hidden>
  <label for="code">6-digit code</label>
  <input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
  <button class="primary" id="signIn" type="submit">Sign in</button>
</form>
<details>
  <summary>Owner? Sign in with a password</summary>
  <form id="pwForm">
    <label for="username">Username</label>
    <input id="username" name="username" type="text" autocomplete="username">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
</details>"""

STAFF_BODY = """<p>Only an owner can connect Claude.</p>
<button id="signOut" type="button">Sign out and use a different account</button>"""

BAD_CLIENT = ("Galleon can't complete this connection: the app asking isn't registered here. "
              "Remove the connector in Claude and add it again.")

PARAM_KEYS = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method",
              "state", "scope", "resource")


def page(heading: str, body_html: str, status: int = 200) -> HTMLResponse:
    """One template, three states. body_html is already escaped by its builder.

    Text nodes are escaped with quote=False and attribute values with quote=True. Both are safe;
    quote=False keeps apostrophes readable ("didn't", not "didn&#x27;t") in prose the user reads.
    """
    return HTMLResponse(PAGE.format(style=STYLE, heading=html.escape(heading, quote=False), body=body_html),
                        status_code=status)


def error_page(message: str, status: int) -> HTMLResponse:
    return page("Can't connect Claude", f"<p>{html.escape(message, quote=False)}</p>", status=status)


def _hidden(params: dict[str, str]) -> str:
    return "".join(f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
                   for k, v in params.items())


def _consent_body(params: dict[str, str], client_name: str, display_name: str) -> str:
    who = client_name or "A Claude app"
    return (f'<p>{html.escape(who, quote=False)} is asking. '
            f'You are signed in as {html.escape(display_name, quote=False)}.</p>'
            f'<p class="muted">It will be able to read your menus, settings and shopping list, and to '
            f'replace one prep session at a time.</p>'
            f'<form method="post" action="/oauth/authorize">{_hidden(params)}'
            f'<div class="row">'
            f'<button class="primary" type="submit" name="decision" value="allow">Allow</button>'
            f'<button type="submit" name="decision" value="deny">Deny</button>'
            f'</div></form>')


def _with_params(uri: str, params: dict[str, str]) -> str:
    """Append params to a redirect URI. Empty values are dropped; a URI with a query gets '&'."""
    parts = [f"{k}={urllib.parse.quote(v, safe='')}" for k, v in params.items() if v]
    return uri + ("&" if "?" in uri else "?") + "&".join(parts)


def _redirect_error(redirect_uri: str, error: str, state: str) -> Response:
    return RedirectResponse(_with_params(redirect_uri, {"error": error, "state": state}), status_code=302)


async def authorize_get(request: Request) -> Response:
    q = {k: request.query_params.get(k, "") for k in PARAM_KEYS}

    def run() -> Response:
        conn = connect()
        try:
            # RFC 6749 §4.1.2.1: an unknown client or an unregistered redirect_uri must NEVER be
            # redirected to — that is how an open redirector is built. Everything else goes back to
            # the client as an error parameter.
            client = _client(conn, q["client_id"])
            if client is None or q["redirect_uri"] not in _client_uris(client):
                return error_page(BAD_CLIENT, 400)
            if q["response_type"] != "code":
                return _redirect_error(q["redirect_uri"], "unsupported_response_type", q["state"])
            if not q["code_challenge"] or q["code_challenge_method"] != "S256":
                return _redirect_error(q["redirect_uri"], "invalid_request", q["state"])
            scope = q["scope"] or SCOPE
            if set(scope.split()) - {SCOPE}:
                return _redirect_error(q["redirect_uri"], "invalid_scope", q["state"])
            user = user_for_token(conn, request.cookies.get(COOKIE_NAME))
            if user is None:
                return page("Sign in to Galleon", LOGIN_BODY)
            if user["role"] != "owner":
                return page("Can't connect Claude", STAFF_BODY)
            return page("Claude wants to read and write Galleon menus",
                        _consent_body({**q, "scope": scope}, client["client_name"], user["display_name"]))
        finally:
            conn.close()

    return await run_in_threadpool(run)


async def authorize_post(request: Request) -> Response:
    # The CSRF defence in place of X-HRS, which a plain form post cannot carry. An absent header is
    # allowed: old browsers send none.
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return error_page("That request didn't come from this page.", 403)
    try:
        form = await form_body(request)
    except BodyTooLarge:
        return error_page("That request was too large.", 400)

    def run() -> Response:
        conn = connect()
        try:
            user = user_for_token(conn, request.cookies.get(COOKIE_NAME))
            if user is None:
                return error_page("Please sign in as an owner first.", 403)
            if user["role"] != "owner":
                return error_page("Only an owner can connect Claude.", 403)
            client = _client(conn, form.get("client_id", ""))
            redirect_uri = form.get("redirect_uri", "")
            if client is None or redirect_uri not in _client_uris(client):
                return error_page(BAD_CLIENT, 400)
            state = form.get("state", "")
            if form.get("decision") != "allow":
                return _redirect_error(redirect_uri, "access_denied", state)
            code = secrets.token_urlsafe(32)
            with tx(conn):
                conn.execute(
                    """INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, code_challenge,
                                                scope, resource, expires_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (token_hash(code), client["client_id"], user["id"], redirect_uri,
                     form.get("code_challenge", ""), form.get("scope", "") or SCOPE,
                     form.get("resource", ""), int(time.time()) + CODE_TTL, now_iso()))
            log.info("issued an authorization code to client %s for user %s", client["client_id"], user["id"])
            return RedirectResponse(_with_params(redirect_uri, {"code": code, "state": state}), status_code=302)
        finally:
            conn.close()

    return await run_in_threadpool(run)


routes = [
    Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
    Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
    Route("/.well-known/oauth-authorization-server", authorization_server_metadata),
    Route("/oauth/register", register, methods=["POST"]),
    Route("/oauth/authorize", authorize_get),
    Route("/oauth/authorize", authorize_post, methods=["POST"]),
]
