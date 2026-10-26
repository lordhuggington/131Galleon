"""Migration runner and schema migration tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def apply_001(conn) -> None:
    """Apply only migration 001, leaving user_version at 1."""
    from app.db import MIGRATIONS_DIR
    sql = (MIGRATIONS_DIR / "001_initial.sql").read_text()
    conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = 1;\nCOMMIT;")


def apply_up_to(conn, last: int) -> None:
    """Apply migrations 001..last and stop, so a test can write pre-migration rows.

    Foreign keys go off the way app.db.migrate does it: 002 rebuilds users with a DROP + RENAME.
    """
    from app.db import MIGRATIONS_DIR
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for f in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            num = int(f.name[:3])
            if num > last:
                break
            conn.executescript(f"BEGIN;\n{f.read_text()}\nPRAGMA user_version = {num};\nCOMMIT;")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def migrate_to(conn, last: int) -> int:
    """Run app.db.migrate as far as migration `last` and no further, so a test can check that one
    step through the real runner (foreign keys off, then PRAGMA foreign_key_check) before later
    migrations drop the tables it is about."""
    import shutil
    import app.db as db
    with tempfile.TemporaryDirectory() as d:
        for f in sorted(db.MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            if int(f.name[:3]) <= last:
                shutil.copy(f, d)
        with mock.patch.object(db, "MIGRATIONS_DIR", Path(d)):
            return db.migrate(conn)


class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "m.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_runner_checks_foreign_keys_and_restores_the_pragma(self):
        from app.db import connect, migrate
        conn = connect(self.path)
        try:
            version = migrate(conn)
            self.assertGreaterEqual(version, 1)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], version)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            # migrate() must leave enforcement switched back on for the caller.
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        finally:
            conn.close()

    def test_002_maps_roles_and_keeps_sessions(self):
        from app.db import connect
        conn = connect(self.path)
        try:
            apply_001(conn)
            for uid, name, display, role in ((1, "owen", "Owen", "homeowner"), (2, "maria", "Maria", "housekeeper")):
                conn.execute("INSERT INTO users (id, username, display_name, role, password_hash, active, created_at) "
                             "VALUES (?, ?, ?, ?, 'hash', 1, '2026-01-01T00:00:00.000000Z')", (uid, name, display, role))
            conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                         "VALUES ('abc', 2, '2026-01-01T00:00:00.000000Z', '2099-01-01T00:00:00.000000Z')")
            conn.execute("INSERT INTO tasks (id, title, created_at, updated_at) VALUES ('t01', 'Wipe', 'x', 'x')")
            conn.execute("INSERT INTO task_completions (visit_date, task_id, done_at, done_by) "
                         "VALUES ('2026-09-29', 't01', 'x', 2)")

            self.assertEqual(migrate_to(conn, 2), 2)

            rows = {r["username"]: r for r in conn.execute("SELECT * FROM users ORDER BY id")}
            self.assertEqual(rows["owen"]["role"], "owner")
            self.assertEqual(rows["owen"]["label"], "")
            self.assertEqual(rows["maria"]["role"], "staff")
            self.assertEqual(rows["maria"]["label"], "Housekeeper")
            self.assertEqual(rows["maria"]["can_see_meals"], 1)
            self.assertIsNone(rows["maria"]["phone"])
            self.assertIsNone(rows["maria"]["door_code"])
            self.assertEqual(rows["maria"]["password_hash"], "hash")
            # the DROP + RENAME must not have cascaded away rows that point at users
            self.assertEqual(conn.execute("SELECT user_id FROM sessions WHERE token_hash = 'abc'").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT done_by FROM task_completions").fetchone()[0], 2)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM visit_photos").fetchone()[0], 0)
        finally:
            conn.close()

    def test_003_gives_older_shopping_rows_an_empty_search_phrase(self):
        from app.db import connect
        conn = connect(self.path)
        try:
            apply_001(conn)
            conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES ('2026-09-28', '{}', 'x')")
            conn.execute("INSERT INTO shopping_items (week, id, item, buy) VALUES ('2026-09-28', 's01', 'Oats', '1 bag')")

            self.assertEqual(migrate_to(conn, 3), 3)

            row = conn.execute("SELECT * FROM shopping_items WHERE week = '2026-09-28' AND id = 's01'").fetchone()
            self.assertEqual(row["item"], "Oats")
            self.assertEqual(row["search"], "")  # rows written before the column still read cleanly
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_004_moves_leftovers_onto_the_session_that_made_them(self):
        from app.db import connect
        conn = connect(self.path)
        try:
            apply_up_to(conn, 3)
            both = {"sessions": {"tue": {"date": "2026-09-29", "recipes": {}},
                                 "fri": {"date": "2026-10-02", "recipes": {}}},
                    "leftovers": ["About 170 g Greek yogurt", "2 eggs"]}
            tue_only = {"sessions": {"tue": {"date": "2026-10-06", "recipes": {}}},
                        "leftovers": ["About 80 g Parmesan"]}
            fri_only = {"sessions": {"fri": {"date": "2026-10-16", "recipes": {}}}}
            for week, data in (("2026-09-28", both), ("2026-10-05", tue_only), ("2026-10-12", fri_only)):
                conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES (?, ?, 'x')",
                             (week, json.dumps(data)))

            self.assertEqual(migrate_to(conn, 4), 4)

            rows = {r["week"]: json.loads(r["data"]) for r in conn.execute("SELECT week, data FROM meal_plans")}
            # Friday is the week's last cook, so a week with both sessions hands its leftovers to Friday.
            self.assertEqual(rows["2026-09-28"]["sessions"]["fri"]["leftovers"],
                             ["About 170 g Greek yogurt", "2 eggs"])
            self.assertEqual(rows["2026-09-28"]["sessions"]["tue"]["leftovers"], [])
            self.assertEqual(rows["2026-10-05"]["sessions"]["tue"]["leftovers"], ["About 80 g Parmesan"])
            self.assertEqual(rows["2026-10-12"]["sessions"]["fri"]["leftovers"], [])
            for week, data in rows.items():
                self.assertNotIn("leftovers", data, week)  # the week-level list is gone
                for name, sess in data["sessions"].items():
                    self.assertIsInstance(sess["leftovers"], list, f"{week} {name}")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_004_drops_ai_jobs_and_adds_the_oauth_tables(self):
        from app.db import connect
        conn = connect(self.path)
        try:
            apply_up_to(conn, 3)
            conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES (?, ?, 'x')",
                         ("2026-09-28", '{"sessions": {}}'))
            conn.execute("INSERT INTO shopping_items (week, id, item, buy, for_session) "
                         "VALUES ('2026-09-28', 's01', 'Oats', '1 bag', 'both')")
            conn.execute("INSERT INTO ai_jobs (week, status, created_at) VALUES ('2026-09-28', 'done', 'x')")

            self.assertEqual(migrate_to(conn, 4), 4)

            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("ai_jobs", names)
            for table in ("oauth_clients", "oauth_codes", "oauth_tokens"):
                self.assertIn(table, names)
            for index in ("ix_oauth_tokens_family", "ix_oauth_tokens_user"):
                self.assertIn(index, names)
            # A pre-004 row with for_session = 'both' is still legal and still readable.
            row = conn.execute("SELECT * FROM shopping_items WHERE week = '2026-09-28' AND id = 's01'").fetchone()
            self.assertEqual(row["for_session"], "both")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_005_removes_meal_prep_and_adds_kinds_jobs_and_hours(self):
        from app.db import connect, migrate
        conn = connect(self.path)
        try:
            apply_up_to(conn, 4)
            for uid, name, role, label in ((1, "owen", "owner", ""), (2, "maria", "staff", "Housekeeper"),
                                           (3, "ondrej", "staff", "builder")):
                conn.execute("INSERT INTO users (id, username, display_name, role, label, can_see_meals, created_at) "
                             "VALUES (?, ?, ?, ?, ?, 0, 'x')", (uid, name, name.title(), role, label))
            conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) "
                         "VALUES ('abc', 3, 'x', '2099-01-01T00:00:00.000000Z')")
            conn.execute("INSERT INTO settings (key, value) VALUES ('meal', '{}')")
            conn.execute("INSERT INTO meal_plans (week, data, created_at, created_by) VALUES ('2026-09-28', '{}', 'x', 1)")
            conn.execute("INSERT INTO shopping_items (week, id, item) VALUES ('2026-09-28', 's01', 'Oats')")
            conn.execute("INSERT INTO oauth_clients (client_id, redirect_uris, created_at) VALUES ('c1', '[]', 'x')")
            conn.execute("INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, code_challenge, "
                         "expires_at, created_at) VALUES ('h', 'c1', 1, 'https://claude.ai/cb', 'ch', 0, 'x')")
            conn.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, expires_at, created_at) "
                         "VALUES ('t', 'refresh', 'c1', 1, 'fam', 0, 'x')")
            for tid, area, link, notes in (("t01", "Meal prep", "meals", "On the Meals tab."),
                                           ("t02", "Meal prep", None, ""),
                                           ("t11", "Kitchen", None, "Do it before the meal prep goes in.")):
                conn.execute("INSERT INTO tasks (id, title, area, link, notes, created_at, updated_at) "
                             "VALUES (?, 'x', ?, ?, ?, 'x', 'x')", (tid, area, link, notes))
            conn.execute("INSERT INTO task_completions (visit_date, task_id, done_at, done_by) "
                         "VALUES ('2026-09-29', 't01', 'x', 2)")

            self.assertEqual(migrate_to(conn, 5), 5)

            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for gone in ("settings", "meal_plans", "shopping_items", "oauth_clients", "oauth_codes", "oauth_tokens"):
                self.assertNotIn(gone, names)
            for added in ("jobs", "job_comments", "job_photos", "hours_claims"):
                self.assertIn(added, names)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
            self.assertNotIn("can_see_meals", cols)
            kinds = {r["username"]: r["kind"] for r in conn.execute("SELECT username, kind FROM users")}
            self.assertEqual(kinds, {"owen": "housekeeper", "maria": "housekeeper", "ondrej": "builder"})
            tasks = {r["id"]: r for r in conn.execute("SELECT * FROM tasks")}
            self.assertEqual(tasks["t01"]["active"], 0)  # the meals task goes, its history stays
            self.assertEqual(tasks["t02"]["area"], "Kitchen")
            self.assertEqual(tasks["t11"]["notes"], "")
            self.assertTrue(all(t["link"] is None for t in tasks.values()))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_completions").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT user_id FROM sessions").fetchone()[0], 3)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE users SET kind = 'plumber' WHERE id = 2")
            with self.assertRaises(sqlite3.IntegrityError):  # quarter hours only
                conn.execute("INSERT INTO hours_claims (user_id, work_date, minutes, created_at, updated_at) "
                             "VALUES (2, '2026-10-20', 20, 'x', 'x')")
        finally:
            conn.close()

    def test_006_unpins_tasks_from_tuesday_and_friday(self):
        from app.db import connect
        conn = connect(self.path)
        try:
            apply_up_to(conn, 5)
            for tid, day in (("t01", "tue"), ("t02", "fri"), ("t03", "any")):
                conn.execute("INSERT INTO tasks (id, title, day, created_at, updated_at) VALUES (?, 'x', ?, 'x', 'x')",
                             (tid, day))
            conn.execute("INSERT INTO task_completions (visit_date, task_id, done_at) VALUES ('2026-09-29', 't01', 'x')")

            self.assertEqual(migrate_to(conn, 6), 6)

            self.assertEqual({r["id"]: r["day"] for r in conn.execute("SELECT id, day FROM tasks")},
                             {"t01": "any", "t02": "any", "t03": "any"})
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM task_completions").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_a_failing_migration_rolls_back_and_restores_the_pragma(self):
        """A script that dies part-way leaves its BEGIN open; migrate() must not hand that back."""
        import app.db as db
        scripts = Path(self.tmp.name) / "migrations"
        scripts.mkdir()
        (scripts / "001_ok.sql").write_text("CREATE TABLE ok (id INTEGER PRIMARY KEY);\n")
        (scripts / "002_broken.sql").write_text(
            "CREATE TABLE x (id INTEGER PRIMARY KEY);\nINSERT INTO nope VALUES (1);\n")
        conn = db.connect(self.path)
        try:
            with mock.patch.object(db, "MIGRATIONS_DIR", scripts):
                with self.assertRaises(sqlite3.OperationalError):
                    db.migrate(conn)
            # no open write transaction, enforcement back on, and 002 did not half-apply
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            self.assertIn("ok", tables)
            self.assertNotIn("x", tables)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
