-- v5: meal prep is gone (menus, shopping, the Claude connector and its OAuth tables), staff get a
-- kind (housekeeper or builder), and builders get a job list while housekeepers claim their hours.

-- Children before parents: shopping_items points at meal_plans, the OAuth codes and tokens at clients.
DROP TABLE shopping_items;
DROP TABLE meal_plans;
DROP TABLE oauth_tokens;
DROP TABLE oauth_codes;
DROP TABLE oauth_clients;
-- The meal settings were the only thing the settings table ever held.
DROP TABLE settings;

-- The "Cook this visit's meal prep" task jumped to the Meals tab; it goes (active = 0 keeps the visit
-- history it ticked). The other Meal prep tasks are kitchen jobs now. Mirrors seed/tasks.json.
UPDATE tasks SET active = 0 WHERE link = 'meals';
UPDATE tasks SET area = 'Kitchen' WHERE area = 'Meal prep' AND active = 1;
UPDATE tasks SET notes = '' WHERE notes = 'Do it before the meal prep goes in.';
UPDATE tasks SET link = NULL;

-- kind only means something for staff; owners see everything whatever it says.
ALTER TABLE users ADD COLUMN kind TEXT NOT NULL DEFAULT 'housekeeper' CHECK (kind IN ('housekeeper', 'builder'));
UPDATE users SET kind = 'builder' WHERE lower(label) = 'builder';
ALTER TABLE users DROP COLUMN can_see_meals;

-- The builder's work package. Deleting a job sets active = 0.
CREATE TABLE jobs (
    id         INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    details    TEXT    NOT NULL DEFAULT '',
    link       TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT    NOT NULL,
    created_by INTEGER REFERENCES users (id),
    updated_at TEXT    NOT NULL,
    done_at    TEXT,
    done_by    INTEGER REFERENCES users (id)
);

CREATE TABLE job_comments (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    body       TEXT    NOT NULL,
    created_at TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id)
);
CREATE INDEX ix_job_comments_job ON job_comments (job_id);

-- Files live in HRS_PHOTOS_DIR next to the visit photos, under the same random names.
CREATE TABLE job_photos (
    id         INTEGER PRIMARY KEY,
    job_id     INTEGER NOT NULL REFERENCES jobs (id) ON DELETE CASCADE,
    caption    TEXT    NOT NULL DEFAULT '',
    filename   TEXT    NOT NULL UNIQUE,
    bytes      INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id)
);
CREATE INDEX ix_job_photos_job ON job_photos (job_id);

-- A housekeeper's hours, one row per claim; owners approve them and then mark them paid.
CREATE TABLE hours_claims (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id),
    work_date   TEXT    NOT NULL,                    -- YYYY-MM-DD
    minutes     INTEGER NOT NULL CHECK (minutes BETWEEN 15 AND 960 AND minutes % 15 = 0),
    note        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'paid')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    approved_at TEXT,
    approved_by INTEGER REFERENCES users (id),
    paid_at     TEXT,
    paid_by     INTEGER REFERENCES users (id)
);
CREATE INDEX ix_hours_user_date ON hours_claims (user_id, work_date);
