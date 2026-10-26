# Builder jobs, hours claims, meal prep removed — design

Date: 2026-10-26. Branch: `builder-jobs`. Owen's request: remove the meal prep stuff, add a builder
profile whose jobs owners can comment on and add to from the front end, load Ondrej's work package
(`seed/jobs.json`), add an hours claim for the housekeeper, and add a second homeowner (Toni Gray —
added on the live server with the CLI after deploy; **their number never goes in the repo, which is public**).

Defaults chosen for Owen (he can revise): everything meal-related goes including the Claude connector;
the builder sets status, comments and adds photos, owners add/edit/delete jobs; hours are logged per
day by the housekeeper and owners approve, then mark paid.

## 1. People: role + kind

- Roles stay exactly `owner` and `staff` (AGENTS.md).
- New column `users.kind TEXT NOT NULL DEFAULT 'housekeeper' CHECK (kind IN ('housekeeper','builder'))`.
  It only means something for staff; owners see everything. Migration sets `kind = 'builder'` where
  `lower(label) = 'builder'`.
- `can_see_meals` is dropped (`ALTER TABLE users DROP COLUMN can_see_meals`).
- `me_dict` and `user_dict`: remove `canSeeMeals`, add `"kind"`. `create_user` / `update_user` accept
  `kind` (`choice(..., ("housekeeper","builder"))`, default `housekeeper`). CLI `create-user` gets
  `--kind` (default housekeeper).
- Who sees what (server-enforced):

  | | owner | staff housekeeper | staff builder |
  |---|---|---|---|
  | Visit run sheet (tasks, visits, notes, extras, visit photos) | yes | yes | **no** |
  | Jobs (list, comment, status, job photos) | yes, plus add/edit/delete jobs | **no** | yes |
  | Hours | all claims; approve / mark paid / delete | own claims; add, edit and delete while `submitted` | **no** |
  | Setup / people | yes | no | no |

- Extend the decorator: `endpoint(role=None, kind=None, ...)`. `kind="builder"` (or `"housekeeper"`)
  lets owners through and staff only of that kind; anyone else gets 403 **"That isn't part of your
  run sheet."** `role="owner"` keeps its refusal text "Only an owner can do that." Apply
  `kind="housekeeper"` to the existing visit/task-tick/note/extra/visit-photo routes (owner-only
  routes stay `role="owner"`).

## 2. Remove meal prep

Delete: `app/menu.py`, `app/mcp.py`, `app/oauth.py`, their tests (`tests/test_menu.py`,
`tests/test_mcp.py`, `tests/test_oauth.py`), the plan/settings/fav/got/connections routes and handlers,
`store` functions for settings/plans/shopping, `cmd_import_seed`'s settings and plans parts,
`seed/plans/`, `seed/settings.json`, `frontend/public/oauth.js`, `OAUTH_CSP`, the `/oauth/`, `/mcp`
and `/.well-known/` no-store prefixes (keep `/api/`), the `HRS_PUBLIC_URL` warning and config if
nothing else uses it, and everything in the front end listed in §6. Update `AGENTS.md`, `README.md`,
`ARCHITECTURE.md` and `.env.example` so nothing describes menus, shopping, the connector or OAuth.
Old design docs under `docs/superpowers/` stay as history.

Migration `005_jobs_hours.sql` (one file, next number):
- `DROP TABLE shopping_items; DROP TABLE meal_plans; DROP TABLE oauth_tokens; DROP TABLE oauth_codes; DROP TABLE oauth_clients;`
- Settings: delete the meal settings row(s); drop the `settings` table if nothing else reads it.
- Tasks: `UPDATE tasks SET active = 0 WHERE link = 'meals'` (the "Cook this visit's meal prep" task);
  move the remaining `area = 'Meal prep'` tasks to `area = 'Kitchen'`. Mirror both in `seed/tasks.json`
  (drop t01, re-area t02/t03, and change t11's note "Do it before the meal prep goes in." to "").
  Remove any `link` handling that only existed to jump to Meals.
- `users`: add `kind`, drop `can_see_meals` (see §1).
- New tables (§3, §4).
- `tests/test_migration.py` covers 004 → 005 on a DB holding a plan, shopping items, an OAuth client
  and token, a meals task and a staff user labelled "Builder", and `PRAGMA foreign_key_check` stays empty.

## 3. Jobs

```sql
CREATE TABLE jobs (
    id         INTEGER PRIMARY KEY,
    title      TEXT    NOT NULL,
    details    TEXT    NOT NULL DEFAULT '',
    link       TEXT    NOT NULL DEFAULT '',
    status     TEXT    NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done')),
    sort_order INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,          -- delete = active 0
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
```

Wire shape (camelCase, like the rest of the API):

```ts
interface Person { id: number; displayName: string }
interface JobComment { id: number; body: string; createdAt: string; by: Person }
interface JobPhoto { id: number; caption: string; url: string; createdAt: string; by: Person }
interface Job {
  id: number; title: string; details: string; link: string;
  status: "todo" | "doing" | "done"; sortOrder: number;
  createdAt: string; updatedAt: string; doneAt: string | null; doneBy: Person | null;
  comments: JobComment[];   // oldest first
  photos: JobPhoto[];       // oldest first
}
```

Routes (all JSON unless noted, all non-GET need `X-HRS: 1`):

| Method + path | Who | Body → result |
|---|---|---|
| `GET /api/jobs` | owner, builder | → `{jobs: Job[]}` active only, ordered by `sort_order, id` |
| `POST /api/jobs` | owner | `{title (req, ≤200), details? (≤4000), link? (≤500, must start http:// or https:// when set)}` → `{job}`; new job goes last |
| `PATCH /api/jobs/{id}` | owner: any of title/details/link/status/sortOrder; builder: `status` only (any other key → 403 "Only an owner can do that.") | → `{job}`; status `done` sets done_at/done_by, leaving done clears them; bumps updated_at |
| `DELETE /api/jobs/{id}` | owner | → `{ok}` (active = 0) |
| `POST /api/jobs/{id}/comments` | owner, builder | `{body (req, ≤2000)}` → `{comment}` |
| `DELETE /api/jobs/{id}/comments/{cid}` | author, or any owner | → `{ok}`; anyone else 403 "You can only delete your own comments." |
| `POST /api/jobs/{id}/photos?caption=` | owner, builder | raw JPEG (`endpoint(raw_body=True, max_bytes=PHOTO_MAX_BYTES)`), same checks as visit photos → `{photo}` |
| `DELETE /api/jobs/{id}/photos/{pid}` | author, or any owner | → `{ok}`, unlinks the file like `delete_photo` |

Unknown or inactive job id → 404 "That job doesn't exist." Share the JPEG save/unlink code with
visit photos rather than copying it. `GET /photos/{filename}` keeps its rules (regex, DB lookup before
touching disk): it finds the name in `visit_photos` (allowed: owner, housekeeper) or `job_photos`
(allowed: owner, builder); otherwise 404 — a person without access also gets 404, not 403.

`/api/state` adds `jobs` for owner/builder, so polling picks up comments and status changes.

Seed: `seed/jobs.json` (already written, Ondrej's 16 jobs: `[{title, details, link}]`, in order).
`import-seed` loads it only when the `jobs` table has no rows (prints "Jobs: 16" or "Jobs: skipped,
table not empty"). `import-seed --only jobs` skips tasks, so the live server's edited tasks aren't
overwritten by the seed. New CLI command `add-job-photo --job <id> --file <path> [--caption ...]` copies a
JPEG in through the same save path (checks magic bytes and size), `created_by` = the first active
owner. It's how the work package's reference photos get onto jobs 4, 8 and 16 on the server.

## 4. Hours claims

```sql
CREATE TABLE hours_claims (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users (id),
    work_date   TEXT    NOT NULL,                    -- YYYY-MM-DD
    minutes     INTEGER NOT NULL CHECK (minutes BETWEEN 15 AND 960 AND minutes % 15 = 0),
    note        TEXT    NOT NULL DEFAULT '',
    status      TEXT    NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'approved', 'paid')),
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL,
    approved_at TEXT, approved_by INTEGER REFERENCES users (id),
    paid_at     TEXT, paid_by     INTEGER REFERENCES users (id)
);
CREATE INDEX ix_hours_user_date ON hours_claims (user_id, work_date);
```

Any date is allowed (not only visit days), but not in the future (server's `date.today()`, 400
"You can't claim hours for a future day."). Minutes are whole quarter hours from 15 to 960
(400 "Hours go in quarter hours, from 0.25 to 16."). Several claims on one day are allowed.

```ts
interface HoursClaim {
  id: number; date: string; minutes: number; note: string;
  status: "submitted" | "approved" | "paid";
  createdAt: string; by: Person;
  approvedAt: string | null; approvedBy: Person | null;
  paidAt: string | null; paidBy: Person | null;
}
```

| Method + path | Who | Body → result |
|---|---|---|
| `GET /api/hours` | owner (everyone's), housekeeper (own) | → `{claims: HoursClaim[]}` newest `work_date` first |
| `POST /api/hours` | housekeeper only (owners get 403 "Only staff claim hours.") | `{date, minutes, note? (≤500)}` → `{claim}` |
| `PATCH /api/hours/{id}` | the claimant, while `submitted`: date/minutes/note; owner: `status` (submitted ↔ approved → paid, also back to approved/submitted to undo) | → `{claim}`; a claimant editing an approved/paid claim → 409 "That claim has already been approved." |
| `DELETE /api/hours/{id}` | the claimant while `submitted`, or any owner | → `{ok}` |

Another housekeeper's claim → 404 (not 403). Setting `approved` fills approved_at/by, `paid` fills
paid_at/by (and approved_* if empty), going back clears the later stamps. `/api/state` adds `hours`
for owner/housekeeper.

## 5. Tests

`tests/test_api.py` (or a new `tests/test_jobs.py` / `tests/test_hours.py`): every new route,
including refusals: housekeeper refused on every jobs route, builder refused on visit routes, hours
and job create/edit/delete, builder PATCH with `title` refused, comment/photo delete by a non-author
refused, `/photos/` 404 for the wrong kind of person, hours validation (future date, 10 minutes,
1000 minutes), claimant can't edit after approval, owner can't POST hours, another housekeeper's
claim is 404. `test_cli.py`: import-seed loads 16 jobs once; add-job-photo. Remove meal/OAuth tests.
`python3 -m unittest discover -s tests -v` passes.

## 6. Front end

Remove: `features/meals/`, `features/shopping/`, `setup/MealSettingsCard.tsx`, `setup/ClaudeCard.tsx`,
`lib/macros*`, `lib/shopping*`, plan/settings state and types, `useCanSeeMeals`, the "Meal prep covers"
line on Visit, the Meals toggle in People, and anything that only existed for them.

Tabs (`Tab` type): `home`, `visit` (owner + housekeeper), `jobs` (owner + builder), `hours`
(owner + housekeeper), `setup` (owner). Nav icons in the existing emoji style: 🔨 Jobs, ⏱️ Hours.

**Home tiles**
- Owner: Visit · Jobs ("n open · m in progress") · Hours ("n to approve") · Add a photo.
- Housekeeper: Visit · Add a photo · Leave a note · Log hours ("x.x h this week").
- Builder: Jobs ("n to do · m in progress") spanning, then the existing door code banner and Account
  link. Keep the odd-count spanning rule.

**Jobs screen** (`features/jobs/`)
- Header with counts; filter chips All / To do / In progress / Done (use `components/Chips`).
- Each job is a card (number = position in list), title, status pill, comment and photo counts;
  tapping opens it in place (or a `Dialog`) showing details (preserve line breaks with CSS
  `white-space: pre-wrap`, no HTML), the link as "Open link ↗" (`target=_blank rel=noopener noreferrer`),
  photo strip (reuse visit photo uploader/resizer logic from `lib/image.ts` and `PhotoUploader` where
  it fits), comment thread (author, relative time, delete for author/owner via `InlineConfirm`),
  a comment box, and status buttons To do / In progress / Done (owner and builder).
- Owners also get "Add a job" (title, details, link) at the top, and Edit / Delete on each job.
  Reordering isn't needed in this pass.
- Owner comments and builder comments read differently (e.g. an owner tag/pill) so Ondrej can see
  what came from the homeowners.

**Hours screen** (`features/hours/`)
- Housekeeper: form — date (defaults to today, max today), hours (number input, step 0.25, 0.25–16),
  note — and "Claim hours". Below, claims grouped by week (Monday key, `lib/dates`), each week with a
  total; each claim shows date, hours, note and a status pill; edit/delete while submitted.
- Owner: same grouping across everyone, a per-person total per week, and per claim Approve / Mark
  paid (and undo), plus "Approve all" for a week's submitted claims. Summary at top: hours to approve,
  hours approved but unpaid.
- Pure helpers (minutes ↔ hours text, weekly grouping and totals) in `src/lib/hours.ts` with Vitest.

**Setup → People**: staff rows get a Housekeeper / Builder choice (`kind`) instead of the Meals toggle;
the add-person form too.

State: add `jobs` and `hours` to `AppState` from `/api/state`, optimistic updates like task ticks
where cheap (status change, approve), refresh after every change. Keep the existing patterns:
`api()` client with `X-HRS`, toasts on error, `Activity` keep-alive, no new dependencies, no
`dangerouslySetInnerHTML`, no inline scripts. `npm test`, `npm run typecheck` and `npm run build`
pass, and `grep -c '<script>' static/index.html` finds no inline script.

## 7. After merge (main session, with Owen)

Owen pushes main; redeploy on the droplet (`git pull && docker compose build && docker compose
--profile tunnel up -d`). Then: back up the DB; `import-seed --only jobs seed`; `add-job-photo` for the stair photo (job 4), the two closet photos (job 8) and the garage
plan pages rendered to JPEG (job 16); `create-user --role owner` for Toni Gray with their phone;
create Ondrej as staff `--kind builder` once Owen gives his number.
