"""Data access for tasks, visits, settings and meal plans."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from .db import now_iso

DEFAULT_SETTINGS = {
    "kcal": 500, "protein": 50,
    "tue": {"breakfast": 3, "main": 9, "dessert": 3, "covers": "Wed, Thu, Fri"},
    "fri": {"breakfast": 4, "main": 12, "dessert": 4, "covers": "Sat, Sun, Mon, Tue"},
    "store": "", "likes": "", "dislikes": "", "pantry": "",
}


# ---------- settings ----------
def default_settings() -> dict:
    """A fresh copy of the defaults, nested sessions included. What someone who can't see meals gets."""
    return {**DEFAULT_SETTINGS, "tue": dict(DEFAULT_SETTINGS["tue"]), "fri": dict(DEFAULT_SETTINGS["fri"])}


def get_settings(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT value FROM settings WHERE key = 'meal'").fetchone()
    s = json.loads(row["value"]) if row else {}
    out = default_settings()
    sessions = {k: {**out[k], **(s.get(k) or {})} for k in ("tue", "fri")}
    return {**out, **s, **sessions}


def visible_settings(conn: sqlite3.Connection, can_see_meals: bool) -> dict:
    """What /api/state may show this person: everything for owners and meals-enabled staff;
    otherwise the defaults, except the prep-coverage days the Visit screen shows everyone."""
    settings = get_settings(conn)
    if can_see_meals:
        return settings
    shown = default_settings()
    for session in ("tue", "fri"):
        shown[session]["covers"] = settings[session]["covers"]
    return shown


def save_settings(conn: sqlite3.Connection, s: dict) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES ('meal', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (json.dumps(s),))


# ---------- tasks ----------
def task_dict(r: sqlite3.Row) -> dict:
    d = {"id": r["id"], "title": r["title"], "area": r["area"], "freq": r["freq"], "day": r["day"],
         "notes": r["notes"], "order": r["sort_order"], "active": bool(r["active"])}
    if r["link"]:
        d["link"] = r["link"]
    return d


def list_tasks(conn: sqlite3.Connection) -> list[dict]:
    return [task_dict(r) for r in conn.execute("SELECT * FROM tasks WHERE active = 1 ORDER BY sort_order, id")]


def upsert_task(conn: sqlite3.Connection, tid: str, t: dict) -> None:
    now = now_iso()
    conn.execute(
        """INSERT INTO tasks (id, title, area, freq, day, notes, link, active, sort_order, created_at, updated_at)
           VALUES (:id, :title, :area, :freq, :day, :notes, :link, :active, :sort_order, :now, :now)
           ON CONFLICT(id) DO UPDATE SET title=excluded.title, area=excluded.area, freq=excluded.freq, day=excluded.day,
             notes=excluded.notes, link=excluded.link, active=excluded.active, sort_order=excluded.sort_order, updated_at=excluded.updated_at""",
        {"id": tid, "title": t["title"], "area": t.get("area") or "Whole house", "freq": t.get("freq") or "visit",
         "day": t.get("day") or "any", "notes": t.get("notes") or "", "link": t.get("link"),
         "active": 1 if t.get("active", True) else 0, "sort_order": int(t.get("order") or 0), "now": now},
    )


# ---------- visits ----------
def list_visits(conn: sqlite3.Connection, days_back: int = 400) -> dict:
    since = (date.today() - timedelta(days=days_back)).isoformat()
    visits: dict[str, dict] = {}

    def v(d: str) -> dict:
        return visits.setdefault(d, {"date": d, "note": "", "done": {}, "extras": {}, "photos": []})

    for r in conn.execute("SELECT date, note FROM visits WHERE date >= ?", (since,)):
        v(r["date"])["note"] = r["note"]
    for r in conn.execute("SELECT visit_date, task_id, done_at FROM task_completions WHERE visit_date >= ?", (since,)):
        v(r["visit_date"])["done"][r["task_id"]] = r["done_at"]
    for r in conn.execute("SELECT * FROM visit_extras WHERE visit_date >= ? ORDER BY created_at", (since,)):
        v(r["visit_date"])["extras"][str(r["id"])] = {
            "title": r["title"], "notes": r["notes"], "done": r["done_at"] is not None, "createdAt": r["created_at"]}
    for r in conn.execute("""SELECT p.*, u.display_name FROM visit_photos p
                             LEFT JOIN users u ON u.id = p.created_by
                             WHERE p.visit_date >= ? ORDER BY p.created_at, p.id""", (since,)):
        v(r["visit_date"])["photos"].append(
            {"id": r["id"], "kind": r["kind"], "caption": r["caption"], "url": f"/photos/{r['filename']}",
             "createdAt": r["created_at"], "by": {"id": r["created_by"], "displayName": r["display_name"] or ""}})
    return visits


# ---------- meal plans ----------
def save_plan(conn: sqlite3.Connection, week: str, plan: dict, source: str, note: str, user_id: int | None,
              got: dict | None = None) -> None:
    """Replace a week's menu and shopping list from a seed file. Call inside a transaction.

    Only `python -m app.cli import-seed` uses this now: the seed JSON has a week-level "leftovers"
    list and items marked for: "both". Leftovers move onto the week's last cook — Friday when it is
    present, otherwise Tuesday — the same rule migration 004 applies, so importing the seed and
    migrating an old database give the same shape.
    """
    sessions = {k: dict(v) for k, v in (plan.get("sessions") or {}).items() if isinstance(v, dict)}
    last = "fri" if "fri" in sessions else "tue"
    for k, sess in sessions.items():
        sess["leftovers"] = list(sess.get("leftovers") or (plan.get("leftovers", []) if k == last else []))
    data = {"sessions": sessions}
    conn.execute("DELETE FROM meal_plans WHERE week = ?", (week,))  # cascades shopping_items
    conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                 (week, json.dumps(data), source, note, now_iso(), user_id))
    got = got or {}
    for n, i in enumerate(plan.get("shopping", [])):
        conn.execute(
            """INSERT INTO shopping_items (week, id, item, buy, aisle, for_session, stock, search, got, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (week, i["id"], i["item"], i.get("buy", ""), i.get("aisle", "Pantry"), i.get("for", "both"),
             1 if i.get("stock") else 0, i.get("search", ""), 1 if got.get(i["id"]) else 0, n),
        )


def save_session(conn: sqlite3.Connection, week: str, session: str, session_data: dict, shopping: list[dict],
                 user_id: int | None) -> None:
    """Merge one prep session into a week. Call inside a transaction.

    The week's other session, and its shopping ticks, are left exactly as they were: only this
    session's meal_plans entry and its shopping_items rows are replaced. An existing row keeps its
    original source, note and created_by — only data and created_at move. Rows marked for "both"
    only exist in weeks planned before the connector; re-planning either session drops them, since
    stale shared items on both lists is worse than the other session briefly missing them.
    """
    row = conn.execute("SELECT data FROM meal_plans WHERE week = ?", (week,)).fetchone()
    if row is None:
        data = {"sessions": {}}
        conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at, created_by) "
                     "VALUES (?, ?, 'Claude', '', ?, ?)", (week, json.dumps(data), now_iso(), user_id))
    else:
        data = json.loads(row["data"])
    data.setdefault("sessions", {})[session] = session_data
    conn.execute("UPDATE meal_plans SET data = ?, created_at = ? WHERE week = ?",
                 (json.dumps(data), now_iso(), week))
    conn.execute("DELETE FROM shopping_items WHERE week = ? AND for_session IN (?, 'both')", (week, session))
    for n, i in enumerate(shopping):
        conn.execute(
            """INSERT INTO shopping_items (week, id, item, buy, aisle, for_session, stock, search, got, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (week, i["id"], i["item"], i["buy"], i["aisle"], session, 1 if i["stock"] else 0, i["search"], n),
        )


def get_plan(conn: sqlite3.Connection, week: str, include_shopping: bool) -> dict | None:
    row = conn.execute("SELECT * FROM meal_plans WHERE week = ?", (week,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    sessions = data.get("sessions") or {}
    for sess in sessions.values():
        # Leftovers belong to the session that produced them (migration 004). A row written before
        # that still reads cleanly.
        if isinstance(sess, dict):
            sess.setdefault("leftovers", [])
    plan = {"week": week, "source": row["source"], "note": row["note"], "createdAt": row["created_at"],
            "sessions": sessions}
    if include_shopping:
        items = conn.execute("SELECT * FROM shopping_items WHERE week = ? ORDER BY sort_order", (week,)).fetchall()
        plan["shopping"] = [{"id": r["id"], "item": r["item"], "buy": r["buy"], "aisle": r["aisle"],
                             "for": r["for_session"], "stock": bool(r["stock"]), "search": r["search"]}
                            for r in items]
        plan["got"] = {r["id"]: True for r in items if r["got"]}
    return plan


def set_recipe_fav(conn: sqlite3.Connection, week: str, session: str, slot: str, fav: bool) -> bool:
    row = conn.execute("SELECT data FROM meal_plans WHERE week = ?", (week,)).fetchone()
    if not row:
        return False
    data = json.loads(row["data"])
    try:
        data["sessions"][session]["recipes"][slot]["fav"] = bool(fav)
    except (KeyError, TypeError):
        return False
    conn.execute("UPDATE meal_plans SET data = ? WHERE week = ?", (json.dumps(data), week))
    return True
