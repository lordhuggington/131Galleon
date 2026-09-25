# House Run Sheet

A small self-hosted app for running a home with a housekeeper: a checklist for each Tuesday and Friday visit, one-off jobs for particular days, high-protein batch meal prep written by Claude, and a shopping list to order from.

Two kinds of people:

| | Owner | Staff |
|---|---|---|
| Home screen with their own door code | ✓ | ✓ |
| Visit checklist: tick jobs, leave notes, add photos | ✓ | ✓ |
| Meals: recipes, prep order, portions | ✓ | only if "Can see Meals" is on |
| Add one-off jobs to a visit | ✓ | |
| Shopping list | ✓ | |
| Setup: tasks, meal settings, people, door codes | ✓ | |
| Generate a week's menu with Claude | ✓ | |
| Delete anyone's photo | ✓ | own photos only |

Staff get a free-text label ("Housekeeper", "Builder", "Pool service"), so anyone can be added without
code changes. With Twilio set up (see below), everyone signs in with a code texted to their phone; owners
also keep a password as a backup.

Stack: Python 3.11+, [Starlette](https://www.starlette.io/) (the framework under FastAPI) and SQLite on the server — three dependencies: `starlette`, `uvicorn`, `httpx`. The front end is React 19 + TypeScript, built with [Vite](https://vite.dev/) from `frontend/` into `static/` (build output, not committed).

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export HRS_DB_PATH=data/house.db HRS_COOKIE_SECURE=0   # plain http on localhost
python -m app.cli import-seed seed                      # tasks, settings and the starter week's menu
python -m app.cli create-user --username owen --name "Owen" --role owner
python -m app.cli create-user --username maria --name "Maria" --role staff --label Housekeeper

export ANTHROPIC_API_KEY=sk-ant-...                     # only needed for "Create menu"
uvicorn app.main:app --reload --port 8000                # terminal 1: the API on :8000
```

Then, in a second terminal, run the front end with hot reload:

```bash
cd frontend
npm install
npm run dev                                             # Vite on :5173, proxying /api, /photos and /healthz to :8000
```

Open http://localhost:5173 while developing. `npm test` runs the Vitest suites for the date, schedule, macro, shopping and phone helpers; `npm run typecheck` runs TypeScript.

For production (and to serve everything from uvicorn on :8000), build the front end once:

```bash
cd frontend && npm run build          # writes ../static
```

`static/` is a build output and is gitignored; the Docker image builds it in its first stage.

Open http://localhost:8000.

Tests: `python -m unittest discover -s tests -v`

## Deploy on a server (Docker)

On a small droplet (1 GB is plenty):

```bash
git clone <this repo> /opt/house-run-sheet && cd /opt/house-run-sheet
cp .env.example .env && nano .env                        # ANTHROPIC_API_KEY, TWILIO_*, tunnel token
mkdir -p data backups && sudo chown 10001:10001 data     # container runs as uid 10001

docker compose build
docker compose run --rm app python -m app.cli import-seed seed
docker compose run --rm app python -m app.cli create-user --username owen --name "Owen" --role owner
docker compose run --rm app python -m app.cli create-user --username maria --name "Maria" --role staff --label Housekeeper

docker compose --profile tunnel up -d                    # app + Cloudflare Tunnel
```

### Getting it onto the internet

The app listens on `127.0.0.1:8000` only. Two options:

- **Cloudflare Tunnel (recommended, no open ports).** Create a tunnel in Cloudflare Zero Trust, copy its token into `CLOUDFLARE_TUNNEL_TOKEN`, and add a public hostname (e.g. `house.yourdomain.com`) pointing at `http://app:8000`. HTTPS is handled by Cloudflare. You can optionally put Cloudflare Access in front for an extra email-code login.
- **Caddy or nginx on the droplet** reverse-proxying to `127.0.0.1:8000` with a Let's Encrypt certificate.

Either way, keep `HRS_COOKIE_SECURE=true` so login cookies are only sent over HTTPS.

### Text-message sign-in (Twilio Verify)

1. In the [Twilio console](https://console.twilio.com/) create a **Verify service** (Verify → Services → Create).
2. Copy the Account SID, an Auth Token and the Verify Service SID into `.env` as `TWILIO_ACCOUNT_SID`,
   `TWILIO_AUTH_TOKEN` and `TWILIO_VERIFY_SERVICE_SID`, then restart.
3. Give each person a phone number (**Setup → People**, or `python -m app.cli set-phone`). They sign in by
   typing their number and the 6-digit code Twilio texts them. Codes are sent at most 3 times per 10 minutes
   per number, and 5 wrong codes lock that number out for 15 minutes.

Leave any of the three values blank and the login screen falls back to username + password. Owners always keep
a password; staff can still use theirs until you put a phone number on their record, which clears it.

**If text messages stop working** (Twilio outage, expired auth token, unpaid bill): owners can still sign in with
their password, and so can staff who have no number on file. Staff who do have one cannot — the app refuses a
password from them. To get one of them back in, run two commands on the server:

```bash
python -m app.cli set-phone --username maria --clear      # removes the number, signs them out everywhere
python -m app.cli set-password --username maria           # prompts for a password they can sign in with
```

Put their number back with `set-phone --phone` once Twilio is working; that clears the password again.

### Backups

`deploy/backup.sh` takes a consistent copy of the SQLite database (safe while running), gzips it into `backups/`, keeps 30 days and optionally rsyncs to a Hetzner Storage Box (`BACKUP_TARGET`). Add it to cron:

```
15 3 * * * cd /opt/house-run-sheet && BACKUP_TARGET=uXXXX@uXXXX.your-storagebox.de:house-run-sheet/ ./deploy/backup.sh >> backups/backup.log 2>&1
```

Visit photos are files, not rows: the script also copies `data/photos/` into `backups/photos/`. That is the
container's `/data/photos` (`HRS_PHOTOS_DIR`); if you move the photos somewhere else, change the path in the
script to match.

Restore: stop the app, `gunzip` a backup to `data/house.db`, copy `backups/photos/` back to `data/photos/`,
start the app.

### Updating

```bash
git pull && docker compose build && docker compose --profile tunnel up -d
```

Schema migrations in `migrations/` run automatically on start-up.

After upgrading to v2, add a phone number for each person straight away (**Setup → People**). Staff can keep
using their old password only until they have one.

## Admin commands

```bash
python -m app.cli migrate
python -m app.cli create-user --username NAME --name "Display name" --role owner|staff \
    [--label "Housekeeper"] [--phone "(310) 555-1234"] [--door-code 4821] [--password PW]
python -m app.cli set-password --username NAME       # owners, or staff with no phone; signs them out everywhere
python -m app.cli set-phone --username NAME --phone "(310) 555-1234"   # staff then sign in by text only
python -m app.cli set-phone --username NAME --clear  # removes their number; signs them out everywhere
python -m app.cli import-seed seed                   # re-import tasks/settings/plans (upserts)
```

Owners are prompted for a password when `--password` is left out; staff aren't, because they sign in by text.
To test a staff account before Twilio is set up, give it `--password` as well — the app accepts that password
only while the person has no phone number on file.

Inside Docker prefix with `docker compose run --rm app` (or `docker compose exec app` while running).

People can also be added, reset and removed from **Setup → People** in the app.

## Layout

```
app/
  main.py      ASGI app, security headers, start-up migrations
  api.py       JSON API routes + role checks
  auth.py      scrypt passwords, cookie sessions, login lockout
  store.py     data access for tasks, visits, settings, meal plans
  ai.py        menu prompt, Claude API streaming call, background job
  db.py        SQLite connection + migration runner
  cli.py       admin commands
migrations/    numbered .sql files (tracked with PRAGMA user_version)
frontend/      React 19 + TypeScript app (Vite)
  src/lib/       pure logic: dates, due-task schedule, macros, shopping text, phone (Vitest)
  src/state/     AppState context + reducer, polling, toasts
  src/features/  home, visit, meals, shopping, setup, auth screens
static/        build output from `npm run build` (gitignored, served by Starlette)
seed/          tasks, settings and the starter week exported from the Claude-hosted version
tests/         unittest suite (API, roles, generation with a mocked Claude API)
deploy/        backup script
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for what changed from the Claude-hosted version and where to take it next.
