# Architecture notes

## What changed from the Claude-hosted version

The first version ran as a Claude artifact: Claude's platform supplied sign-in, a shared document database, live sync between viewers and Claude calls billed to the viewer. Self-hosting means the app now provides each of those itself.

| Concern | Claude-hosted | Self-hosted |
|---|---|---|
| Who can get in | claude.ai accounts + Share menu | Own `users` table. With Twilio set up everyone signs in with a 6-digit code texted by Twilio Verify; owners also have a password, and staff keep theirs until a phone number is set. Two base types (`owner`, `staff`) plus a free-text label |
| Passwords | n/a | scrypt (stdlib `hashlib.scrypt`, N=2^15), min 10 chars |
| Sessions | n/a | Random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; only its SHA-256 is stored; 30-day expiry; a password change or removal signs the person out everywhere, and so does giving them a phone number |
| Brute force | n/a | 5 failed logins per username → 15-minute lockout (in memory) |
| Sign-in codes | n/a | Twilio Verify holds the code (nothing is stored here). 3 sends per number per 10 minutes; 5 wrong codes lock the number out for 15 minutes; unknown numbers get the same `{"ok": true}` so they can't be enumerated |
| Photos | Uploaded to the platform | Client resizes to JPEG, `POST /api/visits/{date}/photos` takes the raw bytes, files land in `HRS_PHOTOS_DIR` under random names and are served by a cookie-authenticated `GET /photos/{filename}` |
| CSRF | n/a | Every non-GET request must carry `X-HRS: 1`; browsers can't add custom headers cross-site without CORS, and CORS is off |
| Permissions | Page hid owner tabs; database let any editor write | Enforced server-side per route (`endpoint(role="owner")`); staff never receive the shopping list, and each person's Meals access is a per-user `can_see_meals` flag |
| Data | Firestore-style JSON documents | SQLite tables (see below), WAL mode, foreign keys on |
| Live updates | Push subscriptions | Page polls `/api/state` every 20 s while visible, and after every change |
| Front end | One artifact page rendered by Claude | React 19 + TypeScript built with Vite from `frontend/` into `static/`; one `AppState` context (reducer + React 19 hooks), optimistic ticks, 20 s polling, screens kept alive with `<Activity>` |
| AI menus | In-browser `sample()` on the viewer's Claude plan | Server calls the Claude Messages API with your `ANTHROPIC_API_KEY` (billed to your Anthropic account per menu). Runs as a background job; the page polls `/api/jobs/{id}` and can cancel |
| Hosting | claude.ai | One Docker container + a volume; Cloudflare Tunnel or a reverse proxy for HTTPS |
| Backups | Platform | `deploy/backup.sh` (SQLite online backup → gzip, plus an rsync of the photos directory; optional off-site copy to a Hetzner Storage Box) |

### Data model

- `users`, `sessions`: people and logins.
- `users` also carries `label` (free text, e.g. "Builder"), `phone` (E.164, unique, used for sign-in), `door_code` (4–8 digits; each person sees their own, and owners see everyone's under **Setup → People**) and `can_see_meals`. `password_hash` is nullable: staff normally have none.
- `visit_photos`: one row per photo (`visit_date`, `kind` done/fix, `caption`, random `filename`, `bytes`, who and when). The bytes live on disk, not in SQLite.
- `tasks`: recurring jobs (`freq` visit/weekly/fortnightly/monthly, `day` any/tue/fri). Deleting sets `active = 0` so history still makes sense.
- `visits`: the note for a visit date. `task_completions` (date, task, who, when) and `visit_extras` (one-off jobs) hang off the date. "Due" logic for weekly/monthly tasks is computed from completion history, the same way as before.
- `settings`: meal targets and preferences as one JSON value.
- `meal_plans`: one row per week (keyed by Monday); recipes are stored as JSON. `shopping_items` is a real table so ticking items off is a single-row update.
- `ai_jobs`: menu generation status and progress.

### Decisions worth knowing

- **Starlette rather than FastAPI.** FastAPI couldn't be installed in the build environment. Starlette is what FastAPI is built on, so the handlers port across almost unchanged if you'd rather use FastAPI; you'd gain request models and OpenAPI docs.
- **Raw SQL + numbered migrations rather than an ORM/Alembic.** Keeps the dependency count at three. Add a migration by creating `migrations/003_whatever.sql`; it's applied once on start-up.
- **One uvicorn worker.** SQLite handles this load easily; the login lockout and the menu job thread live in-process. Moving to several workers would mean moving the lockout into the database.
- **Raw-body photo uploads.** `endpoint(raw_body=True)` hands the handler the request body as `bytes`, so the browser can `POST` a resized JPEG without a multipart parser — no new dependency. The server checks the `FF D8 FF` magic bytes and an 8 MB cap, and names the file from `secrets.token_hex(16)`, never from user input.
- **Foreign keys off during migrations.** SQLite can't alter a CHECK constraint, so `002` rebuilds `users` with the DROP + RENAME procedure. `migrate()` runs every script with `PRAGMA foreign_keys = OFF` and then asserts `PRAGMA foreign_key_check` is empty, so the rebuild can't quietly cascade-delete sessions or blank out `done_by`.
- **Twilio Verify rather than hand-rolled OTP.** Twilio stores and expires the code, rate-limits per number and handles delivery; the app keeps no code and no SMS state of its own.
- **React + TypeScript + Vite on the front end, with a multi-stage Docker build.** `frontend/` is the source; `npm run build` emits `static/` (gitignored) and the image's first stage does that with `npm ci`. Dev deps are kept to TypeScript, Vite, `@vitejs/plugin-react` and Vitest — no UI kit, no state library and no router: `location.hash` plus one context/reducer is enough for five screens. Vitest covers the pure logic (dates, due-task rules, macros, shopping text, phone formatting); screens are checked by TypeScript, the build and a manual smoke pass.
- **No inline scripts, ever.** The CSP is `script-src 'self'`, so the build must emit only external module scripts — the plan greps the built `index.html` to prove it. React escapes text by default and `dangerouslySetInnerHTML` is banned, which replaces v1's hand-rolled `esc()`.

## Before going live

1. Set `ANTHROPIC_API_KEY`, the three `TWILIO_*` values for text-message sign-in, and keep `HRS_COOKIE_SECURE=true`.
2. Serve only over HTTPS (Cloudflare Tunnel does this for you).
3. Create each person their own login with a phone number, so they sign in by text; owners should also change their temporary password under **Account**.
4. Put `deploy/backup.sh` in cron and test one restore.
5. Optional: Cloudflare Access in front of the hostname for a second factor.

## If it grows into a house-management app

- **More than one house.** Add a `households` table and a `household_id` column on tasks, visits, settings and meal plans, and a `memberships(user_id, household_id, role)` table so a housekeeper can work for more than one home. Easier to do before there's much data.
- **HomeKit / Schlage integration.** Door codes are typed in by hand today. Talking to the lock directly (programming a code per person, or reading who unlocked the door when) is Owen's next project.
- **Different visit days.** Tuesday/Friday is currently baked into the schedule logic (`isVisitDay`, `day` values). A `visit_schedule` setting (days of week and hours) would make it general.
- **Notifications.** Email or WhatsApp to the owner when a visit is finished or a note mentions something running low (the note field is already per visit).
- **Recipe library.** Recipes live as JSON inside each week. If you want search, ratings or "cook this again", split them into a `recipes` table referenced by week.
- **Grocery ordering.** The shopping list is structured (item, pack size, aisle), so a later step could push it to Instacart or Amazon Fresh.
