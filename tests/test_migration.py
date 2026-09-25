"""Migration runner and schema migration tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
