"""Data access for tasks, visits, jobs and hours claims."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from .db import now_iso


# ---------- tasks ----------
def task_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "title": r["title"], "area": r["area"], "freq": r["freq"], "day": r["day"],
         "notes": r["notes"], "order": r["sort_order"], "active": bool(r["active"])}


def list_tasks(conn: sqlite3.Connection) -> list[dict]:
    return [task_dict(r) for r in conn.execute("SELECT * FROM tasks WHERE active = 1 ORDER BY sort_order, id")]


def upsert_task(conn: sqlite3.Connection, tid: str, t: dict) -> None:
    now = now_iso()
    conn.execute(
        """INSERT INTO tasks (id, title, area, freq, day, notes, active, sort_order, created_at, updated_at)
           VALUES (:id, :title, :area, :freq, :day, :notes, :active, :sort_order, :now, :now)
           ON CONFLICT(id) DO UPDATE SET title=excluded.title, area=excluded.area, freq=excluded.freq, day=excluded.day,
             notes=excluded.notes, active=excluded.active, sort_order=excluded.sort_order, updated_at=excluded.updated_at""",
        {"id": tid, "title": t["title"], "area": t.get("area") or "Whole house", "freq": t.get("freq") or "visit",
         "day": t.get("day") or "any", "notes": t.get("notes") or "",
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


# ---------- jobs ----------
def _person(uid: int | None, display_name: str | None) -> dict | None:
    return None if uid is None else {"id": uid, "displayName": display_name or ""}


def _author(r: sqlite3.Row) -> dict:
    """Who wrote a comment or added a photo, with their role so the page can mark the owners' ones."""
    return {"id": r["created_by"], "displayName": r["display_name"] or "", "role": r["author_role"]}


def comment_dict(r: sqlite3.Row) -> dict:
    """r is a job_comments row joined to the author's display_name and role (as author_role)."""
    return {"id": r["id"], "body": r["body"], "createdAt": r["created_at"], "by": _author(r)}


def job_photo_dict(r: sqlite3.Row) -> dict:
    """r is a job_photos row joined to the uploader's display_name and role (as author_role)."""
    return {"id": r["id"], "caption": r["caption"], "url": f"/photos/{r['filename']}", "createdAt": r["created_at"],
            "by": _author(r)}


def list_jobs(conn: sqlite3.Connection, job_id: int | None = None) -> list[dict]:
    """Active jobs in list order, each with its comments and photos oldest first. job_id narrows it to one."""
    only = "AND j.id = :id" if job_id is not None else ""
    params = {"id": job_id}
    jobs: dict[int, dict] = {}
    for r in conn.execute(f"""SELECT j.*, u.display_name AS done_by_name FROM jobs j
                              LEFT JOIN users u ON u.id = j.done_by
                              WHERE j.active = 1 {only} ORDER BY j.sort_order, j.id""", params):
        jobs[r["id"]] = {"id": r["id"], "title": r["title"], "details": r["details"], "link": r["link"],
                         "status": r["status"], "sortOrder": r["sort_order"], "createdAt": r["created_at"],
                         "updatedAt": r["updated_at"], "doneAt": r["done_at"],
                         "doneBy": _person(r["done_by"], r["done_by_name"]), "comments": [], "photos": []}
    if not jobs:
        return []
    for r in conn.execute(f"""SELECT c.*, u.display_name, u.role AS author_role FROM job_comments c
                              JOIN jobs j ON j.id = c.job_id
                              LEFT JOIN users u ON u.id = c.created_by
                              WHERE j.active = 1 {only} ORDER BY c.created_at, c.id""", params):
        jobs[r["job_id"]]["comments"].append(comment_dict(r))
    for r in conn.execute(f"""SELECT p.*, u.display_name, u.role AS author_role FROM job_photos p
                              JOIN jobs j ON j.id = p.job_id
                              LEFT JOIN users u ON u.id = p.created_by
                              WHERE j.active = 1 {only} ORDER BY p.created_at, p.id""", params):
        jobs[r["job_id"]]["photos"].append(job_photo_dict(r))
    return list(jobs.values())


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    found = list_jobs(conn, job_id)
    return found[0] if found else None


def add_job(conn: sqlite3.Connection, title: str, details: str, link: str, user_id: int | None) -> int:
    """A new job goes to the end of the list. Returns its id."""
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM jobs").fetchone()[0]
    now = now_iso()
    cur = conn.execute("""INSERT INTO jobs (title, details, link, sort_order, created_at, created_by, updated_at)
                          VALUES (?, ?, ?, ?, ?, ?, ?)""", (title, details, link, order, now, user_id, now))
    return cur.lastrowid


# ---------- hours claims ----------
_CLAIM_SELECT = """SELECT h.*, u.display_name AS by_name, a.display_name AS approved_by_name,
                          p.display_name AS paid_by_name
                     FROM hours_claims h
                     JOIN users u ON u.id = h.user_id
                     LEFT JOIN users a ON a.id = h.approved_by
                     LEFT JOIN users p ON p.id = h.paid_by"""


def claim_dict(r: sqlite3.Row) -> dict:
    return {"id": r["id"], "date": r["work_date"], "minutes": r["minutes"], "note": r["note"], "status": r["status"],
            "createdAt": r["created_at"], "by": _person(r["user_id"], r["by_name"]),
            "approvedAt": r["approved_at"], "approvedBy": _person(r["approved_by"], r["approved_by_name"]),
            "paidAt": r["paid_at"], "paidBy": _person(r["paid_by"], r["paid_by_name"])}


def list_claims(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    """Newest work day first. user_id=None means everyone's (owners); otherwise only that person's."""
    if user_id is None:
        rows = conn.execute(f"{_CLAIM_SELECT} ORDER BY h.work_date DESC, h.id DESC")
    else:
        rows = conn.execute(f"{_CLAIM_SELECT} WHERE h.user_id = ? ORDER BY h.work_date DESC, h.id DESC", (user_id,))
    return [claim_dict(r) for r in rows]


def get_claim(conn: sqlite3.Connection, claim_id: int) -> dict:
    return claim_dict(conn.execute(f"{_CLAIM_SELECT} WHERE h.id = ?", (claim_id,)).fetchone())
