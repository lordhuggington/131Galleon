"""Migration runner and schema migration tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

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
        from app.db import connect, migrate
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

            self.assertGreaterEqual(migrate(conn), 2)

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
        from app.db import connect, migrate
        conn = connect(self.path)
        try:
            apply_001(conn)
            conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES ('2026-09-28', '{}', 'x')")
            conn.execute("INSERT INTO shopping_items (week, id, item, buy) VALUES ('2026-09-28', 's01', 'Oats', '1 bag')")

            self.assertGreaterEqual(migrate(conn), 3)

            row = conn.execute("SELECT * FROM shopping_items WHERE week = '2026-09-28' AND id = 's01'").fetchone()
            self.assertEqual(row["item"], "Oats")
            self.assertEqual(row["search"], "")  # rows written before the column still read cleanly
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
