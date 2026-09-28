"""The app as its own OAuth 2.1 authorization server for the Claude connector.

Every route here lives outside /api on purpose: a top-level browser navigation and a plain form post
cannot carry the X-HRS header the API requires, so POST /oauth/authorize checks Sec-Fetch-Site instead.
Codes and tokens are secrets.token_urlsafe(32) — 256 bits — and are stored only as SHA-256 hex.
Never log a code or a token.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth import token_hash
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


routes = [
    Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
    Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
    Route("/.well-known/oauth-authorization-server", authorization_server_metadata),
    Route("/oauth/register", register, methods=["POST"]),
]
