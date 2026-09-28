-- v4: the app becomes its own OAuth authorization server for the Claude connector, in-app
-- menu generation is gone, and a week's expected leftovers move onto the session that made them.

CREATE TABLE oauth_clients (
    client_id     TEXT NOT NULL PRIMARY KEY,
    client_name   TEXT NOT NULL DEFAULT '',
    redirect_uris TEXT NOT NULL,            -- JSON array
    created_at    TEXT NOT NULL
);

CREATE TABLE oauth_codes (
    code_hash      TEXT    NOT NULL PRIMARY KEY,   -- SHA-256 hex of the code
    client_id      TEXT    NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    redirect_uri   TEXT    NOT NULL,
    code_challenge TEXT    NOT NULL,
    scope          TEXT    NOT NULL DEFAULT '',
    resource       TEXT    NOT NULL DEFAULT '',
    expires_at     INTEGER NOT NULL,              -- unix seconds
    created_at     TEXT    NOT NULL
);

CREATE TABLE oauth_tokens (
    token_hash   TEXT    NOT NULL PRIMARY KEY,    -- SHA-256 hex of the token
    kind         TEXT    NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id    TEXT    NOT NULL REFERENCES oauth_clients (client_id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    family       TEXT    NOT NULL,                -- one grant chain; rotation keeps the family
    scope        TEXT    NOT NULL DEFAULT '',
    expires_at   INTEGER NOT NULL,                -- unix seconds
    created_at   TEXT    NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX ix_oauth_tokens_family ON oauth_tokens (family);
CREATE INDEX ix_oauth_tokens_user   ON oauth_tokens (user_id);

-- Menus are written in the Claude app now; there is no server-side job.
DROP TABLE ai_jobs;

-- Leftovers were a week-level list; they belong to the session that produced them. Friday's
-- session gets them when it exists (it is the week's last cook), otherwise Tuesday's.
UPDATE meal_plans
   SET data = json_set(data, '$.sessions.fri.leftovers', json(json_extract(data, '$.leftovers')))
 WHERE json_type(data, '$.leftovers') = 'array'
   AND json_type(data, '$.sessions.fri') = 'object'
   AND json_type(data, '$.sessions.fri.leftovers') IS NULL;

UPDATE meal_plans
   SET data = json_set(data, '$.sessions.tue.leftovers', json(json_extract(data, '$.leftovers')))
 WHERE json_type(data, '$.leftovers') = 'array'
   AND json_type(data, '$.sessions.fri') IS NULL
   AND json_type(data, '$.sessions.tue') = 'object'
   AND json_type(data, '$.sessions.tue.leftovers') IS NULL;

-- Every session ends up with a list, so the app never has to guess.
UPDATE meal_plans
   SET data = json_set(data, '$.sessions.tue.leftovers', json('[]'))
 WHERE json_type(data, '$.sessions.tue') = 'object'
   AND json_type(data, '$.sessions.tue.leftovers') IS NULL;

UPDATE meal_plans
   SET data = json_set(data, '$.sessions.fri.leftovers', json('[]'))
 WHERE json_type(data, '$.sessions.fri') = 'object'
   AND json_type(data, '$.sessions.fri.leftovers') IS NULL;

UPDATE meal_plans
   SET data = json_remove(data, '$.leftovers')
 WHERE json_type(data, '$.leftovers') IS NOT NULL;
