"""SQLite access: one short-lived connection per request, plus numbered SQL migrations."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .config import get_config

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or get_config().db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # autocommit; use tx() for transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def migrate(conn: sqlite3.Connection) -> int:
    """Apply migrations/NNN_*.sql files newer than PRAGMA user_version. Returns the new version."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    for f in files:
        num = int(f.name[:3])
        if num <= current:
            continue
        sql = f.read_text()
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {num};\nCOMMIT;")
        current = num
    return current
