"""ASGI entry point: `uvicorn app.main:app`."""
from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles

from . import api, oauth
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
    """Pure ASGI middleware adding security headers and no-store on API responses."""

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


@contextlib.asynccontextmanager
async def lifespan(app):
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
    app = Starlette(routes=[*api.routes, *oauth.routes,
                            Mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")],
                    lifespan=lifespan)
    return SecurityHeaders(app)


app = create_app()
