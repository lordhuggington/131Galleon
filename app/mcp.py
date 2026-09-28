"""The MCP endpoint Claude talks to.

JSON-RPC 2.0 over POST: one request in, one JSON response out. Streamable HTTP in its stateless JSON
form — no SSE, no session ids, no server-initiated notifications. Owner-only, bearer-authenticated.
"""
from __future__ import annotations

import json
import logging

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import menu, oauth, store
from .db import connect, tx

log = logging.getLogger("house_run_sheet")

MAX_BODY_BYTES = 256 * 1024
# Newest first; initialize echoes a version we support and otherwise answers LATEST.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST = SUPPORTED_PROTOCOL_VERSIONS[0]
UNSUPPORTED_ERA = ("This server speaks MCP " + ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
                   + ". Begin with an initialize request.")
SERVER_INFO = {"name": "Galleon", "title": "Galleon", "version": "1.0.0"}
INSTRUCTIONS = ("Galleon is a household app for one home in Los Angeles. To plan a Tuesday or Friday "
                "batch-prep session, call get_brief first, work the recipes and macros out with the user "
                "in the chat, then post the session with save_session.")

GET_BRIEF_DESCRIPTION = (
    "Call this first, before planning anything. Returns the household's brief for one batch-prep "
    "session: the date and week, how many portions of breakfast, main and dessert to make, the kcal and "
    "protein target every single portion must hit, the store, likes, dislikes and pantry, the titles "
    "already saved for this session and for the week's other session (with its leftovers to use up), "
    "recent titles not to repeat, favourites, and the rules to follow. Pass `date` to plan a particular "
    "Tuesday or Friday; leave it out for the next one. If `existing` comes back non-null, tell the user "
    "that saving will replace what is already there.")

SAVE_SESSION_DESCRIPTION = (
    "Post one finished batch-prep session (a Tuesday or a Friday) to Galleon. Replaces that session's "
    "recipes, timeline, leftovers and shopping list; the week's other session and its shopping ticks are "
    "left alone. The app re-adds your per-ingredient kcal and protein and refuses the whole session if "
    "any recipe is off target or any field is too long — nothing is saved in that case. Validation "
    "errors list every problem, one per line: fix them all and call save_session again. Call "
    "get_brief first.")

# No outputSchema on either tool on purpose: structuredContent is still returned, and declaring a
# schema would only add a surface a client could fail us on.
TOOLS = [
    {"name": "get_brief", "title": "The household's prep brief",
     "description": GET_BRIEF_DESCRIPTION, "inputSchema": menu.GET_BRIEF_SCHEMA},
    {"name": "save_session", "title": "Save a prep session",
     "description": SAVE_SESSION_DESCRIPTION, "inputSchema": menu.SAVE_SESSION_SCHEMA},
]
TOOL_NAMES = tuple(t["name"] for t in TOOLS)


def jsonrpc_result(rid, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def jsonrpc_error(rid, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}},
                        status_code=status)


# ---------- the two tools ----------
def tool_error(text: str) -> dict:
    """A tool failure: HTTP 200, a JSON-RPC result, isError true. Claude reads the text and retries."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def tool_ok(text: str, structured: dict) -> dict:
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured}


def _get_brief(conn, args: dict, user) -> dict:
    given = args.get("date")
    if given is not None and not isinstance(given, str):
        raise menu.MenuError('date must be a string like "2026-09-29".')
    data = menu.brief(conn, given)
    # One text block holding the JSON, plus the same object as structuredContent: clients that
    # ignore structuredContent read the text.
    return tool_ok(json.dumps(data, indent=2), data)


def _save_session(conn, args: dict, user) -> dict:
    normalized, shopping = menu.normalize_session(args)
    settings = store.get_settings(conn)
    problems = menu.validate_session(normalized, settings)
    if problems:
        return tool_error(menu.problem_report(problems))  # nothing is written when anything is wrong
    week, session = normalized["week"], normalized["session"]
    data = menu.session_data(normalized, settings)
    with tx(conn):
        store.save_session(conn, week, session, data, shopping, user["id"])
    log.info("saved the %s session of week %s from the Claude connector", session, week)
    text, structured = menu.save_result(week, session, data, shopping)
    return tool_ok(text, structured)


HANDLERS = {"get_brief": _get_brief, "save_session": _save_session}
# A tool listed with no handler would reach Claude as "Something went wrong saving that session"; fail
# at import instead.
assert set(HANDLERS) == set(TOOL_NAMES), "every listed tool needs a handler"


def call_tool(name: str, args: dict, user) -> dict:
    """Run one tool on its own connection, in a worker thread, exactly like api.endpoint. Never raises."""
    conn = connect()
    try:
        return HANDLERS[name](conn, args, user)
    except menu.MenuError as e:
        return tool_error(str(e))
    except Exception:  # noqa: BLE001 - a stack trace must never reach the chat
        log.exception("MCP tool %s failed", name)
        return tool_error("Something went wrong saving that session. Try again.")
    finally:
        conn.close()


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None when it is over the cap — declared or measured while streaming."""
    try:
        return await oauth.read_body(request, MAX_BODY_BYTES)
    except oauth.BodyTooLarge:
        return None


async def mcp_post(request: Request) -> Response:
    # Order matters: size, then bearer, then parse, then dispatch. Size first so an anonymous request
    # can't make a worker buffer megabytes; bearer before parse so a token-less client always gets the
    # sign-in handshake rather than a parse error.
    raw = await _read_body(request)
    if raw is None:
        return jsonrpc_error(None, -32600, "Request body too large.", status=413)
    user, refusal = await oauth.require_owner_bearer(request)
    if refusal is not None:
        return refusal
    asked_version = request.headers.get("mcp-protocol-version")
    try:
        msg = json.loads(raw)
    except ValueError:
        return jsonrpc_error(None, -32700, "Parse error.")
    if isinstance(msg, list):
        return jsonrpc_error(None, -32600, "This server does not support batched requests.")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return jsonrpc_error(None, -32600, "Invalid request.")
    rid = msg.get("id")
    # JSON-RPC 2.0 §4: an id is a string, a number or null — and True is an int to isinstance.
    if isinstance(rid, bool) or not isinstance(rid, (str, int, float, type(None))):
        return jsonrpc_error(None, -32600, "Invalid request.")
    method = msg.get("method")
    if asked_version:
        # One line per handshake is worth having; one per ping and per tool call is noise.
        log.log(logging.INFO if method == "initialize" else logging.DEBUG,
                "MCP request with MCP-Protocol-Version: %s", asked_version)
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    meta = params.get("_meta")
    # A handshake-less client from a newer MCP era. Name what we speak so it can fall back. The header
    # arm exempts initialize: an old-era client that guesses that header wrong still reaches the
    # handshake, whose answer names a version it knows. The _meta arm applies to every method.
    if ((isinstance(meta, dict) and "io.modelcontextprotocol/protocolVersion" in meta)
            or (asked_version and asked_version not in SUPPORTED_PROTOCOL_VERSIONS
                and method != "initialize")):
        return jsonrpc_error(rid, -32600, UNSUPPORTED_ERA, status=400)
    if rid is None:
        # A notification: nothing to answer, whatever the method. notifications/initialized lands here.
        return Response(status_code=202)
    if method == "initialize":
        asked = params.get("protocolVersion")
        return jsonrpc_result(rid, {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST,
            "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return jsonrpc_result(rid, {})
    if method == "tools/list":
        return jsonrpc_result(rid, {"tools": TOOLS})  # two tools, no pagination, no nextCursor
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return jsonrpc_error(rid, -32602, f"Unknown tool: {name}.")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return jsonrpc_error(rid, -32602, "arguments must be an object.")
        return jsonrpc_result(rid, await run_in_threadpool(call_tool, name, args, user))
    return jsonrpc_error(rid, -32601, f"Method not found: {method}.")


async def mcp_not_allowed(request: Request) -> Response:
    return JSONResponse({"error": "method_not_allowed"}, status_code=405, headers={"Allow": "POST"})


routes = [
    Route("/mcp", mcp_post, methods=["POST"]),
    Route("/mcp", mcp_not_allowed, methods=["GET", "DELETE"]),
]
