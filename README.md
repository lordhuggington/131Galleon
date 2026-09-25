# House Run Sheet

A small self-hosted app for running a home with a housekeeper: a checklist for each Tuesday and Friday visit, one-off jobs for particular days, high-protein batch meal prep written by Claude, and a shopping list to order from.

Two roles:

| | Homeowner | Housekeeper |
|---|---|---|
| Visit checklist: tick jobs, leave notes | ✓ | ✓ |
| Meals: recipes, prep order, portions | ✓ | ✓ |
| Add one-off jobs to a visit | ✓ | |
| Shopping list | ✓ | |
| Setup: tasks, meal settings, people | ✓ | |
| Generate a week's menu with Claude | ✓ | |

Stack: Python 3.11+, [Starlette](https://www.starlette.io/) (the framework under FastAPI), SQLite, vanilla JS. Three dependencies: `starlette`, `uvicorn`, `httpx`.

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export HRS_DB_PATH=data/house.db HRS_COOKIE_SECURE=0   # plain http on localhost
python -m app.cli import-seed seed                      # tasks, settings and the starter week's menu
python -m app.cli create-user --username owen --name "Owen" --role homeowner
python -m app.cli create-user --username maria --name "Maria" --role housekeeper

export ANTHROPIC_API_KEY=sk-ant-...                     # only needed for "Create menu"
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000.

Tests: `python -m unittest discover -s tests -v`

## Deploy on a server (Docker)

On a small droplet (1 GB is plenty):

```bash
git clone <this repo> /opt/house-run-sheet && cd /opt/house-run-sheet
cp .env.example .env && nano .env                        # ANTHROPIC_API_KEY, tunnel token
mkdir -p data backups && sudo chown 10001:10001 data     # container runs as uid 10001

docker compose build
docker compose run --rm app python -m app.cli import-seed seed
docker compose run --rm app python -m app.cli create-user --username owen --name "Owen" --role homeowner
docker compose run --rm app python -m app.cli create-user --username maria --name "Maria" --role housekeeper

docker compose --profile tunnel up -d                    # app + Cloudflare Tunnel
```

### Getting it onto the internet

The app listens on `127.0.0.1:8000` only. Two options:

- **Cloudflare Tunnel (recommended, no open ports).** Create a tunnel in Cloudflare Zero Trust, copy its token into `CLOUDFLARE_TUNNEL_TOKEN`, and add a public hostname (e.g. `house.yourdomain.com`) pointing at `http://app:8000`. HTTPS is handled by Cloudflare. You can optionally put Cloudflare Access in front for an extra email-code login.
- **Caddy or nginx on the droplet** reverse-proxying to `127.0.0.1:8000` with a Let's Encrypt certificate.

Either way, keep `HRS_COOKIE_SECURE=true` so login cookies are only sent over HTTPS.

### Backups

`deploy/backup.sh` takes a consistent copy of the SQLite database (safe while running), gzips it into `backups/`, keeps 30 days and optionally rsyncs to a Hetzner Storage Box (`BACKUP_TARGET`). Add it to cron:

```
15 3 * * * cd /opt/house-run-sheet && BACKUP_TARGET=uXXXX@uXXXX.your-storagebox.de:house-run-sheet/ ./deploy/backup.sh >> backups/backup.log 2>&1
```

Restore: stop the app, `gunzip` a backup to `data/house.db`, start the app.

### Updating

```bash
git pull && docker compose build && docker compose --profile tunnel up -d
```

Schema migrations in `migrations/` run automatically on start-up.

## Admin commands

```bash
python -m app.cli migrate
python -m app.cli create-user --username NAME --name "Display name" --role homeowner|housekeeper
python -m app.cli set-password --username NAME       # also signs them out everywhere
python -m app.cli import-seed seed                   # re-import tasks/settings/plans (upserts)
```

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
static/        index.html, app.js, app.css (no build step)
seed/          tasks, settings and the starter week exported from the Claude-hosted version
tests/         unittest suite (API, roles, generation with a mocked Claude API)
deploy/        backup script
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for what changed from the Claude-hosted version and where to take it next.
