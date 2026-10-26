# Architecture notes

## What changed from the Claude-hosted version

The first version ran as a Claude artifact: Claude's platform supplied sign-in, a shared document database, live sync between viewers and Claude calls billed to the viewer. Self-hosting means the app now provides each of those itself.

| Concern | Claude-hosted | Self-hosted |
|---|---|---|
| Who can get in | claude.ai accounts + Share menu | Own `users` table. With Twilio set up everyone signs in with a 6-digit code texted by Twilio Verify; owners also have a password, and staff keep theirs until a phone number is set. Two base types (`owner`, `staff`), a `kind` for staff (`housekeeper` or `builder`) and a free-text label |
| Passwords | n/a | scrypt (stdlib `hashlib.scrypt`, N=2^15), min 10 chars |
| Sessions | n/a | Random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; only its SHA-256 is stored; 30-day expiry; a password change or removal signs the person out everywhere, and so does giving them a phone number |
| Brute force | n/a | 5 failed logins per username → 15-minute lockout (in memory) |
| Sign-in codes | n/a | Twilio Verify holds the code (nothing is stored here). 3 sends per number per 10 minutes; 5 wrong codes lock the number out for 15 minutes; unknown numbers get the same `{"ok": true}` so they can't be enumerated |
| Photos | Uploaded to the platform | Client resizes to JPEG, `POST /api/visits/{date}/photos` (or `/api/jobs/{id}/photos`) takes the raw bytes, files land in `HRS_PHOTOS_DIR` under random names and are served by a cookie-authenticated `GET /photos/{filename}` — visit photos to owners and housekeepers, job photos to owners and builders, a 404 for anyone else |
| CSRF | n/a | Every non-GET request must carry `X-HRS: 1`; browsers can't add custom headers cross-site without CORS, and CORS is off |
| Permissions | Page hid owner tabs; database let any editor write | Enforced server-side per route: `endpoint(role="owner")` for owners only, `endpoint(kind="housekeeper")` / `endpoint(kind="builder")` for owners plus staff of that kind. A builder never receives the visit run sheet or hours, a housekeeper never the jobs, and a housekeeper sees only their own hours claims |
| Data | Firestore-style JSON documents | SQLite tables (see below), WAL mode, foreign keys on |
| Live updates | Push subscriptions | Page polls `/api/state` every 20 s while visible, and after every change |
| Front end | One artifact page rendered by Claude | React 19 + TypeScript built with Vite from `frontend/` into `static/`; one `AppState` context (reducer + React 19 hooks), optimistic ticks, 20 s polling, screens kept alive with `<Activity>` |
| Hosting | claude.ai | One Docker container + a volume; Cloudflare Tunnel or a reverse proxy for HTTPS |
| Backups | Platform | `deploy/backup.sh` (SQLite online backup → gzip, plus an rsync of the photos directory; optional off-site copy to a Hetzner Storage Box) |

### Data model

- `users`, `sessions`: people and logins.
- `users` also carries `label` (free text, e.g. "Builder"), `kind` (`housekeeper` or `builder`; only matters for staff — housekeepers get the visit run sheet and hours, builders the jobs), `phone` (E.164, unique, used for sign-in) and `door_code` (4–8 digits; each person sees their own, and owners see everyone's under **Setup → People**). `password_hash` is nullable: staff normally have none.
- `visit_photos`: one row per photo (`visit_date`, `kind` done/fix, `caption`, random `filename`, `bytes`, who and when). The bytes live on disk, not in SQLite.
- `tasks`: recurring jobs (`freq` visit/weekly/fortnightly/monthly, `day` any/tue/fri). Deleting sets `active = 0` so history still makes sense.
- `visits`: the note for a visit date. `task_completions` (date, task, who, when) and `visit_extras` (one-off jobs) hang off the date. "Due" logic for weekly/monthly tasks is computed from completion history, the same way as before.
- `jobs`: the builder's work package (`title`, `details`, `link`, `status` todo/doing/done, `sort_order`, who finished it and when). Owners add, edit and delete them (delete sets `active = 0`); the builder only moves the status. `job_comments` and `job_photos` hang off a job; a comment or photo is deleted by its author or any owner. `import-seed` loads `seed/jobs.json` only into an empty table.
- `hours_claims`: a housekeeper's hours, one row per claim (`work_date`, `minutes` in whole quarter hours from 15 to 960, `note`, `status` submitted/approved/paid with `approved_*` and `paid_*` stamps). The claimant can change or delete a claim only while it is submitted; owners approve, mark paid and undo either.
- Migration `005` removed meal prep: the `settings`, `meal_plans`, `shopping_items` and OAuth tables, `users.can_see_meals`, and the task that linked to the Meals tab.

### Decisions worth knowing

- **Starlette rather than FastAPI.** FastAPI couldn't be installed in the build environment. Starlette is what FastAPI is built on, so the handlers port across almost unchanged if you'd rather use FastAPI; you'd gain request models and OpenAPI docs.
- **Raw SQL + numbered migrations rather than an ORM/Alembic.** Keeps the dependency count at three. Add a migration by creating `migrations/006_whatever.sql` (the next free number); it's applied once on start-up.
- **One uvicorn worker.** SQLite handles this load easily and the login lockout lives in-process. Moving to several workers would mean moving the lockout into the database.
- **Raw-body photo uploads.** `endpoint(raw_body=True)` hands the handler the request body as `bytes`, so the browser can `POST` a resized JPEG without a multipart parser — no new dependency. The server checks the `FF D8 FF` magic bytes and an 8 MB cap, and names the file from `secrets.token_hex(16)`, never from user input.
- **Foreign keys off during migrations.** SQLite can't alter a CHECK constraint, so `002` rebuilds `users` with the DROP + RENAME procedure. `migrate()` runs every script with `PRAGMA foreign_keys = OFF` and then asserts `PRAGMA foreign_key_check` is empty, so the rebuild can't quietly cascade-delete sessions or blank out `done_by`.
- **Twilio Verify rather than hand-rolled OTP.** Twilio stores and expires the code, rate-limits per number and handles delivery; the app keeps no code and no SMS state of its own.
- **React + TypeScript + Vite on the front end, with a multi-stage Docker build.** `frontend/` is the source; `npm run build` emits `static/` (gitignored) and the image's first stage does that with `npm ci`. Dev deps are kept to TypeScript, Vite, `@vitejs/plugin-react` and Vitest — no UI kit, no state library and no router: `location.hash` plus one context/reducer is enough for a handful of screens. Vitest covers the pure logic (dates, due-task rules, hours, phone formatting); screens are checked by TypeScript, the build and a manual smoke pass.
- **No inline scripts, ever.** The CSP is `script-src 'self'`, so the build must emit only external module scripts — the plan greps the built `index.html` to prove it. React escapes text by default and `dangerouslySetInnerHTML` is banned, which replaces v1's hand-rolled `esc()`.
- **Meal prep is gone (migration 005).** The menus, the shopping list and the Claude connector that planned them were removed in October 2026, along with their screens, routes and tables. Their design notes stay under `docs/superpowers/` as history.

## Before going live

1. Set the three `TWILIO_*` values for text-message sign-in, and keep `HRS_COOKIE_SECURE=true`.
2. Serve only over HTTPS (Cloudflare Tunnel does this for you).
3. Create each person their own login with a phone number, so they sign in by text; owners should also change their temporary password under **Account**.
4. Put `deploy/backup.sh` in cron and test one restore.
5. Optional: Cloudflare Access in front of the hostname for a second factor.

## If it grows into a house-management app

- **More than one house.** Add a `households` table and a `household_id` column on tasks, visits, jobs and hours claims, and a `memberships(user_id, household_id, role)` table so a housekeeper can work for more than one home. Easier to do before there's much data.
- **HomeKit / Schlage integration.** Door codes are typed in by hand today. Talking to the lock directly (programming a code per person, or reading who unlocked the door when) is Owen's next project.
- **Different visit days.** Tuesday/Friday is currently baked into the schedule logic (`isVisitDay`, `day` values). A `visit_schedule` setting (days of week and hours) would make it general.
- **Notifications.** Email or WhatsApp to the owner when a visit is finished or a note mentions something running low (the note field is already per visit).
- **More kinds of staff.** `kind` is a CHECK-constrained column, so a gardener or pool service with their own screen is a migration plus an `endpoint(kind=...)` on their routes.
- **Hours for other staff, or pay rates.** Claims are minutes only; an hourly rate per person would turn the owner's approved-but-unpaid total into money.
