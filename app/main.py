"""ASGI entry point: `uvicorn app.main:app`."""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from . import api, mcp, oauth
from .config import get_config
from .db import connect, migrate

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
log = logging.getLogger("house_run_sheet")

# style-src allows inline style attributes (progress bar widths etc.); scripts stay locked to 'self'.
# img-src allows blob: so the page can preview a resized photo before it's uploaded.
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

# Widened beyond /api/: the OAuth endpoints, the metadata documents and the MCP endpoint must never
# be cached by a proxy either.
NO_STORE_PREFIXES = ("/api/", "/oauth/", "/mcp", "/.well-known/")

# The consent form's Allow button 302s to claude.ai, and form-action is checked against the
# submission target, so /oauth/ gets its own tighter policy. Its script is /oauth.js ('self').
OAUTH_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self'; connect-src 'self'; "
             "form-action 'self' https://claude.ai http://localhost:* http://127.0.0.1:*; "
             "frame-ancestors 'none'; base-uri 'none'")


class SecurityHeaders:
    """Pure ASGI middleware. Every response gets nosniff, a same-origin referrer policy, DENY framing
    and a Content-Security-Policy — OAUTH_CSP under /oauth/, where the consent form posts on to
    claude.ai, and CSP everywhere else. Anything under NO_STORE_PREFIXES also gets no-store, so the
    API, the OAuth endpoints, the metadata documents and /mcp are never held by a proxy."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        no_store = path.startswith(NO_STORE_PREFIXES)
        csp = OAUTH_CSP if path.startswith("/oauth/") else CSP

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", csp.encode()),
                ]
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def log_through_uvicorn() -> None:
    """Send our records to the stderr handler uvicorn already installed.

    uvicorn configures only its own loggers, so "house_run_sheet" reaches no handler at all: every
    log.info() is dropped — the schema version, a registered client, an issued code, a saved
    session — and WARNING+ escapes raw through logging.lastResort. Borrowing uvicorn's handler puts
    the lot on one stream in uvicorn's own format. Only this logger, not the root: root at INFO
    would also unmute httpx, which logs the URL of every Twilio call.

    Under the test client uvicorn has configured nothing, so this does nothing and the suite
    stays quiet.
    """
    if log.handlers:
        return
    # 0.46 hangs the handler on "uvicorn"; "uvicorn.error" inherits it by propagation.
    handlers = logging.getLogger("uvicorn").handlers or logging.getLogger("uvicorn.error").handlers
    if not handlers:
        return
    for handler in handlers:
        log.addHandler(handler)
    log.setLevel(logging.INFO)  # NOTSET would inherit root's WARNING and drop every info line


@contextlib.asynccontextmanager
async def lifespan(app):
    log_through_uvicorn()
    cfg = get_config()
    Path(cfg.photos_dir).mkdir(parents=True, exist_ok=True)
    if not os.environ.get("HRS_PUBLIC_URL"):
        # Local development keeps working; a misconfigured droplet is loud in the logs instead of
        # silently advertising localhost to Claude.
        log.warning("HRS_PUBLIC_URL is not set; the Claude connector will advertise %s", cfg.public_url)
    conn = connect()
    try:
        version = migrate(conn)
        log.info("database ready at schema version %s", version)
    finally:
        conn.close()
    yield


def create_app() -> Starlette:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)  # the frontend build writes here; run without it in dev
    app = Starlette(routes=[*api.routes, *oauth.routes, *mcp.routes,
                            Mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")],
                    lifespan=lifespan)
    return SecurityHeaders(app)


app = create_app()
