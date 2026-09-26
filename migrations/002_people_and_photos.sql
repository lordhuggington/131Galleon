-- v2: rebuild users for the owner/staff people model, and add visit photos.
-- SQLite can't alter a CHECK constraint, so users is rebuilt with the documented
-- 12-step procedure. app/db.py runs every migration with PRAGMA foreign_keys = OFF and
-- then checks PRAGMA foreign_key_check, so the DROP below does not cascade-delete sessions
-- or null out done_by / created_by columns. The other tables' "REFERENCES users (id)"
-- clauses are stored as text, so after the drop + rename they resolve to the rebuilt table.

CREATE TABLE users_new (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('owner', 'staff')),
    label         TEXT    NOT NULL DEFAULT '',
    phone         TEXT    UNIQUE,
    door_code     TEXT,
    can_see_meals INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

INSERT INTO users_new (id, username, display_name, role, label, password_hash, active, created_at)
    SELECT id, username, display_name,
           CASE role WHEN 'homeowner' THEN 'owner' ELSE 'staff' END,
           CASE role WHEN 'homeowner' THEN '' ELSE 'Housekeeper' END,
           password_hash, active, created_at
    FROM users;

DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

CREATE TABLE visit_photos (
    id         INTEGER PRIMARY KEY,
    visit_date TEXT    NOT NULL,
    kind       TEXT    NOT NULL CHECK (kind IN ('done', 'fix')),
    caption    TEXT    NOT NULL DEFAULT '',
    filename   TEXT    NOT NULL UNIQUE,
    bytes      INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id)
);
CREATE INDEX ix_photos_date ON visit_photos (visit_date);
