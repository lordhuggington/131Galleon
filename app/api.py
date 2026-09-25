"""JSON API. Every handler runs in a worker thread with its own SQLite connection."""
from __future__ import annotations

import json
import re
import secrets
from datetime import date
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import ai, sms, store
from .auth import (COOKIE_NAME, ROLES, create_session, delete_session, delete_user_sessions, hash_password,
                   normalize_phone, password_problem, send_throttle, throttle, user_for_token, verify_password)
from .config import get_config
from .db import connect, now_iso, tx

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE_RE = re.compile(r"^\d{4,10}$")
FREQS = ("visit", "weekly", "fortnightly", "monthly")
DAYS = ("any", "tue", "fri")
SESSIONS = ("tue", "fri")
SLOTS = ("breakfast", "main", "dessert")
_DUMMY_HASH = hash_password(secrets.token_hex(16))  # equalises login timing for unknown usernames


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def endpoint(role: str | None = None, public: bool = False):
    """Wrap a sync handler(request, conn, user, body) with auth, CSRF header check, JSON parsing and errors."""
    def deco(fn: Callable[..., Any]):
        async def handler(request: Request) -> Response:
            if request.method not in ("GET", "HEAD") and request.headers.get("x-hrs") != "1":
                return err(403, "Missing X-HRS request header.")
            body: dict = {}
            if request.method in ("POST", "PUT", "PATCH"):
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


def path_week(request: Request) -> str:
    w = path_date(request, "week")
    if date.fromisoformat(w).weekday() != 0:
        raise ApiError(400, "A week is identified by its Monday.")
    return w


def me_dict(u) -> dict:
    """The signed-in person's own record. doorCode is only ever their own."""
    return {"id": u["id"], "username": u["username"], "displayName": u["display_name"], "role": u["role"],
            "label": u["label"], "phone": u["phone"], "doorCode": u["door_code"],
            "canSeeMeals": u["role"] == "owner" or bool(u["can_see_meals"])}


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
        except sms.SmsError as e:
            return err(502, str(e))
    return {"ok": True}  # identical answer for unknown numbers: no enumeration


@endpoint(public=True)
def login_sms_check(request, conn, _user, body):
    phone = normalize_phone(body.get("phone") if isinstance(body.get("phone"), str) else "")
    code = s(body, "code", 10, required=True)
    if not CODE_RE.match(code):
        raise ApiError(400, "Enter the 6-digit code from the text message.")
    if not get_config().sms_enabled:
        # Without a Verify service SID the check would hit a bogus URL and read as "wrong code".
        return JSONResponse({"error": "Text-message sign-in isn't set up.", "smsUnavailable": True}, status_code=503)
    if throttle.blocked(phone):
        return err(429, "Too many failed attempts. Wait 15 minutes and try again.")
    row = conn.execute("SELECT * FROM users WHERE phone = ? AND active = 1", (phone,)).fetchone()
    ok = False
    if row:
        try:
            ok = sms.check_verification(phone, code)
        except sms.SmsError as e:
            return err(502, str(e))
    if not ok:
        throttle.fail(phone)
        return err(401, "That code isn't right or has expired.")
    throttle.reset(phone)
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
    return {"me": me_dict(user), "tasks": store.list_tasks(conn), "visits": store.list_visits(conn),
            "settings": store.get_settings(conn)}


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
@endpoint()
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


@endpoint()
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


@endpoint()
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


# ---------- settings ----------
@endpoint(role="owner")
def put_settings(request, conn, user, body):
    out = {"kcal": num(body, "kcal", 200, 1500), "protein": num(body, "protein", 10, 150)}
    for k in SESSIONS:
        sess = body.get(k)
        if not isinstance(sess, dict):
            raise ApiError(400, f"'{k}' portions are missing.")
        out[k] = {"breakfast": num(sess, "breakfast", 0, 30), "main": num(sess, "main", 0, 40),
                  "dessert": num(sess, "dessert", 0, 30), "covers": s(sess, "covers", 80, required=True)}
    for k in ("store", "likes", "dislikes", "pantry"):
        out[k] = s(body, k, 1000)
    store.save_settings(conn, out)
    return {"settings": store.get_settings(conn)}


# ---------- meal plans ----------
@endpoint()
def get_plan(request, conn, user, _body):
    return {"plan": store.get_plan(conn, path_week(request), include_shopping=user["role"] == "owner")}


@endpoint(role="owner")
def delete_plan(request, conn, user, _body):
    conn.execute("DELETE FROM meal_plans WHERE week = ?", (path_week(request),))
    return {"ok": True}


@endpoint(role="owner")
def set_fav(request, conn, user, body):
    w = path_week(request)
    sess, slot = request.path_params["session"], request.path_params["slot"]
    if sess not in SESSIONS or slot not in SLOTS:
        raise ApiError(404, "That recipe doesn't exist.")
    with tx(conn):
        if not store.set_recipe_fav(conn, w, sess, slot, boolean(body, "fav")):
            raise ApiError(404, "That recipe doesn't exist.")
    return {"ok": True}


@endpoint(role="owner")
def set_got(request, conn, user, body):
    w = path_week(request)
    cur = conn.execute("UPDATE shopping_items SET got = ? WHERE week = ? AND id = ?",
                       (1 if boolean(body, "got") else 0, w, request.path_params["item_id"]))
    if cur.rowcount == 0:
        raise ApiError(404, "That shopping item doesn't exist.")
    return {"ok": True}


@endpoint(role="owner")
def generate(request, conn, user, body):
    w = path_week(request)
    note = s(body, "note", 500)
    running = conn.execute("SELECT id FROM ai_jobs WHERE status IN ('running', 'cancelling')").fetchone()
    if running:
        return JSONResponse({"error": "A menu is already being written.", "jobId": running["id"]}, status_code=409)
    return {"jobId": ai.start_job(conn, w, note, user["id"])}


def _job_id(request) -> int:
    try:
        return int(request.path_params["job_id"])
    except ValueError:
        raise ApiError(404, "That job doesn't exist.")


@endpoint(role="owner")
def get_job(request, conn, user, _body):
    r = conn.execute("SELECT * FROM ai_jobs WHERE id = ?", (_job_id(request),)).fetchone()
    if not r:
        raise ApiError(404, "That job doesn't exist.")
    return {"id": r["id"], "week": r["week"], "status": r["status"], "progressChars": r["progress_chars"],
            "titles": json.loads(r["titles"]), "error": r["error"]}


@endpoint(role="owner")
def cancel_job(request, conn, user, _body):
    conn.execute("UPDATE ai_jobs SET status = 'cancelling' WHERE id = ? AND status = 'running'", (_job_id(request),))
    return {"ok": True}


# ---------- people (owner) ----------
def user_dict(r) -> dict:
    return {"id": r["id"], "username": r["username"], "displayName": r["display_name"], "role": r["role"],
            "label": r["label"], "phone": r["phone"], "doorCode": r["door_code"],
            "canSeeMeals": bool(r["can_see_meals"]), "active": bool(r["active"]),
            "hasPassword": bool(r["password_hash"])}


@endpoint(role="owner")
def list_users(request, conn, user, _body):
    return {"users": [user_dict(r) for r in conn.execute("SELECT * FROM users ORDER BY role, display_name")]}


@endpoint(role="owner")
def create_user(request, conn, user, body):
    username = s(body, "username", 64, required=True).lower()
    if not re.match(r"^[a-z0-9._-]{2,64}$", username):
        raise ApiError(400, "Usernames use letters, numbers, dots, dashes or underscores.")
    display = s(body, "displayName", 80, required=True)
    role = choice(body, "role", ROLES, "staff")
    password = body.get("password") if isinstance(body.get("password"), str) else ""
    if problem := password_problem(password):
        raise ApiError(400, problem)
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise ApiError(409, "That username is taken.")
    cur = conn.execute("INSERT INTO users (username, display_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                       (username, display, role, hash_password(password), now_iso()))
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
    with tx(conn):
        if "displayName" in body:
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (s(body, "displayName", 80, required=True), uid))
        if "role" in body or "active" in body:
            role = choice(body, "role", ROLES) if "role" in body else target["role"]
            active = boolean(body, "active") if "active" in body else bool(target["active"])
            if uid == user["id"] and (role != "owner" or not active):
                raise ApiError(400, "You can't remove your own owner access.")
            conn.execute("UPDATE users SET role = ?, active = ? WHERE id = ?", (role, 1 if active else 0, uid))
            if not active:
                delete_user_sessions(conn, uid)
        if "password" in body:
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
    Route("/api/settings", put_settings, methods=["PUT"]),
    Route("/api/plans/{week}", get_plan),
    Route("/api/plans/{week}", delete_plan, methods=["DELETE"]),
    Route("/api/plans/{week}/recipes/{session}/{slot}", set_fav, methods=["PATCH"]),
    Route("/api/plans/{week}/shopping/{item_id}", set_got, methods=["PATCH"]),
    Route("/api/plans/{week}/generate", generate, methods=["POST"]),
    Route("/api/jobs/{job_id}", get_job),
    Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
    Route("/api/users", list_users),
    Route("/api/users", create_user, methods=["POST"]),
    Route("/api/users/{user_id}", update_user, methods=["PATCH"]),
]
