"""JSON API. Every handler runs in a worker thread with its own SQLite connection."""
from __future__ import annotations

import contextlib
import json
import re
import secrets
from datetime import date
from pathlib import Path
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import sms, store
from .auth import (COOKIE_NAME, ROLES, create_session, delete_session, delete_user_sessions, hash_password,
                   normalize_phone, password_problem, send_throttle, sms_check_throttle, throttle, user_for_token,
                   verify_password)
from .config import get_config
from .db import connect, now_iso, tx

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE_RE = re.compile(r"^\d{4,10}$")
USERNAME_RE = re.compile(r"^[a-z0-9._-]{2,64}$")
DOOR_CODE_RE = re.compile(r"^[0-9]{4,8}$")  # ASCII only: a keypad has no Unicode digits
FREQS = ("visit", "weekly", "fortnightly", "monthly")
DAYS = ("any", "tue", "fri")
KINDS = ("housekeeper", "builder")
JOB_STATUSES = ("todo", "doing", "done")
CLAIM_STATUSES = ("submitted", "approved", "paid")
PHOTO_KINDS = ("done", "fix")
PHOTO_MAX_BYTES = 8 * 1024 * 1024
PHOTO_NAME_RE = re.compile(r"^[0-9a-f]{32}\.jpg$")
JPEG_MAGIC = b"\xff\xd8\xff"
NOT_YOURS = "That isn't part of your run sheet."
_DUMMY_HASH = hash_password(secrets.token_hex(16))  # equalises login timing for unknown usernames


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def endpoint(role: str | None = None, kind: str | None = None, public: bool = False, raw_body: bool = False,
             max_bytes: int | None = None):
    """Wrap a sync handler(request, conn, user, body) with auth, CSRF header check, JSON parsing and errors.

    role="owner" lets only owners through. kind="housekeeper" or kind="builder" lets owners through plus
    staff of that kind: the visit run sheet is the housekeeper's, the job list the builder's.

    raw_body=True skips JSON parsing and passes the request body straight through as bytes (photo uploads).
    max_bytes caps a raw body before it is buffered: an oversized Content-Length is refused unread, and a
    chunked body is abandoned as soon as it goes over. Both happen before the session lookup, so an
    anonymous request can't make a worker hold an arbitrary amount of memory.
    """
    def deco(fn: Callable[..., Any]):
        async def handler(request: Request) -> Response:
            if request.method not in ("GET", "HEAD") and request.headers.get("x-hrs") != "1":
                return err(403, "Missing X-HRS request header.")
            body: Any = b"" if raw_body else {}
            if raw_body and max_bytes is not None:
                declared = request.headers.get("content-length", "")
                # isascii() as well as isdigit(): "²".isdigit() is True, and int("²") raises.
                if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
                    return err(413, "That photo is too large.")
                chunks: list[bytes] = []
                total = 0
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > max_bytes:
                        return err(413, "That photo is too large.")  # stop reading; don't keep the rest
                    chunks.append(chunk)
                body = b"".join(chunks)
            elif raw_body:
                body = await request.body()
            elif request.method in ("POST", "PUT", "PATCH"):
                raw = await request.body()
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        return err(400, "Request body must be JSON.")
                    if not isinstance(body, dict):
                        return err(400, "Request body must be a JSON object.")

            def run() -> Response:
                conn = connect()
                try:
                    user = None
                    if not public:
                        user = user_for_token(conn, request.cookies.get(COOKIE_NAME))
                        if not user:
                            return err(401, "Please sign in.")
                        if role and user["role"] != role:
                            return err(403, "Only an owner can do that.")
                        if kind and user["role"] != "owner" and user["kind"] != kind:
                            return err(403, NOT_YOURS)
                    result = fn(request, conn, user, body)
                    return result if isinstance(result, Response) else JSONResponse(result)
                except ApiError as e:
                    return err(e.status, e.message)
                finally:
                    conn.close()

            return await run_in_threadpool(run)
        return handler
    return deco


# ---------- validation helpers ----------
def s(body: dict, key: str, max_len: int, required: bool = False, default: str = "") -> str:
    v = body.get(key, default)
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise ApiError(400, f"'{key}' must be text.")
    v = v.strip()
    if required and not v:
        raise ApiError(400, f"'{key}' is required.")
    if len(v) > max_len:
        raise ApiError(400, f"'{key}' is too long (max {max_len} characters).")
    return v


def choice(body: dict, key: str, options: tuple, default: str | None = None) -> str:
    v = body.get(key, default)
    if v not in options:
        raise ApiError(400, f"'{key}' must be one of: {', '.join(options)}.")
    return v


def boolean(body: dict, key: str) -> bool:
    v = body.get(key)
    if not isinstance(v, bool):
        raise ApiError(400, f"'{key}' must be true or false.")
    return v


def num(body: Any, key: str, lo: int, hi: int) -> int:
    v = body.get(key) if isinstance(body, dict) else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise ApiError(400, f"'{key}' must be a number from {lo} to {hi}.")
    return int(v)


def path_date(request: Request, key: str = "date") -> str:
    v = request.path_params[key]
    if not DATE_RE.match(v):
        raise ApiError(400, "Dates must look like 2026-09-29.")
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, "That date doesn't exist.")
    return v


def sees(u, kind: str) -> bool:
    """Whether this person gets the housekeeper's or the builder's part of the app. Owners get both."""
    return u["role"] == "owner" or u["kind"] == kind


def me_dict(u) -> dict:
    """The signed-in person's own record. doorCode is only ever their own."""
    return {"id": u["id"], "username": u["username"], "displayName": u["display_name"], "role": u["role"],
            "label": u["label"], "kind": u["kind"], "phone": u["phone"], "doorCode": u["door_code"]}


# ---------- auth ----------
def _session_response(conn, row) -> JSONResponse:
    token, max_age = create_session(conn, row["id"])
    resp = JSONResponse({"me": me_dict(row)})
    resp.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True, secure=get_config().cookie_secure,
                    samesite="lax", path="/")
    return resp


@endpoint(public=True)
def login(request, conn, _user, body):
    username = s(body, "username", 64, required=True).lower()
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    if throttle.blocked(username):
        return err(429, "Too many failed attempts. Wait 15 minutes and try again.")
    row = conn.execute("SELECT * FROM users WHERE username = ? AND active = 1", (username,)).fetchone()
    # Password sign-in is for owners, plus staff who have no phone on file yet (so the v2 upgrade
    # can't lock anyone out). Always run scrypt so unknown usernames take the same time.
    stored = row["password_hash"] if row and row["password_hash"] else _DUMMY_HASH
    ok = verify_password(password, stored)
    if not row or not row["password_hash"] or not (row["role"] == "owner" or row["phone"] is None):
        ok = False
    if not ok:
        throttle.fail(username)
        return err(401, "Wrong username or password.")
    throttle.reset(username)
    return _session_response(conn, row)


@endpoint(public=True)
def login_options(request, conn, _user, _body):
    return {"sms": get_config().sms_enabled}


@endpoint(public=True)
def login_sms_start(request, conn, _user, body):
    phone = normalize_phone(body.get("phone") if isinstance(body.get("phone"), str) else "")
    if not get_config().sms_enabled:
        return JSONResponse({"error": "Text-message sign-in isn't set up.", "smsUnavailable": True}, status_code=503)
    if send_throttle.blocked(phone):
        return err(429, "Too many codes sent to that number. Wait 10 minutes.")
    send_throttle.fail(phone)  # counted whether or not we actually text, so timing can't leak
    row = conn.execute("SELECT id FROM users WHERE phone = ? AND active = 1", (phone,)).fetchone()
    if row:
        try:
            sms.start_verification(phone)
        except sms.SmsError:
            # Swallowed on purpose: start_verification only runs for a number that IS on file, so
            # any distinct answer here — even a generic 502 — would confirm membership. app/sms.py
            # has already logged the failure (with the number redacted); add nothing.
            pass
    # One answer for every case: on file or not, texted or not. No enumeration oracle in the response —
    # but a number that is on file costs a Twilio round trip and one that isn't costs nothing, so the
    # timing still tells an attacker (3 probes per number per 10 minutes, each one texting a real
    # person). Closing that means sending off the request path, which v2 deliberately doesn't do.
    return {"ok": True}


@endpoint(public=True)
def login_sms_check(request, conn, _user, body):
    phone = normalize_phone(body.get("phone") if isinstance(body.get("phone"), str) else "")
    code = s(body, "code", 10, required=True)
    if not CODE_RE.match(code):
        raise ApiError(400, "Enter the 6-digit code from the text message.")
    if not get_config().sms_enabled:
        # Without a Verify service SID the check would hit a bogus URL and read as "wrong code".
        return JSONResponse({"error": "Text-message sign-in isn't set up.", "smsUnavailable": True}, status_code=503)
    if sms_check_throttle.blocked(phone):
        return err(429, "Too many failed attempts. Wait 15 minutes and try again.")
    row = conn.execute("SELECT * FROM users WHERE phone = ? AND active = 1", (phone,)).fetchone()
    ok = False
    if row:
        try:
            ok = sms.check_verification(phone, code)
        except sms.SmsError as e:
            return err(502, str(e))
    if not ok:
        sms_check_throttle.fail(phone)
        return err(401, "That code isn't right or has expired.")
    sms_check_throttle.reset(phone)
    return _session_response(conn, row)


@endpoint(public=True)
def logout(request, conn, _user, _body):
    delete_session(conn, request.cookies.get(COOKIE_NAME))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


@endpoint()
def me(request, conn, user, _body):
    return {"me": me_dict(user)}


@endpoint()
def change_my_password(request, conn, user, body):
    if user["role"] != "owner":
        raise ApiError(400, "Staff sign in by text message and don't have a password.")
    current = body.get("current") if isinstance(body.get("current"), str) else ""
    new = body.get("new") if isinstance(body.get("new"), str) else ""
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user["id"],)).fetchone()
    if not verify_password(current, row["password_hash"] or ""):
        raise ApiError(400, "Your current password is wrong.")
    if problem := password_problem(new):
        raise ApiError(400, problem)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user["id"]))
    delete_user_sessions(conn, user["id"], keep_token=request.cookies.get(COOKIE_NAME))
    return {"ok": True}


# ---------- state (polled by the page) ----------
@endpoint()
def state(request, conn, user, _body):
    # Every key is always there, so the page has one shape to render; what isn't part of someone's run
    # sheet comes back empty rather than missing. A builder gets no tasks or visits, a housekeeper no
    # jobs, and a housekeeper sees only their own hours.
    housekeeping, building = sees(user, "housekeeper"), sees(user, "builder")
    hours: list[dict] = []
    if user["role"] == "owner":
        hours = store.list_claims(conn)
    elif housekeeping:
        hours = store.list_claims(conn, user["id"])
    return {"me": me_dict(user),
            "tasks": store.list_tasks(conn) if housekeeping else [],
            "visits": store.list_visits(conn) if housekeeping else {},
            "jobs": store.list_jobs(conn) if building else [],
            "hours": hours}


# ---------- tasks (owner) ----------
def _task_fields(body: dict, partial: bool) -> dict:
    out = {}
    if not partial or "title" in body:
        out["title"] = s(body, "title", 200, required=True)
    if not partial or "area" in body:
        out["area"] = s(body, "area", 60) or "Whole house"
    if not partial or "freq" in body:
        out["freq"] = choice(body, "freq", FREQS, "visit")
    if not partial or "day" in body:
        out["day"] = choice(body, "day", DAYS, "any")
    if not partial or "notes" in body:
        out["notes"] = s(body, "notes", 500)
    return out


@endpoint(role="owner")
def create_task(request, conn, user, body):
    f = _task_fields(body, partial=False)
    tid = "t" + secrets.token_hex(4)
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM tasks").fetchone()[0]
    store.upsert_task(conn, tid, {**f, "order": order})
    return {"task": store.task_dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())}


@endpoint(role="owner")
def update_task(request, conn, user, body):
    tid = request.path_params["task_id"]
    f = _task_fields(body, partial=True)
    if not f:
        raise ApiError(400, "Nothing to change.")
    sets = ", ".join(f"{k} = :{k}" for k in f)
    cur = conn.execute(f"UPDATE tasks SET {sets}, updated_at = :now WHERE id = :id AND active = 1", {**f, "now": now_iso(), "id": tid})
    if cur.rowcount == 0:
        raise ApiError(404, "That task doesn't exist.")
    return {"task": store.task_dict(conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone())}


@endpoint(role="owner")
def delete_task(request, conn, user, _body):
    cur = conn.execute("UPDATE tasks SET active = 0, updated_at = ? WHERE id = ?", (now_iso(), request.path_params["task_id"]))
    if cur.rowcount == 0:
        raise ApiError(404, "That task doesn't exist.")
    return {"ok": True}


# ---------- visits ----------
@endpoint(kind="housekeeper")
def set_task_done(request, conn, user, body):
    d, tid = path_date(request), request.path_params["task_id"]
    done = boolean(body, "done")
    if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone():
        raise ApiError(404, "That task doesn't exist.")
    if done:
        conn.execute("""INSERT INTO task_completions (visit_date, task_id, done_at, done_by) VALUES (?, ?, ?, ?)
                        ON CONFLICT(visit_date, task_id) DO NOTHING""", (d, tid, now_iso(), user["id"]))
    else:
        conn.execute("DELETE FROM task_completions WHERE visit_date = ? AND task_id = ?", (d, tid))
    return {"ok": True}


@endpoint(kind="housekeeper")
def set_visit_note(request, conn, user, body):
    d = path_date(request)
    note = s(body, "note", 4000)
    conn.execute("""INSERT INTO visits (date, note, updated_at, updated_by) VALUES (?, ?, ?, ?)
                    ON CONFLICT(date) DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at, updated_by = excluded.updated_by""",
                 (d, note, now_iso(), user["id"]))
    return {"ok": True}


@endpoint(role="owner")
def add_extra(request, conn, user, body):
    d = path_date(request)
    title, notes = s(body, "title", 200, required=True), s(body, "notes", 500)
    cur = conn.execute("INSERT INTO visit_extras (visit_date, title, notes, created_at, created_by) VALUES (?, ?, ?, ?, ?)",
                       (d, title, notes, now_iso(), user["id"]))
    return {"id": str(cur.lastrowid)}


def _extra_id(request) -> int:
    try:
        return int(request.path_params["extra_id"])
    except ValueError:
        raise ApiError(404, "That job doesn't exist.")


@endpoint(kind="housekeeper")
def set_extra_done(request, conn, user, body):
    d, eid = path_date(request), _extra_id(request)
    done = boolean(body, "done")
    cur = conn.execute("UPDATE visit_extras SET done_at = ?, done_by = ? WHERE id = ? AND visit_date = ?",
                       (now_iso() if done else None, user["id"] if done else None, eid, d))
    if cur.rowcount == 0:
        raise ApiError(404, "That job doesn't exist.")
    return {"ok": True}


@endpoint(role="owner")
def delete_extra(request, conn, user, _body):
    d, eid = path_date(request), _extra_id(request)
    conn.execute("DELETE FROM visit_extras WHERE id = ? AND visit_date = ?", (eid, d))
    return {"ok": True}


# ---------- photos ----------
def _photos_dir() -> Path:
    return Path(get_config().photos_dir)


def _caption(request) -> str:
    caption = (request.query_params.get("caption") or "").strip()
    if len(caption) > 300:
        raise ApiError(400, "'caption' is too long (max 300 characters).")
    return caption


def save_jpeg(body: bytes) -> str:
    """Check an uploaded photo and write it under a fresh random name, which is returned.

    Shared by visit photos, job photos and `cli add-job-photo`. The name is never derived from input.
    """
    if len(body) > PHOTO_MAX_BYTES:  # defence in depth: endpoint(max_bytes=…) already refused it unread
        raise ApiError(413, "That photo is too large.")
    if not body.startswith(JPEG_MAGIC):
        raise ApiError(415, "Only JPEG photos are accepted.")
    filename = secrets.token_hex(16) + ".jpg"
    directory = _photos_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_bytes(body)
    return filename


def unlink_photo(filename: str) -> None:
    """Remove a photo's file once its row is gone.

    The row is the source of truth: it's gone, so the photo is unreachable whatever happens to the
    file. Re-check the stored name (a hand-edited row shouldn't be able to aim the unlink) and let an
    unlink failure pass — the worst case is an orphaned file nothing can serve.
    """
    if PHOTO_NAME_RE.match(filename):
        with contextlib.suppress(OSError):
            (_photos_dir() / filename).unlink(missing_ok=True)


def _photo_dict(r, display_name: str) -> dict:
    return {"id": r["id"], "kind": r["kind"], "caption": r["caption"], "url": f"/photos/{r['filename']}",
            "createdAt": r["created_at"], "by": {"id": r["created_by"], "displayName": display_name}}


@endpoint(kind="housekeeper", raw_body=True, max_bytes=PHOTO_MAX_BYTES)
def add_photo(request, conn, user, body):
    d = path_date(request)
    kind = request.query_params.get("kind", "done")
    if kind not in PHOTO_KINDS:
        raise ApiError(400, f"'kind' must be one of: {', '.join(PHOTO_KINDS)}.")
    caption = _caption(request)
    filename = save_jpeg(body)
    cur = conn.execute(
        """INSERT INTO visit_photos (visit_date, kind, caption, filename, bytes, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, ?, ?)""", (d, kind, caption, filename, len(body), now_iso(), user["id"]))
    row = conn.execute("SELECT * FROM visit_photos WHERE id = ?", (cur.lastrowid,)).fetchone()
    return {"photo": _photo_dict(row, user["display_name"])}


@endpoint()
def get_photo(request, conn, user, _body):
    """Served from outside /api/ so the no-store header doesn't apply; still needs the session cookie.

    The name must be in visit_photos (owners and housekeepers) or job_photos (owners and builders)
    before the disk is touched. Someone the photo isn't for gets the same 404 as a name that
    doesn't exist, so a builder can't probe for visit photos or the other way round.
    """
    name = request.path_params["filename"]
    missing = ApiError(404, "That photo doesn't exist.")
    if not PHOTO_NAME_RE.match(name):
        raise missing
    allowed = (sees(user, "housekeeper")
               and conn.execute("SELECT 1 FROM visit_photos WHERE filename = ?", (name,)).fetchone()) or \
              (sees(user, "builder")
               and conn.execute("SELECT 1 FROM job_photos WHERE filename = ?", (name,)).fetchone())
    if not allowed:
        raise missing
    path = _photos_dir() / name
    if not path.is_file():
        raise missing
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800, immutable"})


@endpoint(kind="housekeeper")
def delete_photo(request, conn, user, _body):
    d = path_date(request)
    try:
        pid = int(request.path_params["photo_id"])
    except ValueError:
        raise ApiError(404, "That photo doesn't exist.")
    row = conn.execute("SELECT * FROM visit_photos WHERE id = ? AND visit_date = ?", (pid, d)).fetchone()
    if not row:
        raise ApiError(404, "That photo doesn't exist.")
    if user["role"] != "owner" and row["created_by"] != user["id"]:
        raise ApiError(403, "You can only delete your own photos.")
    conn.execute("DELETE FROM visit_photos WHERE id = ?", (pid,))
    unlink_photo(row["filename"])
    return {"ok": True}


# ---------- jobs (owners and builders) ----------
def _int_param(request, key: str, missing: str) -> int:
    try:
        return int(request.path_params[key])
    except ValueError:
        raise ApiError(404, missing)


def _job_id(request) -> int:
    return _int_param(request, "job_id", "That job doesn't exist.")


def _active_job(conn, request) -> int:
    """The id of an active job, or 404. A deleted job (active = 0) is gone as far as the API goes."""
    jid = _job_id(request)
    if not conn.execute("SELECT 1 FROM jobs WHERE id = ? AND active = 1", (jid,)).fetchone():
        raise ApiError(404, "That job doesn't exist.")
    return jid


def job_link(body: dict) -> str:
    link = s(body, "link", 500)
    if link and not link.lower().startswith(("http://", "https://")):
        raise ApiError(400, "A link has to start with http:// or https://.")
    return link


def _job_fields(body: dict, partial: bool) -> dict:
    out = {}
    if not partial or "title" in body:
        out["title"] = s(body, "title", 200, required=True)
    if not partial or "details" in body:
        out["details"] = s(body, "details", 4000)
    if not partial or "link" in body:
        out["link"] = job_link(body)
    return out


@endpoint(kind="builder")
def list_jobs(request, conn, user, _body):
    return {"jobs": store.list_jobs(conn)}


@endpoint(role="owner")
def create_job(request, conn, user, body):
    f = _job_fields(body, partial=False)
    jid = store.add_job(conn, f["title"], f["details"], f["link"], user["id"])
    return {"job": store.get_job(conn, jid)}


@endpoint(kind="builder")
def update_job(request, conn, user, body):
    jid = _active_job(conn, request)
    if user["role"] != "owner" and set(body) - {"status"}:
        raise ApiError(403, "Only an owner can do that.")  # the builder moves a job along; owners write it
    f = _job_fields(body, partial=True)
    if "sortOrder" in body:
        f["sort_order"] = num(body, "sortOrder", 0, 1_000_000)
    if "status" in body:
        f["status"] = choice(body, "status", JOB_STATUSES)
    if not f:
        raise ApiError(400, "Nothing to change.")
    now = now_iso()
    with tx(conn):
        sets = ", ".join(f"{k} = :{k}" for k in f)
        conn.execute(f"UPDATE jobs SET {sets}, updated_at = :now WHERE id = :id", {**f, "now": now, "id": jid})
        if f.get("status") == "done":
            # Re-sending done keeps who finished it and when.
            conn.execute("UPDATE jobs SET done_at = ?, done_by = ? WHERE id = ? AND done_at IS NULL",
                         (now, user["id"], jid))
        elif "status" in f:
            conn.execute("UPDATE jobs SET done_at = NULL, done_by = NULL WHERE id = ?", (jid,))
    return {"job": store.get_job(conn, jid)}


@endpoint(role="owner")
def delete_job(request, conn, user, _body):
    jid = _job_id(request)
    cur = conn.execute("UPDATE jobs SET active = 0, updated_at = ? WHERE id = ? AND active = 1", (now_iso(), jid))
    if cur.rowcount == 0:
        raise ApiError(404, "That job doesn't exist.")
    return {"ok": True}


@endpoint(kind="builder")
def add_job_comment(request, conn, user, body):
    jid = _active_job(conn, request)
    text = s(body, "body", 2000, required=True)
    cur = conn.execute("INSERT INTO job_comments (job_id, body, created_at, created_by) VALUES (?, ?, ?, ?)",
                       (jid, text, now_iso(), user["id"]))
    row = conn.execute("""SELECT c.*, u.display_name, u.role AS author_role FROM job_comments c
                          LEFT JOIN users u ON u.id = c.created_by WHERE c.id = ?""", (cur.lastrowid,)).fetchone()
    return {"comment": store.comment_dict(row)}


@endpoint(kind="builder")
def delete_job_comment(request, conn, user, _body):
    jid = _active_job(conn, request)
    cid = _int_param(request, "comment_id", "That comment doesn't exist.")
    row = conn.execute("SELECT created_by FROM job_comments WHERE id = ? AND job_id = ?", (cid, jid)).fetchone()
    if not row:
        raise ApiError(404, "That comment doesn't exist.")
    if user["role"] != "owner" and row["created_by"] != user["id"]:
        raise ApiError(403, "You can only delete your own comments.")
    conn.execute("DELETE FROM job_comments WHERE id = ?", (cid,))
    return {"ok": True}


@endpoint(kind="builder", raw_body=True, max_bytes=PHOTO_MAX_BYTES)
def add_job_photo(request, conn, user, body):
    jid = _active_job(conn, request)
    caption = _caption(request)
    filename = save_jpeg(body)
    cur = conn.execute("""INSERT INTO job_photos (job_id, caption, filename, bytes, created_at, created_by)
                          VALUES (?, ?, ?, ?, ?, ?)""", (jid, caption, filename, len(body), now_iso(), user["id"]))
    row = conn.execute("""SELECT p.*, u.display_name, u.role AS author_role FROM job_photos p
                          LEFT JOIN users u ON u.id = p.created_by WHERE p.id = ?""", (cur.lastrowid,)).fetchone()
    return {"photo": store.job_photo_dict(row)}


@endpoint(kind="builder")
def delete_job_photo(request, conn, user, _body):
    jid = _active_job(conn, request)
    pid = _int_param(request, "photo_id", "That photo doesn't exist.")
    row = conn.execute("SELECT created_by, filename FROM job_photos WHERE id = ? AND job_id = ?", (pid, jid)).fetchone()
    if not row:
        raise ApiError(404, "That photo doesn't exist.")
    if user["role"] != "owner" and row["created_by"] != user["id"]:
        raise ApiError(403, "You can only delete your own photos.")
    conn.execute("DELETE FROM job_photos WHERE id = ?", (pid,))
    unlink_photo(row["filename"])
    return {"ok": True}


# ---------- hours claims (housekeepers claim, owners approve and pay) ----------
ALREADY_APPROVED = "That claim has already been approved."


def _work_date(body: dict) -> str:
    v = body.get("date")
    if not isinstance(v, str) or not DATE_RE.match(v):
        raise ApiError(400, "Dates must look like 2026-09-29.")
    try:
        d = date.fromisoformat(v)
    except ValueError:
        raise ApiError(400, "That date doesn't exist.")
    if d > date.today():
        raise ApiError(400, "You can't claim hours for a future day.")
    return v


def _minutes(body: dict) -> int:
    v = body.get("minutes")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or not 15 <= v <= 960 or v % 15:
        raise ApiError(400, "Hours go in quarter hours, from 0.25 to 16.")
    return v


def _claim_for(conn, request, user):
    """The claim row, if this person may see it: owners any, a housekeeper only their own. Others 404."""
    cid = _int_param(request, "claim_id", "That claim doesn't exist.")
    row = conn.execute("SELECT * FROM hours_claims WHERE id = ?", (cid,)).fetchone()
    if not row or (user["role"] != "owner" and row["user_id"] != user["id"]):
        raise ApiError(404, "That claim doesn't exist.")
    return row


@endpoint(kind="housekeeper")
def list_hours(request, conn, user, _body):
    return {"claims": store.list_claims(conn, None if user["role"] == "owner" else user["id"])}


@endpoint(kind="housekeeper")
def create_claim(request, conn, user, body):
    if user["role"] == "owner":
        raise ApiError(403, "Only staff claim hours.")
    work_date, minutes, note = _work_date(body), _minutes(body), s(body, "note", 500)
    now = now_iso()
    cur = conn.execute("""INSERT INTO hours_claims (user_id, work_date, minutes, note, created_at, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?)""", (user["id"], work_date, minutes, note, now, now))
    return {"claim": store.get_claim(conn, cur.lastrowid)}


@endpoint(kind="housekeeper")
def update_claim(request, conn, user, body):
    row = _claim_for(conn, request, user)
    now = now_iso()
    if user["role"] == "owner":
        if set(body) & {"date", "minutes", "note"}:
            raise ApiError(403, "Only the person who claimed the hours can change those.")
        if "status" not in body:
            raise ApiError(400, "Nothing to change.")
        status = choice(body, "status", CLAIM_STATUSES)
        # Each stamp says when the claim reached that step. Moving forward fills what's missing (paying a
        # claim nobody approved approves it too); moving back clears the steps it undoes.
        approved = (row["approved_at"] or now, row["approved_by"] or user["id"]) if status != "submitted" else (None, None)
        paid = (row["paid_at"] or now, row["paid_by"] or user["id"]) if status == "paid" else (None, None)
        conn.execute("""UPDATE hours_claims SET status = ?, approved_at = ?, approved_by = ?, paid_at = ?, paid_by = ?,
                        updated_at = ? WHERE id = ?""", (status, *approved, *paid, now, row["id"]))
        return {"claim": store.get_claim(conn, row["id"])}
    if "status" in body:
        raise ApiError(403, "Only an owner can do that.")
    if row["status"] != "submitted":
        raise ApiError(409, ALREADY_APPROVED)
    f = {}
    if "date" in body:
        f["work_date"] = _work_date(body)
    if "minutes" in body:
        f["minutes"] = _minutes(body)
    if "note" in body:
        f["note"] = s(body, "note", 500)
    if not f:
        raise ApiError(400, "Nothing to change.")
    sets = ", ".join(f"{k} = :{k}" for k in f)
    # status = 'submitted' in the WHERE as well: an owner approving it in between wins.
    cur = conn.execute(f"UPDATE hours_claims SET {sets}, updated_at = :now WHERE id = :id AND status = 'submitted'",
                       {**f, "now": now, "id": row["id"]})
    if cur.rowcount == 0:
        raise ApiError(409, ALREADY_APPROVED)
    return {"claim": store.get_claim(conn, row["id"])}


@endpoint(kind="housekeeper")
def delete_claim(request, conn, user, _body):
    row = _claim_for(conn, request, user)
    if user["role"] == "owner":
        conn.execute("DELETE FROM hours_claims WHERE id = ?", (row["id"],))
        return {"ok": True}
    cur = conn.execute("DELETE FROM hours_claims WHERE id = ? AND status = 'submitted'", (row["id"],))
    if cur.rowcount == 0:
        raise ApiError(409, ALREADY_APPROVED)
    return {"ok": True}


# ---------- people (owner) ----------
def user_dict(r) -> dict:
    return {"id": r["id"], "username": r["username"], "displayName": r["display_name"], "role": r["role"],
            "label": r["label"], "kind": r["kind"], "phone": r["phone"], "doorCode": r["door_code"],
            "active": bool(r["active"]),
            "hasPassword": bool(r["password_hash"])}


def _derive_username(conn, display: str) -> str:
    """Make a username from a display name: lower-case [a-z0-9.], then -2, -3… until it's free."""
    base = re.sub(r"[^a-z0-9.]", "", display.lower())[:56] or "person"
    name, n = base, 1
    while conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone():
        n += 1
        name = f"{base}-{n}"
    return name


def _phone_field(body: dict, conn, exclude_id: int | None) -> str | None:
    """Normalized phone from the body, or None when blank. Raises 409 if someone else has it."""
    raw = body.get("phone")
    if not isinstance(raw, str) or not raw.strip():
        return None  # NULL, never '': the UNIQUE index would collide on the second blank
    phone = normalize_phone(raw)
    if exclude_id is None:
        row = conn.execute("SELECT display_name FROM users WHERE phone = ?", (phone,)).fetchone()
    else:
        row = conn.execute("SELECT display_name FROM users WHERE phone = ? AND id != ?", (phone, exclude_id)).fetchone()
    if row:
        raise ApiError(409, f"That phone number is already used by {row['display_name']}.")
    return phone


def _door_code_field(body: dict) -> str | None:
    raw = body.get("doorCode")
    if not isinstance(raw, str) or not raw.strip():
        return None
    code = raw.strip()
    if not DOOR_CODE_RE.match(code):
        raise ApiError(400, "A door code is 4 to 8 digits.")
    return code


@endpoint(role="owner")
def list_users(request, conn, user, _body):
    return {"users": [user_dict(r) for r in conn.execute("SELECT * FROM users ORDER BY role, display_name")]}


@endpoint(role="owner")
def create_user(request, conn, user, body):
    display = s(body, "displayName", 80, required=True)
    role = choice(body, "role", ROLES, "staff")
    label = s(body, "label", 40)
    phone = _phone_field(body, conn, None)
    door_code = _door_code_field(body)
    kind = choice(body, "kind", KINDS, "housekeeper")
    username = s(body, "username", 64).lower()
    if username:
        if not USERNAME_RE.match(username):
            raise ApiError(400, "Usernames use letters, numbers, dots, dashes or underscores.")
        if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise ApiError(409, "That username is taken.")
    else:
        username = _derive_username(conn, display)
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    pw_hash = None
    if role != "owner" and phone and password:
        # Say so rather than dropping it: login() would never accept this password, so storing a hash
        # would leave an unusable credential behind and the client would get no hint it was ignored.
        raise ApiError(400, "They sign in by text, so they don't need a password.")
    # Owners always have a password. Staff sign in by text, so one is only kept for staff who have no
    # phone yet: login() refuses a password from staff who do, so that hash could never be used again.
    if role == "owner" or (password and not phone):
        if problem := password_problem(password):
            raise ApiError(400, problem)
        pw_hash = hash_password(password)
    cur = conn.execute(
        """INSERT INTO users (username, display_name, role, label, kind, phone, door_code, password_hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (username, display, role, label, kind, phone, door_code, pw_hash, now_iso()))
    return {"user": user_dict(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone())}


@endpoint(role="owner")
def update_user(request, conn, user, body):
    try:
        uid = int(request.path_params["user_id"])
    except ValueError:
        raise ApiError(404, "That person doesn't exist.")
    target = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not target:
        raise ApiError(404, "That person doesn't exist.")
    new_role = choice(body, "role", ROLES) if "role" in body else target["role"]
    with tx(conn):
        if "displayName" in body:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (s(body, "displayName", 80, required=True), uid))
        if "label" in body:
            conn.execute("UPDATE users SET label = ? WHERE id = ?", (s(body, "label", 40), uid))
        if "kind" in body:
            conn.execute("UPDATE users SET kind = ? WHERE id = ?", (choice(body, "kind", KINDS), uid))
        if "doorCode" in body:
            conn.execute("UPDATE users SET door_code = ? WHERE id = ?", (_door_code_field(body), uid))
        new_phone = target["phone"]
        if "phone" in body:
            new_phone = _phone_field(body, conn, uid)
            conn.execute("UPDATE users SET phone = ? WHERE id = ?", (new_phone, uid))
        if "role" in body or "active" in body:
            active = boolean(body, "active") if "active" in body else bool(target["active"])
            if uid == user["id"] and (new_role != "owner" or not active):
                raise ApiError(400, "You can't remove your own owner access.")
            # Owners always have a password (§3.1): without one they can neither sign in by password nor
            # by text, so a promotion has to bring a password with it unless they kept an old hash.
            if "role" in body and new_role == "owner" and target["password_hash"] is None \
                    and not isinstance(body.get("password"), str):
                raise ApiError(400, "Give them a password when you make them an owner.")
            conn.execute("UPDATE users SET role = ?, active = ? WHERE id = ?", (new_role, 1 if active else 0, uid))
            if not active:
                delete_user_sessions(conn, uid)
        phone_changed = "phone" in body and new_phone != target["phone"]
        became_staff = "role" in body and new_role != "owner" and target["role"] == "owner"
        if new_phone and new_role != "owner" and (phone_changed or became_staff):
            # This change *moved* them to staff-with-phone, whether it gave them the phone or took away
            # the owner role: they sign in by text from now on, so the password that login() will no
            # longer accept goes, and the sessions it opened go with it. Only a real transition counts —
            # the People form re-submits every field on Save, and re-sending the number someone already
            # has must not sign them out mid-visit.
            conn.execute("UPDATE users SET password_hash = NULL WHERE id = ?", (uid,))
            delete_user_sessions(conn, uid)
        if "password" in body:
            if new_role != "owner":
                raise ApiError(400, "Staff sign in by text message and don't have a password.")
            pw = body.get("password") if isinstance(body.get("password"), str) else ""
            if problem := password_problem(pw):
                raise ApiError(400, problem)
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(pw), uid))
            if uid != user["id"]:
                delete_user_sessions(conn, uid)
    return {"user": user_dict(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())}


async def healthz(request: Request) -> Response:
    return JSONResponse({"ok": True})


routes = [
    Route("/healthz", healthz),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/login/options", login_options),
    Route("/api/login/sms/start", login_sms_start, methods=["POST"]),
    Route("/api/login/sms/check", login_sms_check, methods=["POST"]),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/me", me),
    Route("/api/me/password", change_my_password, methods=["PUT"]),
    Route("/api/state", state),
    Route("/api/tasks", create_task, methods=["POST"]),
    Route("/api/tasks/{task_id}", update_task, methods=["PATCH"]),
    Route("/api/tasks/{task_id}", delete_task, methods=["DELETE"]),
    Route("/api/visits/{date}/tasks/{task_id}", set_task_done, methods=["PUT"]),
    Route("/api/visits/{date}/note", set_visit_note, methods=["PUT"]),
    Route("/api/visits/{date}/extras", add_extra, methods=["POST"]),
    Route("/api/visits/{date}/extras/{extra_id}", set_extra_done, methods=["PATCH"]),
    Route("/api/visits/{date}/extras/{extra_id}", delete_extra, methods=["DELETE"]),
    Route("/api/visits/{date}/photos", add_photo, methods=["POST"]),
    Route("/api/visits/{date}/photos/{photo_id}", delete_photo, methods=["DELETE"]),
    Route("/api/jobs", list_jobs),
    Route("/api/jobs", create_job, methods=["POST"]),
    Route("/api/jobs/{job_id}", update_job, methods=["PATCH"]),
    Route("/api/jobs/{job_id}", delete_job, methods=["DELETE"]),
    Route("/api/jobs/{job_id}/comments", add_job_comment, methods=["POST"]),
    Route("/api/jobs/{job_id}/comments/{comment_id}", delete_job_comment, methods=["DELETE"]),
    Route("/api/jobs/{job_id}/photos", add_job_photo, methods=["POST"]),
    Route("/api/jobs/{job_id}/photos/{photo_id}", delete_job_photo, methods=["DELETE"]),
    Route("/api/hours", list_hours),
    Route("/api/hours", create_claim, methods=["POST"]),
    Route("/api/hours/{claim_id}", update_claim, methods=["PATCH"]),
    Route("/api/hours/{claim_id}", delete_claim, methods=["DELETE"]),
    Route("/api/users", list_users),
    Route("/api/users", create_user, methods=["POST"]),
    Route("/api/users/{user_id}", update_user, methods=["PATCH"]),
    Route("/photos/{filename}", get_photo),
]
