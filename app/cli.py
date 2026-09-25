"""Admin commands.

    python -m app.cli migrate
    python -m app.cli create-user --username owen --name "Owen" --role homeowner
    python -m app.cli set-password --username owen
    python -m app.cli import-seed seed/
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path

from . import store
from .auth import ROLES, hash_password, password_problem
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


def cmd_migrate(args, conn) -> None:
    print(f"Schema version {migrate(conn)}")


def cmd_create_user(args, conn) -> None:
    migrate(conn)
    username = args.username.strip().lower()
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        sys.exit(f"User '{username}' already exists. Use set-password to change the password.")
    pw = args.password or _ask_password()
    if problem := password_problem(pw):
        sys.exit(problem)
    conn.execute("INSERT INTO users (username, display_name, role, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                 (username, args.name or username, args.role, hash_password(pw), now_iso()))
    print(f"Created {args.role} '{username}'.")


def cmd_set_password(args, conn) -> None:
    migrate(conn)
    username = args.username.strip().lower()
    if not conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        sys.exit(f"No user '{username}'.")
    pw = args.password or _ask_password()
    if problem := password_problem(pw):
        sys.exit(problem)
    with tx(conn):
        conn.execute("UPDATE users SET password_hash = ?, active = 1 WHERE username = ?", (hash_password(pw), username))
        conn.execute("DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE username = ?)", (username,))
    print(f"Password updated for '{username}'; their other sessions were signed out.")


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
    cu = sub.add_parser("create-user", help="add a homeowner or housekeeper login")
    cu.add_argument("--username", required=True)
    cu.add_argument("--name", help="display name")
    cu.add_argument("--role", choices=ROLES, required=True)
    cu.add_argument("--password", help="omit to be prompted")
    sp = sub.add_parser("set-password", help="reset someone's password")
    sp.add_argument("--username", required=True)
    sp.add_argument("--password", help="omit to be prompted")
    im = sub.add_parser("import-seed", help="load tasks, settings and meal plans from a folder")
    im.add_argument("path")
    args = ap.parse_args(argv)
    conn = connect()
    try:
        {"migrate": cmd_migrate, "create-user": cmd_create_user, "set-password": cmd_set_password,
         "import-seed": cmd_import_seed}[args.cmd](args, conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
