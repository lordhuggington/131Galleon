-- House Run Sheet: initial schema.
-- Applied by app/db.py; PRAGMA user_version tracks the last applied migration number.

CREATE TABLE users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('homeowner', 'housekeeper')),
    password_hash TEXT    NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);

CREATE TABLE sessions (
    token_hash TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL
);
CREATE INDEX ix_sessions_user ON sessions (user_id);

-- Recurring household tasks. Deleting a task sets active = 0 so visit history keeps its meaning.
CREATE TABLE tasks (
    id         TEXT    PRIMARY KEY,
    title      TEXT    NOT NULL,
    area       TEXT    NOT NULL DEFAULT 'Whole house',
    freq       TEXT    NOT NULL DEFAULT 'visit' CHECK (freq IN ('visit', 'weekly', 'fortnightly', 'monthly')),
    day        TEXT    NOT NULL DEFAULT 'any' CHECK (day IN ('any', 'tue', 'fri')),
    notes      TEXT    NOT NULL DEFAULT '',
    link       TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL,
    updated_at TEXT    NOT NULL
);

-- One row per visit day that has a note; completions and one-off jobs hang off the date.
CREATE TABLE visits (
    date       TEXT    PRIMARY KEY,
    note       TEXT    NOT NULL DEFAULT '',
    updated_at TEXT    NOT NULL,
    updated_by INTEGER REFERENCES users (id)
);

CREATE TABLE task_completions (
    visit_date TEXT    NOT NULL,
    task_id    TEXT    NOT NULL REFERENCES tasks (id),
    done_at    TEXT    NOT NULL,
    done_by    INTEGER REFERENCES users (id),
    PRIMARY KEY (visit_date, task_id)
);
CREATE INDEX ix_completions_task ON task_completions (task_id, visit_date);

CREATE TABLE visit_extras (
    id         INTEGER PRIMARY KEY,
    visit_date TEXT    NOT NULL,
    title      TEXT    NOT NULL,
    notes      TEXT    NOT NULL DEFAULT '',
    done_at    TEXT,
    done_by    INTEGER REFERENCES users (id),
    created_at TEXT    NOT NULL,
    created_by INTEGER REFERENCES users (id)
);
CREATE INDEX ix_extras_date ON visit_extras (visit_date);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL  -- JSON
);

-- A week's menu: recipes for both prep sessions plus expected leftovers, stored as JSON.
CREATE TABLE meal_plans (
    week       TEXT    PRIMARY KEY,  -- Monday, YYYY-MM-DD
    data       TEXT    NOT NULL,     -- JSON: {sessions, leftovers}
    source     TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL,
    created_by INTEGER REFERENCES users (id)
);

CREATE TABLE shopping_items (
    week        TEXT    NOT NULL REFERENCES meal_plans (week) ON DELETE CASCADE,
    id          TEXT    NOT NULL,
    item        TEXT    NOT NULL,
    buy         TEXT    NOT NULL DEFAULT '',
    aisle       TEXT    NOT NULL DEFAULT 'Pantry',
    for_session TEXT    NOT NULL DEFAULT 'both' CHECK (for_session IN ('tue', 'fri', 'both')),
    stock       INTEGER NOT NULL DEFAULT 0,
    got         INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (week, id)
);

-- Background menu generation jobs (Claude API calls take about a minute).
CREATE TABLE ai_jobs (
    id             INTEGER PRIMARY KEY,
    week           TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('running', 'cancelling', 'done', 'error', 'cancelled')),
    progress_chars INTEGER NOT NULL DEFAULT 0,
    titles         TEXT    NOT NULL DEFAULT '[]',
    error          TEXT,
    created_at     TEXT    NOT NULL,
    finished_at    TEXT,
    created_by     INTEGER REFERENCES users (id)
);
