"""Admin commands.

    python -m app.cli migrate
    python -m app.cli create-user --username owen --name "Owen" --role owner
    python -m app.cli create-user --username maria --name "Maria" --role staff --label Housekeeper
    python -m app.cli set-password --username owen          # owners, or staff with no phone on file
    python -m app.cli set-phone --username maria --phone "(310) 555-1234"
    python -m app.cli set-phone --username maria --clear    # back to a password if Twilio is down
    python -m app.cli import-seed seed/
"""
from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
import sys
from pathlib import Path

from . import store
from .api import DOOR_CODE_RE, ApiError
from .auth import ROLES, hash_password, normalize_phone, password_problem
from .db import connect, migrate, now_iso, tx


def _ask_password() -> str:
    while True:
        pw = getpass.getpass("Password: ")
        if problem := password_problem(pw):
            print(problem)
            continue
        if getpass.getpass("Repeat: ") != pw:
            print("Passwords don't match.")
            continue
        return pw


def _normalize(phone: str) -> str:
    try:
        return normalize_phone(phone)
    except ApiError as e:
        sys.exit(e.message)


def _checked_hash(password: str) -> str:
    if problem := password_problem(password):
        sys.exit(problem)
    return hash_password(password)


def cmd_migrate(args, conn) -> None:
    print(f"Schema version {migrate(conn)}")


def cmd_create_user(args, conn) -> None:
    migrate(conn)
    username = args.username.strip().lower()
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        sys.exit(f"User '{username}' already exists. Use set-password to change the password.")
    phone = _normalize(args.phone) if args.phone else None
    door_code = (args.door_code or "").strip() or None
    if door_code and not DOOR_CODE_RE.match(door_code):
        sys.exit("A door code is 4 to 8 digits.")
    pw_hash = None
    if args.role == "owner":
        pw_hash = _checked_hash(args.password or _ask_password())  # owners keep a password as an emergency fallback
    elif args.password and not phone:
        # Transitional: staff who haven't got a phone yet. Once they have one, login() refuses a
        # password from them, so a hash kept here could never be used again.
        pw_hash = _checked_hash(args.password)
    try:
        conn.execute(
            """INSERT INTO users (username, display_name, role, label, phone, door_code, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (username, args.name or username, args.role, args.label or "", phone, door_code, pw_hash, now_iso()))
    except sqlite3.IntegrityError:
        sys.exit("That phone number is already used by another person.")
    print(f"Created {args.role} '{username}'.")


def cmd_set_password(args, conn) -> None:
    migrate(conn)
    username = args.username.strip().lower()
    row = conn.execute("SELECT role, phone FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        sys.exit(f"No user '{username}'.")
    if row["role"] != "owner" and row["phone"]:
        # login() never accepts a password from staff who have a phone, so setting one here would leave
        # an unusable credential behind. Clearing the number first is the break-glass for a Twilio
        # outage: it is a local admin command on the server, not something the API exposes.
        sys.exit(f"'{username}' signs in with a code texted to their phone. Clear their number with "
                 f"`set-phone --clear` first if they need a password.")
    pw_hash = _checked_hash(args.password or _ask_password())
    with tx(conn):
        conn.execute("UPDATE users SET password_hash = ?, active = 1 WHERE username = ?", (pw_hash, username))
        conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))
    print(f"Password updated for '{username}'; their other sessions were signed out.")


def cmd_set_phone(args, conn) -> None:
    migrate(conn)
    username = args.username.strip().lower()
    row = conn.execute("SELECT id, role FROM users WHERE username = ?", (username,)).fetchone()
    if not row:
        sys.exit(f"No user '{username}'.")
    if args.clear:
        # The other half of the break-glass: with no number on file, set-password works again. Any
        # password_hash is left as it is (for staff it is already NULL).
        with tx(conn):
            conn.execute("UPDATE users SET phone = NULL WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
        print(f"'{username}' no longer has a phone on file; they were signed out everywhere.")
        return
    phone = _normalize(args.phone)
    try:
        with tx(conn):
            conn.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, row["id"]))
            if row["role"] != "owner":
                # They sign in by text from now on, so the password login() will no longer accept goes.
                conn.execute("UPDATE users SET password_hash = NULL WHERE id = ?", (row["id"],))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    except sqlite3.IntegrityError:
        sys.exit("That phone number is already used by another person.")
    print(f"'{username}' will get sign-in codes by text; they were signed out everywhere.")


def cmd_import_seed(args, conn) -> None:
    """Load tasks.json, settings.json and plans/*.json (the format exported from the Claude-hosted version)."""
    migrate(conn)
    root = Path(args.path)
    with tx(conn):
        tasks_file = root / "tasks.json"
        if tasks_file.exists():
            tasks = json.loads(tasks_file.read_text())
            for tid, t in tasks.items():
                store.upsert_task(conn, tid, t)
            print(f"Tasks: {len(tasks)}")
        settings_file = root / "settings.json"
        if settings_file.exists():
            store.save_settings(conn, json.loads(settings_file.read_text()))
            print("Settings: imported")
        plans = sorted((root / "plans").glob("*.json")) if (root / "plans").is_dir() else []
        for p in plans:
            d = json.loads(p.read_text())
            week = d.get("week") or p.stem
            store.save_plan(conn, week, d, source=d.get("source", "import"), note=d.get("note", ""), user_id=None,
                            got=d.get("got") or {})
        print(f"Meal plans: {len(plans)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="create or upgrade the database")
    cu = sub.add_parser("create-user", help="add an owner or staff login")
    cu.add_argument("--username", required=True)
    cu.add_argument("--name", help="display name")
    cu.add_argument("--role", choices=ROLES, required=True)
    cu.add_argument("--label", help='what they do, e.g. "Housekeeper", "Builder"')
    cu.add_argument("--phone", help="mobile number for text-message sign-in")
    cu.add_argument("--door-code", help="4 to 8 digits")
    cu.add_argument("--password", help="owners, or staff with no phone; omit to be prompted (owners only)")
    sp = sub.add_parser("set-password", help="reset a password (owners, or staff with no phone on file)")
    sp.add_argument("--username", required=True)
    sp.add_argument("--password", help="omit to be prompted")
    ph = sub.add_parser("set-phone", help="set or clear someone's mobile number for text-message sign-in")
    ph.add_argument("--username", required=True)
    number = ph.add_mutually_exclusive_group(required=True)
    number.add_argument("--phone", help="mobile number, e.g. \"(310) 555-1234\"")
    number.add_argument("--clear", action="store_true",
                        help="remove their number and sign them out everywhere")
    im = sub.add_parser("import-seed", help="load tasks, settings and meal plans from a folder")
    im.add_argument("path")
    args = ap.parse_args(argv)
    conn = connect()
    try:
        {"migrate": cmd_migrate, "create-user": cmd_create_user, "set-password": cmd_set_password,
         "set-phone": cmd_set_phone, "import-seed": cmd_import_seed}[args.cmd](args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
