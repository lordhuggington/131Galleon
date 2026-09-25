# Architecture notes

## What changed from the Claude-hosted version

The first version ran as a Claude artifact: Claude's platform supplied sign-in, a shared document database, live sync between viewers and Claude calls billed to the viewer. Self-hosting means the app now provides each of those itself.

| Concern | Claude-hosted | Self-hosted |
|---|---|---|
| Who can get in | claude.ai accounts + Share menu | Own `users` table, username + password, two roles |
| Passwords | n/a | scrypt (stdlib `hashlib.scrypt`, N=2^15), min 10 chars |
| Sessions | n/a | Random 256-bit token in an `HttpOnly`, `Secure`, `SameSite=Lax` cookie; only its SHA-256 is stored; 30-day expiry; password change or removal signs the person out everywhere |
| Brute force | n/a | 5 failed logins per username → 15-minute lockout (in memory) |
| CSRF | n/a | Every non-GET request must carry `X-HRS: 1`; browsers can't add custom headers cross-site without CORS, and CORS is off |
| Permissions | Page hid owner tabs; database let any editor write | Enforced server-side per route (`endpoint(role="homeowner")`); housekeepers never receive the shopping list |
| Data | Firestore-style JSON documents | SQLite tables (see below), WAL mode, foreign keys on |
| Live updates | Push subscriptions | Page polls `/api/state` every 20 s while visible, and after every change |
| AI menus | In-browser `sample()` on the viewer's Claude plan | Server calls the Claude Messages API with your `ANTHROPIC_API_KEY` (billed to your Anthropic account per menu). Runs as a background job; the page polls `/api/jobs/{id}` and can cancel |
| Hosting | claude.ai | One Docker container + a volume; Cloudflare Tunnel or a reverse proxy for HTTPS |
| Backups | Platform | `deploy/backup.sh` (SQLite online backup → gzip → optional Hetzner Storage Box) |

### Data model

- `users`, `sessions`: people and logins.
- `tasks`: recurring jobs (`freq` visit/weekly/fortnightly/monthly, `day` any/tue/fri). Deleting sets `active = 0` so history still makes sense.
- `visits`: the note for a visit date. `task_completions` (date, task, who, when) and `visit_extras` (one-off jobs) hang off the date. "Due" logic for weekly/monthly tasks is computed from completion history, the same way as before.
- `settings`: meal targets and preferences as one JSON value.
- `meal_plans`: one row per week (keyed by Monday); recipes are stored as JSON. `shopping_items` is a real table so ticking items off is a single-row update.
- `ai_jobs`: menu generation status and progress.

### Decisions worth knowing

- **Starlette rather than FastAPI.** FastAPI couldn't be installed in the build environment. Starlette is what FastAPI is built on, so the handlers port across almost unchanged if you'd rather use FastAPI; you'd gain request models and OpenAPI docs.
- **Raw SQL + numbered migrations rather than an ORM/Alembic.** Keeps the dependency count at three. Add a migration by creating `migrations/002_whatever.sql`; it's applied once on start-up.
- **One uvicorn worker.** SQLite handles this load easily; the login lockout and the menu job thread live in-process. Moving to several workers would mean moving the lockout into the database.
- **No build step on the front end.** `static/app.js` is the artifact's code with the platform calls swapped for `fetch`. It will be the first thing to outgrow if the app grows; see below.

## Before going live

1. Set `ANTHROPIC_API_KEY` and keep `HRS_COOKIE_SECURE=true`.
2. Serve only over HTTPS (Cloudflare Tunnel does this for you).
3. Create each person their own login and have them change the temporary password under **Account**.
4. Put `deploy/backup.sh` in cron and test one restore.
5. Optional: Cloudflare Access in front of the hostname for a second factor.

## If it grows into a house-management app

- **More than one house.** Add a `households` table and a `household_id` column on tasks, visits, settings and meal plans, and a `memberships(user_id, household_id, role)` table so a housekeeper can work for more than one home. Easier to do before there's much data.
- **More roles.** Roles are a two-value `CHECK` constraint and a `role=` argument on routes. For gardener, pool service, etc., move to a permissions table (role → allowed actions) rather than adding `if role ==` branches.
- **Different visit days.** Tuesday/Friday is currently baked into the schedule logic (`isVisitDay`, `day` values). A `visit_schedule` setting (days of week and hours) would make it general.
- **Front end.** Once there are more screens, move `static/app.js` to React + TypeScript with Vite, keeping the same JSON API.
- **Notifications.** Email or WhatsApp to the homeowner when a visit is finished or a note mentions something running low (the note field is already per visit).
- **Photos.** Before/after or "this is broken" photos per visit: store files on disk or object storage, with a `visit_photos` table.
- **Recipe library.** Recipes live as JSON inside each week. If you want search, ratings or "cook this again", split them into a `recipes` table referenced by week.
- **Grocery ordering.** The shopping list is structured (item, pack size, aisle), so a later step could push it to Instacart or Amazon Fresh.
