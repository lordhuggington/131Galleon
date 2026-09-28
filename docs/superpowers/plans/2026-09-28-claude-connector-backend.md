# Claude Connector Menus — Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace server-side menu generation with a remote MCP connector — the app becomes its own OAuth 2.1 authorization server, exposes `get_brief` and `save_session` over `POST /mcp`, and refuses any session whose macros miss the household's targets.

**Architecture:** Three new/rewritten backend modules mounted alongside `/api` in one Starlette process. `app/ai.py` becomes `app/menu.py` (the brief, the rules, normalization and validation — no model calls). `app/oauth.py` is the whole OAuth 2.1 server: metadata documents, dynamic client registration, a server-rendered consent page with the SMS login inline, and a token endpoint with PKCE S256 and rotating refresh tokens. `app/mcp.py` is a hand-rolled JSON-RPC 2.0 endpoint in the stateless Streamable-HTTP style: one request in, one JSON response out, no SSE, no sessions. Everything Anthropic-shaped — the `ai_jobs` table, the API key, the background thread — is deleted outright.

**Tech Stack:** Python 3.13 (3.11+ supported), starlette 1.0.0, uvicorn 0.46.0, httpx 0.28.1, SQLite via stdlib `sqlite3`, `unittest` + `starlette.testclient.TestClient`. No new dependencies: PKCE, SHA-256, base64url, form parsing, HTML escaping and time zones all come from the standard library.

**Spec:** `docs/superpowers/specs/2026-09-28-claude-connector-menus-design.md` — read it alongside this plan.

## Global Constraints

Every task's requirements implicitly include this section.

- **Branch:** work on `claude-connector` (already checked out). Commit after every task. **Never commit `.env` or anything under `data/`.**
- **No new Python dependencies.** `requirements.txt` stays exactly `starlette==1.0.0`, `uvicorn[standard]==0.46.0`, `httpx==0.28.1`. Anything else must come from the standard library. In particular: **never call `request.form()`** — Starlette 1.0 asserts `python-multipart` is installed before parsing *any* form body, urlencoded included, and it is not installed. Parse forms with `urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)` through the shared `oauth.form_body(request)` helper.
- **Run the whole suite:** `python3 -m unittest discover -s tests -v` from the repo root. One test: `python3 -m unittest tests.test_menu.MenuTest.test_name -v`. Use `python3` (Python 3.13.5 locally).
- **Baseline is 101 passing tests.** Task 1 deletes 13 and adds 1 (89); every task after that ends with the full suite green.
- **AGENTS.md rules, verbatim** (these override any habit you have):
  - "Python 3.11+, Starlette + stdlib `sqlite3` + `httpx`. No ORM. Keep dependencies minimal."
  - "Run tests: `python3 -m unittest discover -s tests -v`. Add a test for every new route, including staff being refused where relevant."
  - "Schema changes: add `migrations/NNN_description.sql` (next number). Never edit an applied migration. `migrate()` runs each script with foreign keys off and then checks `PRAGMA foreign_key_check`."
  - "Every API handler uses the `endpoint(role=...)` decorator in `app/api.py`; owner-only routes must pass `role=\"owner\"` (the refusal reads \"Only an owner can do that.\"). Roles are exactly `owner` and `staff`."
  - "Never log a door code or a sign-in code, and never log a full phone number — redact it the way `app/sms.py: _redact` does." (Tokens and authorization codes get the same treatment: never log one.)
  - "`static/` is a build output: gitignored, never edited by hand, rebuilt by `npm run build` and by the Dockerfile's node stage." — so the consent page's script is written to **`frontend/public/oauth.js`**, which Vite copies verbatim into `static/` and the existing static mount serves at `/oauth.js`.
  - "CSP is `script-src 'self'`: no inline `<script>` in the built page, and never use `dangerouslySetInnerHTML`."
  - "Non-GET requests must send `X-HRS: 1` (CSRF guard)" — this applies to `/api/*` only. `/oauth/*` is deliberately outside `/api`, and `POST /oauth/authorize` uses a `Sec-Fetch-Site` check instead, because a plain browser form post cannot carry a custom header.
  - "Visit days are Tuesday and Friday; weeks are keyed by their Monday (`YYYY-MM-DD`)."
- **Exact strings matter.** Every quoted message, tool description, rule and error line in this plan is copied character for character from the spec. Claude reads the validation lines and the frontend renders the `error` field verbatim; do not paraphrase, re-wrap or "improve" any of them.
- **Tests get a temp DB** via `os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "…db")` in `setUp` with `tempfile.TemporaryDirectory()`, cleaned up in `tearDown`, and save/restore every env var they touch (copy the `save_env`/`restore_env` pattern at the top of `tests/test_api.py`). **Do not import across test files** — repeat a helper rather than importing it from another test module.
- **`HRS_PUBLIC_URL` is `http://testserver` in tests** — that is the host `starlette.testclient.TestClient` uses, so the metadata documents and the `WWW-Authenticate` header come out with a URL the tests can assert on.
- **Out of scope for this plan:** everything under `frontend/src/` and `static/` (a separate frontend plan deletes `GenPanel.tsx`/`useGeneration.ts` and reworks the screens), `README.md`, `ARCHITECTURE.md`, `AGENTS.md` and `.env.example` (same plan). The one frontend file this plan writes is `frontend/public/oauth.js`, because it belongs to the server-rendered consent page. The React app will be partly broken in the meantime (it still calls the deleted `/api/plans/{week}/generate`) — that is accepted and expected.

---

## File Structure

| File | Responsibility after this plan |
|---|---|
| `app/menu.py` | **Renamed from `app/ai.py`.** `AISLES`, `SLOTS`, `add_days`, `RULES`, the two JSON Schemas, `today_la`, `resolve_prep_date`, `week_and_session`, `brief`, `normalize_session`, `validate_session`, `session_data`, `save_result`, `problem_report`, `MenuError`. No HTTP, no model call. |
| `app/oauth.py` | **New.** The OAuth 2.1 authorization server *and* resource server: two metadata documents, `POST /oauth/register`, `GET`/`POST /oauth/authorize` (the consent page), `POST /oauth/token`, plus `owner_for_bearer`, `require_owner_bearer`, `unauthorized`, `form_body`, `sweep`. |
| `app/mcp.py` | **New.** `POST /mcp` JSON-RPC envelope, `initialize`/`ping`/`tools/list`/`tools/call`, the two tool handlers, `405` for `GET`/`DELETE`. |
| `app/api.py` | Loses `generate`, `_job_id`, `get_job`, `cancel_job` and their routes; gains `GET /api/oauth/connections` and `DELETE /api/oauth/connections/{family}`. |
| `app/main.py` | Mounts `oauth.routes` and `mcp.routes`; `NO_STORE_PREFIXES`; `OAUTH_CSP`; the `HRS_PUBLIC_URL` warning; no more `ai_jobs` sweep. |
| `app/config.py` | `public_url` in; `anthropic_api_key` and `anthropic_model` out. |
| `app/auth.py` | `_token_hash` → `token_hash` (public; `app/oauth.py` imports it). |
| `app/store.py` | `get_plan` reads per-session leftovers; `save_plan` writes them (seed import only); new `save_session` merges one session into a week. |
| `migrations/004_oauth.sql` | **New.** Three `oauth_*` tables, `DROP TABLE ai_jobs`, and the five statements that move leftovers onto their session. |
| `frontend/public/oauth.js` | **New.** The consent page's script: SMS code sign-in, password disclosure, sign-out. Vanilla JS, no framework, no build step. |
| `tests/test_menu.py` | **New.** Date resolution, the brief, normalization, validation, the merge. |
| `tests/test_oauth.py` | **New.** Metadata, registration, the consent page, the full dance, token errors, rotation, connections. |
| `tests/test_mcp.py` | **New.** The 401 handshake, the JSON-RPC envelope, both tools end to end. |
| `tests/test_api.py` | Generation tests deleted; `HRS_PUBLIC_URL` replaces `ANTHROPIC_API_KEY`; two small additions. |
| `tests/test_migration.py` | Two 004 tests and an `apply_up_to` helper. |

**Interface summary** (every task's `Interfaces` block repeats what it needs; this is the map):

```python
menu.MenuError(Exception)                                   # message goes back to Claude as a tool error
menu.RULES: str
menu.AISLES: list[str]; menu.SLOTS: tuple[str, str, str]    # ("breakfast", "main", "dessert")
menu.GET_BRIEF_SCHEMA: dict; menu.SAVE_SESSION_SCHEMA: dict
menu.add_days(d: str, n: int) -> str
menu.today_la() -> datetime.date
menu.resolve_prep_date(given: str | None) -> str
menu.week_and_session(prep_date: str) -> tuple[str, str]
menu.brief(conn, date: str | None) -> dict
menu.normalize_session(args) -> tuple[dict, list[dict]]     # (normalized, normalized["shopping"])
menu.validate_session(normalized: dict, settings: dict) -> list[str]
menu.problem_report(problems: list[str]) -> str
menu.session_data(normalized: dict, settings: dict) -> dict
menu.save_result(week: str, session: str, session_data: dict, shopping: list[dict]) -> tuple[str, dict]
store.save_session(conn, week: str, session: str, session_data: dict, shopping: list[dict], user_id: int | None) -> None
auth.token_hash(token: str) -> str
oauth.form_body(request) -> dict[str, str]                  # raises oauth.FormTooLarge
oauth.unauthorized(token_presented: bool) -> Response
oauth.owner_for_bearer(conn, header: str | None) -> sqlite3.Row | None
oauth.require_owner_bearer(request) -> tuple[sqlite3.Row | None, Response | None]
oauth.sweep(conn) -> None
oauth.routes: list[Route]; mcp.routes: list[Route]
```

---

### Task 1: Delete in-app generation and turn `app/ai.py` into `app/menu.py`

**Files:**
- Delete + rewrite: `app/ai.py` → `app/menu.py` (via `git mv`)
- Modify: `app/api.py:17` (imports), `app/api.py:522-551` (`generate`, `_job_id`, `get_job`, `cancel_job`), `app/api.py:723-725` (three routes)
- Modify: `app/config.py:15-44`
- Modify: `app/main.py:1-66` (lifespan)
- Modify: `tests/test_api.py:1-21` (imports, `ENV_KEYS`), `tests/test_api.py:46-62` (`setUp`), `tests/test_api.py:267-278` (`test_staff_limits`), `tests/test_api.py:785-1023` (all AI tests)
- Modify: `tests/test_cli.py:13-25` (`ENV_KEYS`, `setUp`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `app/menu.py` exporting exactly `AISLES: list[str]`, `SLOTS: tuple[str, str, str]` = `("breakfast", "main", "dessert")`, `add_days(d: str, n: int) -> str`. Tasks 3 and 4 grow this module.
  - `app.config.Config.public_url: str` — `os.environ.get("HRS_PUBLIC_URL", "http://localhost:8000").rstrip("/")`. No `anthropic_api_key`, no `anthropic_model`.
  - `tests/test_api.py` fixture facts every later task copies: `H = {"x-hrs": "1"}`; `ENV_KEYS` includes `HRS_PUBLIC_URL`; `setUp` imports `seed/` and creates owner `owen` (password `owner-pass`) and staff `maria` (password `staff-pass`); `self.login("owen" | "maria")` returns the `me` dict.

**Context:** This task is a pure deletion, so the test step comes *after* the removal on purpose: asserting `POST /api/plans/{week}/generate` is gone while the handler still exists would start a real background thread calling `api.anthropic.com` from the test suite. Delete the tests that pin the old behaviour first, then the code, then add the one regression test that proves the routes are gone.

The `ai_jobs` table stays in the database until Task 2 — migration 004 drops it. Nothing reads it after this task.

- [ ] **Step 1: Delete the generation tests from `tests/test_api.py`**

Delete these four methods in full (currently lines 785–839, starting at the `# ---- AI generation (Claude API mocked) ----` comment, which also goes): `test_generate_menu`, `test_generate_bad_reply`, `test_normalize_keeps_the_amazon_fresh_search_phrase`, `test_the_prompt_asks_for_amazon_fresh_wording`.

Then delete the whole `class StreamParsingTest` (currently lines 846–1023 — it sits *after* the stray `if __name__ == "__main__": unittest.main()` block, which becomes the file's real ending). After this the file must end:

```python
        self.assertEqual(self.c.delete(f"/api/visits/2026-09-29/photos/{mine['id']}", headers=H).status_code, 404)
        self.assertEqual(self.c.get("/api/state").json()["visits"].get("2026-09-29", {}).get("photos", []), [])


if __name__ == "__main__":
    unittest.main()
```

Delete the now-unused imports at the top of the file — `import json` and `import time` (lines 6 and 9). Keep `contextlib`, `io`, `os`, `tempfile`, `unittest`, `Path`, `mock`.

- [ ] **Step 2: Swap the environment variable in `tests/test_api.py`**

```python
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL",
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_VERIFY_SERVICE_SID")
```

and in `setUp`, replace the `ANTHROPIC_API_KEY` line with:

```python
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"  # the host starlette.testclient uses
```

Drop the `POST /api/plans/…/generate` assertion from `test_staff_limits` (line 275) so the method reads:

```python
    # ---- roles ----
    def test_staff_limits(self):
        self.login("maria")
        state = self.c.get("/api/state").json()
        self.assertEqual(state["me"]["role"], "staff")
        self.assertEqual(len(state["tasks"]), 41)
        self.assertEqual(self.c.post("/api/tasks", json={"title": "x"}, headers=H).status_code, 403)
        self.assertEqual(self.c.get("/api/users").status_code, 403)
        self.assertEqual(self.c.post("/api/visits/2026-09-29/extras", json={"title": "x"}, headers=H).status_code, 403)
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertIn("sessions", plan)
        self.assertNotIn("shopping", plan)  # shopping list is owner-only
```

- [ ] **Step 3: Keep `tests/test_cli.py` quiet about the new warning**

`password_login` boots the real app, whose `lifespan` will warn when `HRS_PUBLIC_URL` is unset. Set it there too:

```python
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_PUBLIC_URL")  # set here; restored in tearDown so the suite stays order-independent
```

and in `setUp`, after the `HRS_PHOTOS_DIR` line:

```python
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
```

- [ ] **Step 4: Run the suite — it must still be green with 13 fewer tests**

Run: `python3 -m unittest discover -s tests`
Expected: `Ran 88 tests` … `OK` (101 − 13).

- [ ] **Step 5: Delete the generation routes and handlers from `app/api.py`**

Change the import on line 17:

```python
from . import sms, store
```

Delete these four functions in full (currently lines 522–551): `generate`, `_job_id`, `get_job`, `cancel_job` — including the `@endpoint(role="owner")` decorators above `generate`, `get_job` and `cancel_job`. The `# ---------- people (owner) ----------` comment now follows `set_got` directly.

Delete these three lines from `routes` (currently 723–725):

```python
    Route("/api/plans/{week}/generate", generate, methods=["POST"]),
    Route("/api/jobs/{job_id}", get_job),
    Route("/api/jobs/{job_id}/cancel", cancel_job, methods=["POST"]),
```

- [ ] **Step 6: Rename the module and cut it down**

```bash
git mv app/ai.py app/menu.py
```

Then replace the **entire** contents of `app/menu.py` with this. Everything Anthropic-shaped goes: `API_URL`, `GenerationError`, `build_prompt`, `extract_json`, `normalize_plan`, `_num`, `call_model`, `_apply_delta`, `_api_error_message`, `_Cancelled`, `recent_context`, `start_job`, `run_job`, and the `json`/`re`/`threading`/`time`/`httpx`/`Callable`/`config`/`db`/`store` imports they needed.

```python
"""The menu brief, the rules Claude follows, and validation of a posted prep session.

Menus are planned in the Claude app and posted back through the MCP connector (app/mcp.py).
Nothing in this module calls a model or talks to the network.
"""
from __future__ import annotations

from datetime import date, timedelta

AISLES = ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"]
SLOTS = ("breakfast", "main", "dessert")


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()
```

- [ ] **Step 7: Swap the config values**

In `app/config.py`, delete the `anthropic_api_key` and `anthropic_model` fields and their two `os.environ.get` lines, and add `public_url`. The dataclass and factory become:

```python
@dataclass(frozen=True)
class Config:
    db_path: str
    public_url: str
    cookie_secure: bool
    session_days: int
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_verify_service_sid: str
    photos_dir: str

    @property
    def sms_enabled(self) -> bool:
        """Text-message sign-in works only when all three Twilio values are set."""
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_verify_service_sid)


def get_config() -> Config:
    return Config(
        db_path=os.environ.get("HRS_DB_PATH", "data/house.db"),
        # The address Claude reaches this app on. Every OAuth metadata URL and the connector URL are
        # built from it, so a trailing slash would produce '…//mcp'.
        public_url=os.environ.get("HRS_PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        cookie_secure=_bool("HRS_COOKIE_SECURE", True),
        session_days=int(os.environ.get("HRS_SESSION_DAYS", "30")),
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", "").strip(),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", "").strip(),
        twilio_verify_service_sid=os.environ.get("TWILIO_VERIFY_SERVICE_SID", "").strip(),
        photos_dir=os.environ.get("HRS_PHOTOS_DIR", "data/photos"),
    )
```

- [ ] **Step 8: Drop the orphan-job sweep and warn about `HRS_PUBLIC_URL`**

In `app/main.py`: add `import os` to the imports, change `from .db import connect, migrate, now_iso` to `from .db import connect, migrate`, and replace `lifespan` with:

```python
@contextlib.asynccontextmanager
async def lifespan(app):
    cfg = get_config()
    Path(cfg.photos_dir).mkdir(parents=True, exist_ok=True)
    if not os.environ.get("HRS_PUBLIC_URL"):
        # Local development keeps working; a misconfigured droplet is loud in the logs instead of
        # silently advertising localhost to Claude.
        log.warning("HRS_PUBLIC_URL is not set; the Claude connector will advertise %s", cfg.public_url)
    conn = connect()
    try:
        version = migrate(conn)
        log.info("database ready at schema version %s", version)
    finally:
        conn.close()
    yield
```

- [ ] **Step 9: Grep for anything left behind**

Run:

```bash
grep -rn "ai_jobs\|ANTHROPIC\|anthropic\|app\.ai\|from \.ai\|import ai\b\|jobId\|/api/jobs" app/ tests/ migrations/001_initial.sql
```

Expected: **only** `migrations/001_initial.sql` (the `ai_jobs` table it created — never edit an applied migration; Task 2 drops the table in 004). If anything under `app/` or `tests/` still matches, fix it before moving on.

- [ ] **Step 10: Add the regression test**

Add this to `tests/test_api.py`, immediately after `test_staff_limits`:

```python
    def test_menu_generation_is_gone(self):
        self.login("owen")
        self.assertEqual(self.c.post("/api/plans/2026-09-28/generate", json={}, headers=H).status_code, 404)
        self.assertEqual(self.c.get("/api/jobs/1").status_code, 404)
```

- [ ] **Step 11: Run the suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 89 tests` … `OK`. No `ModuleNotFoundError: app.ai`, no warning about `HRS_PUBLIC_URL`.

- [ ] **Step 12: Commit**

```bash
git add app/ai.py app/menu.py app/api.py app/config.py app/main.py tests/test_api.py tests/test_cli.py
git commit -m "Delete in-app menu generation; app/ai.py becomes app/menu.py" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: Migration 004 and per-session leftovers in `store.py`

**Files:**
- Create: `migrations/004_oauth.sql`
- Modify: `app/store.py:100-130` (`save_plan`, `get_plan`)
- Modify: `tests/test_migration.py` (add `apply_up_to` + two tests)
- Modify: `tests/test_api.py` (one new test)

**Interfaces:**
- Consumes: `app.db.migrate(conn) -> int` (runs each script with foreign keys off, then `PRAGMA foreign_key_check`); `app.db.MIGRATIONS_DIR: Path`; `store.save_plan(conn, week, plan, source, note, user_id, got=None) -> None` as it is today.
- Produces:
  - Schema version **4**: tables `oauth_clients (client_id, client_name, redirect_uris, created_at)`, `oauth_codes (code_hash, client_id, user_id, redirect_uri, code_challenge, scope, resource, expires_at, created_at)`, `oauth_tokens (token_hash, kind, client_id, user_id, family, scope, expires_at, created_at, last_used_at, revoked_at)` plus indexes `ix_oauth_tokens_family`, `ix_oauth_tokens_user`. `ai_jobs` is gone. `shopping_items.for_session` still permits `'both'`.
  - `store.get_plan(conn, week, include_shopping) -> dict | None` — **no** top-level `leftovers`; every entry of `plan["sessions"]` has a `leftovers: list[str]`.
  - `store.save_plan(...)` — writes `{"sessions": {...}}` only, each session carrying its own `leftovers`.
  - `tests/test_migration.py: apply_up_to(conn, last: int) -> None`.

**Context:** SQLite's JSON functions have been built in by default since 3.38.0 (2022-02); `python:3.12-slim` ships 3.46.1 and the dev box 3.50.4. The five `UPDATE` statements below were run against both and produce identical output, including the "already per-session" and "no sessions at all" cases. Copy them exactly — do not reformat them.

- [ ] **Step 1: Write the failing tests**

Add this helper to `tests/test_migration.py`, right after the existing `apply_001`:

```python
def apply_up_to(conn, last: int) -> None:
    """Apply migrations 001..last and stop, so a test can write pre-migration rows.

    Foreign keys go off the way app.db.migrate does it: 002 rebuilds users with a DROP + RENAME.
    """
    from app.db import MIGRATIONS_DIR
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for f in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            num = int(f.name[:3])
            if num > last:
                break
            conn.executescript(f"BEGIN;\n{f.read_text()}\nPRAGMA user_version = {num};\nCOMMIT;")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
```

Add `import json` to the file's imports, then add both tests to `class MigrationTest`:

```python
    def test_004_moves_leftovers_onto_the_session_that_made_them(self):
        from app.db import connect, migrate
        conn = connect(self.path)
        try:
            apply_up_to(conn, 3)
            both = {"sessions": {"tue": {"date": "2026-09-29", "recipes": {}},
                                 "fri": {"date": "2026-10-02", "recipes": {}}},
                    "leftovers": ["About 170 g Greek yogurt", "2 eggs"]}
            tue_only = {"sessions": {"tue": {"date": "2026-10-06", "recipes": {}}},
                        "leftovers": ["About 80 g Parmesan"]}
            fri_only = {"sessions": {"fri": {"date": "2026-10-16", "recipes": {}}}}
            for week, data in (("2026-09-28", both), ("2026-10-05", tue_only), ("2026-10-12", fri_only)):
                conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES (?, ?, 'x')",
                             (week, json.dumps(data)))

            self.assertGreaterEqual(migrate(conn), 4)

            rows = {r["week"]: json.loads(r["data"]) for r in conn.execute("SELECT week, data FROM meal_plans")}
            # Friday is the week's last cook, so a week with both sessions hands its leftovers to Friday.
            self.assertEqual(rows["2026-09-28"]["sessions"]["fri"]["leftovers"],
                             ["About 170 g Greek yogurt", "2 eggs"])
            self.assertEqual(rows["2026-09-28"]["sessions"]["tue"]["leftovers"], [])
            self.assertEqual(rows["2026-10-05"]["sessions"]["tue"]["leftovers"], ["About 80 g Parmesan"])
            self.assertEqual(rows["2026-10-12"]["sessions"]["fri"]["leftovers"], [])
            for week, data in rows.items():
                self.assertNotIn("leftovers", data, week)  # the week-level list is gone
                for name, sess in data["sessions"].items():
                    self.assertIsInstance(sess["leftovers"], list, f"{week} {name}")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_004_drops_ai_jobs_and_adds_the_oauth_tables(self):
        from app.db import connect, migrate
        conn = connect(self.path)
        try:
            apply_up_to(conn, 3)
            conn.execute("INSERT INTO meal_plans (week, data, created_at) VALUES (?, ?, 'x')",
                         ("2026-09-28", '{"sessions": {}}'))
            conn.execute("INSERT INTO shopping_items (week, id, item, buy, for_session) "
                         "VALUES ('2026-09-28', 's01', 'Oats', '1 bag', 'both')")
            conn.execute("INSERT INTO ai_jobs (week, status, created_at) VALUES ('2026-09-28', 'done', 'x')")

            self.assertGreaterEqual(migrate(conn), 4)

            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("ai_jobs", names)
            for table in ("oauth_clients", "oauth_codes", "oauth_tokens"):
                self.assertIn(table, names)
            for index in ("ix_oauth_tokens_family", "ix_oauth_tokens_user"):
                self.assertIn(index, names)
            # A pre-004 row with for_session = 'both' is still legal and still readable.
            row = conn.execute("SELECT * FROM shopping_items WHERE week = '2026-09-28' AND id = 's01'").fetchone()
            self.assertEqual(row["for_session"], "both")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_migration -v`
Expected: both new tests FAIL with `AssertionError: 3 not greater than or equal to 4` (no 004 file yet). The four existing tests pass.

- [ ] **Step 3: Write the migration**

Create `migrations/004_oauth.sql` with exactly this:

```sql
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
```

- [ ] **Step 4: Run the migration tests to verify they pass**

Run: `python3 -m unittest tests.test_migration -v`
Expected: 6 tests, `OK`.

- [ ] **Step 5: Teach `store.py` the per-session shape**

In `app/store.py`, replace `save_plan` and `get_plan` with:

```python
def save_plan(conn: sqlite3.Connection, week: str, plan: dict, source: str, note: str, user_id: int | None,
              got: dict | None = None) -> None:
    """Replace a week's menu and shopping list from a seed file. Call inside a transaction.

    Only `python -m app.cli import-seed` uses this now: the seed JSON has a week-level "leftovers"
    list and items marked for: "both". Leftovers move onto the week's last cook — Friday when it is
    present, otherwise Tuesday — the same rule migration 004 applies, so importing the seed and
    migrating an old database give the same shape.
    """
    sessions = {k: dict(v) for k, v in (plan.get("sessions") or {}).items() if isinstance(v, dict)}
    last = "fri" if "fri" in sessions else "tue"
    for k, sess in sessions.items():
        sess["leftovers"] = list(sess.get("leftovers") or (plan.get("leftovers", []) if k == last else []))
    data = {"sessions": sessions}
    conn.execute("DELETE FROM meal_plans WHERE week = ?", (week,))  # cascades shopping_items
    conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
                 (week, json.dumps(data), source, note, now_iso(), user_id))
    got = got or {}
    for n, i in enumerate(plan.get("shopping", [])):
        conn.execute(
            """INSERT INTO shopping_items (week, id, item, buy, aisle, for_session, stock, search, got, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (week, i["id"], i["item"], i.get("buy", ""), i.get("aisle", "Pantry"), i.get("for", "both"),
             1 if i.get("stock") else 0, i.get("search", ""), 1 if got.get(i["id"]) else 0, n),
        )


def get_plan(conn: sqlite3.Connection, week: str, include_shopping: bool) -> dict | None:
    row = conn.execute("SELECT * FROM meal_plans WHERE week = ?", (week,)).fetchone()
    if not row:
        return None
    data = json.loads(row["data"])
    sessions = data.get("sessions") or {}
    for sess in sessions.values():
        # Leftovers belong to the session that produced them (migration 004). A row written before
        # that still reads cleanly.
        if isinstance(sess, dict):
            sess.setdefault("leftovers", [])
    plan = {"week": week, "source": row["source"], "note": row["note"], "createdAt": row["created_at"],
            "sessions": sessions}
    if include_shopping:
        items = conn.execute("SELECT * FROM shopping_items WHERE week = ? ORDER BY sort_order", (week,)).fetchall()
        plan["shopping"] = [{"id": r["id"], "item": r["item"], "buy": r["buy"], "aisle": r["aisle"],
                             "for": r["for_session"], "stock": bool(r["stock"]), "search": r["search"]}
                            for r in items]
        plan["got"] = {r["id"]: True for r in items if r["got"]}
    return plan
```

- [ ] **Step 6: Prove the seed import still works, through the API**

Add this to `tests/test_api.py`, immediately after `test_shopping_and_fav`:

```python
    def test_the_seed_puts_its_leftovers_on_the_friday_session(self):
        self.login("owen")
        plan = self.c.get("/api/plans/2026-09-28").json()["plan"]
        self.assertNotIn("leftovers", plan)  # never at the top of a plan any more
        self.assertEqual(plan["sessions"]["tue"]["leftovers"], [])
        self.assertIn("About 170 g Greek yogurt", plan["sessions"]["fri"]["leftovers"])
```

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 92 tests` … `OK` (89 + 2 migration + 1 API).

- [ ] **Step 8: Import the seed into a genuinely fresh database by hand**

Run:

```bash
rm -rf /tmp/hrs-seed-check && HRS_DB_PATH=/tmp/hrs-seed-check/house.db python3 -m app.cli import-seed seed/
python3 - <<'PY'
import json, sqlite3
c = sqlite3.connect("/tmp/hrs-seed-check/house.db")
print("version", c.execute("PRAGMA user_version").fetchone()[0])
d = json.loads(c.execute("SELECT data FROM meal_plans WHERE week = '2026-09-28'").fetchone()[0])
print("top-level leftovers?", "leftovers" in d)
print("tue", d["sessions"]["tue"]["leftovers"])
print("fri", len(d["sessions"]["fri"]["leftovers"]), "lines")
PY
```

Expected exactly:

```
Tasks: 41
Settings: imported
Meal plans: 1
version 4
top-level leftovers? False
tue []
fri 6 lines
```

- [ ] **Step 9: Commit**

```bash
git add migrations/004_oauth.sql app/store.py tests/test_migration.py tests/test_api.py
git commit -m "Add the OAuth tables and move a week's leftovers onto its sessions" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `app/menu.py` — the rules, the prep-day calendar and the brief

**Files:**
- Modify: `app/menu.py` (add to the module Task 1 left behind)
- Create: `tests/test_menu.py`

**Interfaces:**
- Consumes: `menu.AISLES`, `menu.SLOTS` = `("breakfast", "main", "dessert")`, `menu.add_days(d, n)`; `store.get_settings(conn) -> dict` (keys `kcal`, `protein`, `store`, `likes`, `dislikes`, `pantry`, and `tue`/`fri` each with `breakfast`, `main`, `dessert`, `covers`); `store.get_plan`'s per-session `leftovers` from Task 2; `app.db.connect`.
- Produces:
  - `menu.MenuError(Exception)` — its `str()` is the single line that goes back to Claude as a tool error.
  - `menu.RULES: str`, `menu.WEEKDAYS: tuple[str, ...]`, `menu.MONTHS: tuple[str, ...]`, `menu.GET_BRIEF_SCHEMA: dict`.
  - `menu.today_la() -> datetime.date`, `menu.resolve_prep_date(given: str | None) -> str`, `menu.week_and_session(prep_date: str) -> tuple[str, str]` (`(week_monday, "tue" | "fri")`), `menu.brief(conn, date: str | None) -> dict` with exactly the keys `week, session, date, covers, portions, targets, store, likes, dislikes, pantry, existing, otherSession, recentTitles, favourites, rules`.
  - `tests/test_menu.py` with `class MenuTest` whose `setUp` imports `seed/` into a temp DB and leaves `self.conn` open, and helpers `self.write_plan(week, titles, leftovers=None)`.

**Context:** Owen is in Los Angeles and the container runs UTC, so a UTC "today" would be a day ahead all evening — hence `today_la()`. `ZoneInfo("America/Los_Angeles")` is present in `python:3.12-slim`, so **no `tzdata` package is needed**. Weekday and month names come from module-level tuples, not `strftime("%A")`/`%b`, so they cannot change with the process locale and `%-d` (unportable) never appears.

`brief()` reuses the `ORDER BY week DESC` walk the deleted `ai.recent_context` used.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_menu.py`:

```python
"""The brief, the prep-day calendar and session validation. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
ENV_KEYS = ("HRS_DB_PATH", "HRS_PUBLIC_URL")


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "menu.db")
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
        from app import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["import-seed", str(ROOT / "seed")])  # migrates, then loads seed/plans/2026-09-28.json
        from app.db import connect
        self.conn = connect()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def write_plan(self, week: str, titles: dict[str, dict[str, str]], leftovers: list[str] | None = None) -> None:
        """Write a minimal saved week straight into meal_plans. titles = {"tue": {"main": "Chilli"}}."""
        from app import menu
        from app.db import now_iso
        sessions = {
            session: {"date": menu.add_days(week, 1 if session == "tue" else 4), "covers": "", "timeline": [],
                      "leftovers": list(leftovers or []),
                      "recipes": {slot: {"title": t, "portions": 1, "ingredients": [], "steps": [], "fav": False}
                                  for slot, t in slot_titles.items()}}
            for session, slot_titles in titles.items()}
        self.conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at) VALUES (?, ?, 'test', '', ?)",
                          (week, json.dumps({"sessions": sessions}), now_iso()))

    # ---- the prep-day calendar ----
    def test_resolve_prep_date_finds_the_next_tuesday_or_friday(self):
        from app import menu
        cases = [(date(2026, 9, 28), "2026-09-29", "2026-09-28", "tue"),   # Monday -> that Tuesday
                 (date(2026, 9, 29), "2026-09-29", "2026-09-28", "tue"),   # Tuesday -> today counts
                 (date(2026, 9, 30), "2026-10-02", "2026-09-28", "fri"),   # Wednesday -> that Friday
                 (date(2026, 10, 3), "2026-10-06", "2026-10-05", "tue")]   # Saturday -> the next Tuesday
        for today, prep, week, session in cases:
            with self.subTest(today=today.isoformat()), mock.patch("app.menu.today_la", return_value=today):
                self.assertEqual(menu.resolve_prep_date(None), prep)
                self.assertEqual(menu.week_and_session(prep), (week, session))

    def test_resolve_prep_date_keeps_a_tuesday_or_friday_it_is_given(self):
        from app import menu
        with mock.patch("app.menu.today_la", return_value=date(2026, 9, 28)):
            self.assertEqual(menu.resolve_prep_date("2026-10-16"), "2026-10-16")  # a Friday three weeks out

    def test_resolve_prep_date_rejects_a_day_that_is_not_a_prep_day(self):
        from app import menu
        with self.assertRaises(menu.MenuError) as cm:
            menu.resolve_prep_date("2026-09-30")
        self.assertEqual(str(cm.exception),
                         "2026-09-30 is a Wednesday. Prep sessions are Tuesdays and Fridays — pick one of those.")

    def test_resolve_prep_date_rejects_a_non_date(self):
        from app import menu
        with self.assertRaises(menu.MenuError) as cm:
            menu.resolve_prep_date("2026-13-01")
        self.assertEqual(str(cm.exception), '"2026-13-01" is not a date in YYYY-MM-DD form.')

    # ---- the brief ----
    def test_brief_reads_the_settings_and_the_date(self):
        from app import menu
        with mock.patch("app.menu.today_la", return_value=date(2026, 9, 28)):
            b = menu.brief(self.conn, None)
        self.assertEqual(b["week"], "2026-09-28")
        self.assertEqual(b["session"], "tue")
        self.assertEqual(b["date"], "2026-09-29")
        self.assertEqual(b["covers"], "Wed, Thu, Fri")
        self.assertEqual(b["portions"], {"breakfast": 3, "main": 9, "dessert": 3})
        self.assertEqual(b["targets"], {"kcal": 500, "protein": 50,
                                        "tolerance": {"kcalPercent": 7, "proteinBelow": 3}})
        self.assertEqual(b["store"], "")
        self.assertEqual(b["likes"], "")
        self.assertEqual(b["dislikes"], "")
        self.assertEqual(b["pantry"], "")
        self.assertEqual(b["rules"], menu.RULES)

    def test_brief_reports_the_saved_session_and_the_other_one(self):
        from app import menu
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(b["existing"]["titles"]["main"], "Chipotle chicken burrito bowls")
        self.assertEqual(list(b["existing"]["titles"]), ["breakfast", "main", "dessert"])
        self.assertEqual(b["otherSession"]["session"], "fri")
        self.assertEqual(b["otherSession"]["date"], "2026-10-02")
        self.assertEqual(b["otherSession"]["titles"]["breakfast"],
                         "Chocolate peanut butter banana overnight oats")
        self.assertIn("About 170 g Greek yogurt", b["otherSession"]["leftovers"])
        # a week nothing is saved for has neither
        empty = menu.brief(self.conn, "2026-10-06")
        self.assertIsNone(empty["existing"])
        self.assertIsNone(empty["otherSession"])

    def test_brief_lists_six_earlier_weeks_of_titles(self):
        from app import menu
        for week in ("2026-09-21", "2026-09-14", "2026-09-07", "2026-08-31", "2026-08-24",
                     "2026-08-17", "2026-08-10"):
            self.write_plan(week, {"tue": {"main": f"Main {week}"}})
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(len(b["recentTitles"]), 6)                      # six weeks, newest first
        self.assertEqual(b["recentTitles"][0], "Main 2026-09-21")
        self.assertEqual(b["recentTitles"][-1], "Main 2026-08-17")
        self.assertNotIn("Main 2026-08-10", b["recentTitles"])           # the seventh week is dropped
        self.assertNotIn("Chipotle chicken burrito bowls", b["recentTitles"])  # its own week never counts

    def test_brief_lists_favourites(self):
        from app import store
        from app import menu
        from app.db import tx
        with tx(self.conn):
            store.set_recipe_fav(self.conn, "2026-09-28", "tue", "main", True)
        b = menu.brief(self.conn, "2026-09-29")
        self.assertEqual(b["favourites"], ["Chipotle chicken burrito bowls"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_menu -v`
Expected: all 8 FAIL with `AttributeError: module 'app.menu' has no attribute 'MenuError'` / `'resolve_prep_date'` / `'brief'`.

- [ ] **Step 3: Write the implementation**

Replace the whole of `app/menu.py` with this — it is Task 1's four symbols plus everything new, so there is nothing to merge by hand:

```python
"""The menu brief, the rules Claude follows, and validation of a posted prep session.

Menus are planned in the Claude app and posted back through the MCP connector (app/mcp.py).
Nothing in this module calls a model or talks to the network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import store

AISLES = ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"]
SLOTS = ("breakfast", "main", "dessert")
LA = ZoneInfo("America/Los_Angeles")  # present in python:3.12-slim; no tzdata package needed
# Written out rather than taken from strftime("%A"/"%b"), which follows the process locale.
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
PREP_WEEKDAYS = (1, 4)  # Tuesday, Friday


class MenuError(Exception):
    """A message that goes straight back to Claude as a tool error. One line, no stack trace."""


RULES = """You are writing one batch-prep session for a housekeeper who cooks for one adult in Los Angeles. \
The brief gives you the day, the portion counts, the targets and the household's preferences. Work the \
numbers out in the chat with Owen, then post the finished session with save_session.

- Every single portion - breakfast, main and dessert alike - must hit the brief's kcal target within the \
stated tolerance and reach at least its protein target. The app checks this on save and refuses an \
off-target session.
- Ingredient amounts are for the WHOLE BATCH, in US units with grams in brackets where useful. "kcal" and \
"protein" are for that whole-batch amount of that ingredient, taken from the real product's nutrition \
label or the USDA figure - look them up rather than estimating, and use the exact brand the household buys.
- Before you call save_session, add the ingredients up yourself: total kcal divided by portions, and total \
protein divided by portions, for each of the three recipes. Adjust amounts until both land on target. Never \
post a session you have not added up.
- Quick and simple: the cooking fits in about 2.5 hours for one person with normal home-kitchen equipment. \
Only everyday ingredients found at a normal US supermarket (the brief's "store" if it names one).
- Minimise waste: size quantities to standard US pack sizes and use whole packs where you can; prefer \
frozen or shelf-stable forms for small amounts.
- Use the other session's leftovers first where it makes sense. The brief lists them under "otherSession".
- Do not put anything from the brief's "pantry" list on the shopping list.
- Respect "likes". Never use anything in "dislikes".
- Do not repeat a title from "recentTitles". You may reuse at most one title from "favourites".
- The shopping list is ordered on Amazon Fresh (amazon.com), so word every "item" the way Amazon Fresh \
sells it: brand where it matters and a real pack size, like "Fage Total 0% Greek Yogurt, 32 oz", "Amazon \
Grocery 93/7 Ground Beef, 1 lb" or "Just Bare Chicken Breast Tenderloins, 2 lb". "buy" is how many of that \
pack to order. "search" is a short Amazon Fresh search phrase for that item - brand, product and size, with \
no quantity like "x2" - that lands on the right product.
- "stock" is true only for long-life items that last several weeks (oils, spices, oats, rice, protein \
powder, baking goods). Everything else is false.
- Food safety: any portion eaten more than 3 days after it was cooked must be frozen on prep day and moved \
to the fridge the night before. Say so in that recipe's "storage" and "portionNote".
- Steps are short plain sentences a cook can follow: oven temperatures, times, doneness (chicken to 165F), \
how to split into portions evenly (by weight), and labelling each container with the day to eat it.
- The timeline is 4 to 6 lines ordering the session's work efficiently - things that chill or bake go first.
- "leftovers" is the realistic amounts of anything left over after THIS session, so the next session can \
use them up."""

GET_BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "description": "The prep day to plan, a Tuesday or a Friday, as YYYY-MM-DD. Omit for the next one.",
        }
    },
    "required": [],
    "additionalProperties": False,
}


def add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


def today_la() -> date:
    """Owen's today. The container runs UTC, so a UTC date would be a day ahead all evening."""
    return datetime.now(LA).date()


def resolve_prep_date(given: str | None) -> str:
    """The prep day to plan: the next Tuesday or Friday on or after today, or the one asked for."""
    if not given:
        d = today_la()
        while d.weekday() not in PREP_WEEKDAYS:
            d += timedelta(days=1)
        return d.isoformat()
    try:
        d = date.fromisoformat(given)
    except ValueError:
        raise MenuError(f'"{given}" is not a date in YYYY-MM-DD form.')
    if d.weekday() not in PREP_WEEKDAYS:
        raise MenuError(f"{d.isoformat()} is a {WEEKDAYS[d.weekday()]}. "
                        f"Prep sessions are Tuesdays and Fridays — pick one of those.")
    return d.isoformat()


def week_and_session(prep_date: str) -> tuple[str, str]:
    """(Monday of that week, "tue" | "fri") for a prep day."""
    d = date.fromisoformat(prep_date)
    return (d - timedelta(days=d.weekday())).isoformat(), "tue" if d.weekday() == 1 else "fri"


def _titles(session: dict) -> dict[str, str]:
    """{slot: title} for the slots a saved session actually has, in SLOTS order."""
    recipes = session.get("recipes") or {}
    return {slot: str(recipes[slot].get("title") or "") for slot in SLOTS
            if isinstance(recipes.get(slot), dict)}


def brief(conn, date: str | None) -> dict:
    """Everything Claude needs to plan one session. Raises MenuError for a day that is not a prep day."""
    prep = resolve_prep_date(date)
    week, session = week_and_session(prep)
    other = "fri" if session == "tue" else "tue"
    settings = store.get_settings(conn)
    rows = conn.execute("SELECT week, data FROM meal_plans ORDER BY week DESC").fetchall()
    earlier = {r["week"] for r in rows if r["week"] < week}
    earlier = set(sorted(earlier, reverse=True)[:6])  # the six most recent saved weeks before this one
    recent: list[str] = []
    favourites: list[str] = []
    this_week: dict = {}
    for r in rows:  # newest week first
        d = json.loads(r["data"])
        if r["week"] == week:
            this_week = d
        for s in (d.get("sessions") or {}).values():
            for rec in (s.get("recipes") or {}).values():
                title = str((rec or {}).get("title") or "")
                if not title:
                    continue
                if r["week"] in earlier and title not in recent:
                    recent.append(title)
                if rec.get("fav") and title not in favourites:
                    favourites.append(title)
    sessions = this_week.get("sessions") or {}
    mine, theirs = sessions.get(session), sessions.get(other)
    existing = None
    if isinstance(mine, dict) and _titles(mine):
        existing = {"titles": _titles(mine)}
    other_session = None
    if isinstance(theirs, dict) and _titles(theirs):
        other_session = {"session": other, "date": add_days(week, 1 if other == "tue" else 4),
                         "titles": _titles(theirs),
                         "leftovers": [str(x) for x in (theirs.get("leftovers") or [])]}
    return {
        "week": week, "session": session, "date": prep,
        "covers": settings[session]["covers"],
        "portions": {slot: int(settings[session][slot]) for slot in SLOTS},
        "targets": {"kcal": int(settings["kcal"]), "protein": int(settings["protein"]),
                    "tolerance": {"kcalPercent": 7, "proteinBelow": 3}},
        "store": settings.get("store") or "", "likes": settings.get("likes") or "",
        "dislikes": settings.get("dislikes") or "", "pantry": settings.get("pantry") or "",
        "existing": existing, "otherSession": other_session,
        "recentTitles": recent, "favourites": favourites, "rules": RULES,
    }
```

Note the shadowing: `brief(conn, date)`'s parameter hides the `date` class inside that function, which is why `brief` calls `resolve_prep_date`/`week_and_session`/`add_days` instead of `date.fromisoformat` directly. Keep the parameter name — the tool's argument is `date` and the spec's signature is `menu.brief(conn, date)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v`
Expected: 8 tests, `OK`.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 100 tests` … `OK` (92 + 8).

- [ ] **Step 6: Commit**

```bash
git add app/menu.py tests/test_menu.py
git commit -m "Add the household brief, the menu rules and the prep-day calendar" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: `app/menu.py` — normalization, validation and the one-session merge

**Files:**
- Modify: `app/menu.py` (append; nothing written in Task 3 changes)
- Modify: `app/store.py` (add `save_session`)
- Modify: `tests/test_menu.py` (helpers + 11 tests)

**Interfaces:**
- Consumes: `menu.AISLES`, `menu.SLOTS`, `menu.MenuError`, `menu.add_days`, `menu.WEEKDAYS`, `menu.MONTHS` (Task 3); `store.get_settings(conn) -> dict`; `store.get_plan(conn, week, include_shopping)` with per-session `leftovers` (Task 2); `app.db.tx(conn)`, `app.db.now_iso()`; `config.get_config().public_url` (Task 1).
- Produces:
  - `menu.SAVE_SESSION_SCHEMA: dict`.
  - `menu.normalize_session(args) -> tuple[dict, list[dict]]` — `(normalized, shopping)`. `normalized` has keys `week: str`, `session: str`, `recipes: dict[str, dict]` (only the slots that arrived as objects; each recipe has `title, blurb, portions: int, portionNote, ingredients: list[dict], steps: list[str], storage, fav: False`), `timeline: list[str]`, `shopping: list[dict]`, `leftovers: list[str]`. The second element **is** `normalized["shopping"]` — the same list, with `id` already assigned — so it can be handed straight to `store.save_session`. Raises `MenuError` for a wrong top-level shape.
  - `menu.validate_session(normalized: dict, settings: dict) -> list[str]` — every problem, one line each, in field order. Empty list means save it.
  - `menu.problem_report(problems: list[str]) -> str` — the heading plus one line per problem.
  - `menu.session_data(normalized: dict, settings: dict) -> dict` — the JSON stored at `meal_plans.data["sessions"][session]`: `date, covers, recipes, timeline, leftovers`.
  - `menu.save_result(week: str, session: str, session_data: dict, shopping: list[dict]) -> tuple[str, dict]` — the success summary text and the `structuredContent` object.
  - `store.save_session(conn, week: str, session: str, session_data: dict, shopping: list[dict], user_id: int | None) -> None` — call inside `tx(conn)`.

**Context:** `normalize_session` **coerces but never truncates**: a too-long string is reported by `validate_session` so Claude can shorten it itself, and an `aisle` outside `AISLES` is silently changed to `"Pantry"` (not a problem — rejecting it would cause a pointless retry). Unknown keys are ignored; `additionalProperties: false` already tells Claude not to send them.

The macro rule is identical to `frontend/src/lib/macros.ts: macroStatus` — both sides rounded to whole numbers for the comparison *and* for the message, so an error line and the pill the Meals tab draws can never disagree. `portions` is **not** checked against the brief's portion counts: Owen may want more or fewer than the setting says.

Per-item checks stop at the cap (`[:40]`, `[:30]`, `[:12]`, `[:60]`, `[:20]`) so a 200-item list produces one "too many" line, not 200.

- [ ] **Step 1: Write the failing tests**

Add these helpers to `class MenuTest` in `tests/test_menu.py`, after `write_plan`:

```python
    def recipe(self, title: str, portions: int, kcal: float, protein: float) -> dict:
        """One recipe whose whole-batch numbers divide exactly into kcal and protein per portion."""
        return {"title": title, "blurb": "One short line.", "portions": portions,
                "portionNote": f"{portions} containers, one a day.",
                "ingredients": [{"item": "Everything", "amount": "1 batch (1000 g)",
                                 "kcal": kcal * portions, "protein": protein * portions}],
                "steps": ["Cook it.", "Portion it out by weight."], "storage": "Fridge for 3 days."}

    def payload(self, **overrides) -> dict:
        """A valid save_session argument object. Override any top-level key."""
        args = {"week": "2026-10-05", "session": "tue",
                "recipes": {"breakfast": self.recipe("Vanilla blueberry overnight oats", 3, 500, 52),
                            "main": self.recipe("Beef burritos", 9, 500, 51),
                            "dessert": self.recipe("Chocolate overnight oats", 3, 500, 49)},
                "timeline": ["Start the oats.", "Brown the beef.", "Roll the burritos.", "Label everything."],
                "shopping": [{"item": "Amazon Grocery 93/7 Ground Beef, 1 lb", "buy": "2 lb", "aisle": "Meat",
                              "stock": False, "search": "Amazon Grocery 93/7 ground beef 1 lb"},
                             {"item": "Mission Carb Balance Tortillas, 8 ct", "buy": "1 pack", "aisle": "Bakery",
                              "stock": False, "search": "Mission Carb Balance flour tortillas"}],
                "leftovers": ["About 170 g Greek yogurt"]}
        args.update(overrides)
        return args

    def seed_payload(self, session: str = "tue") -> dict:
        """A save_session argument object built from the real seed week, so it is realistic prose."""
        seed = json.loads((ROOT / "seed" / "plans" / "2026-09-28.json").read_text())
        s = seed["sessions"][session]
        return {
            "week": "2026-09-28", "session": session,
            "recipes": {slot: {"title": r["title"], "blurb": r["blurb"], "portions": r["portions"],
                               "portionNote": r["portionNote"], "storage": r["storage"], "steps": r["steps"],
                               "ingredients": [{"item": i["item"], "amount": i["amount"],
                                                "kcal": i["kcal"], "protein": i["protein"]}
                                               for i in r["ingredients"]]}
                        for slot, r in s["recipes"].items()},
            "timeline": s["timeline"],
            "shopping": [{"item": i["item"], "buy": i["buy"], "aisle": i["aisle"], "stock": i["stock"],
                          "search": i.get("search", "")}
                         for i in seed["shopping"] if i["for"] in (session, "both")],
            "leftovers": ["About 170 g Greek yogurt"],
        }
```

Then add these 11 tests to `class MenuTest`:

```python
    # ---- normalization ----
    def test_normalize_trims_coerces_and_numbers_the_shopping_list(self):
        from app import menu, store
        args = self.payload(session="fri", shopping=[
            {"item": "  Fage Total 0% Greek Yogurt, 32 oz  ", "buy": " 1 ", "aisle": "Deli counter",
             "stock": "yes", "search": " Fage Total 0% 32 oz "},
            {"item": "Old-fashioned oats", "buy": "1 bag", "aisle": "Pantry", "stock": True, "search": "oats"}])
        args["recipes"]["main"]["portions"] = "9"          # a string, the way a sloppy client might send it
        normalized, shopping = menu.normalize_session(args)
        self.assertIs(shopping, normalized["shopping"])    # the same list, ready for store.save_session
        self.assertEqual([i["id"] for i in shopping], ["f01", "f02"])  # f… for Friday, t… for Tuesday
        self.assertEqual(shopping[0]["item"], "Fage Total 0% Greek Yogurt, 32 oz")
        self.assertEqual(shopping[0]["buy"], "1")
        self.assertEqual(shopping[0]["aisle"], "Pantry")   # an unknown aisle is coerced, not refused
        self.assertIs(shopping[0]["stock"], True)
        self.assertEqual(shopping[0]["search"], "Fage Total 0% 32 oz")
        self.assertEqual(normalized["recipes"]["main"]["portions"], 9)
        self.assertIs(normalized["recipes"]["main"]["fav"], False)
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])

    def test_normalize_rejects_a_wrong_shape(self):
        from app import menu
        expected = "save_session needs an object with week, session, recipes, timeline, shopping and leftovers."
        for bad in ("not an object", self.payload(recipes=[]), self.payload(timeline="four lines"),
                    self.payload(shopping={}), self.payload(leftovers=None)):
            with self.subTest(bad=type(bad).__name__):
                with self.assertRaises(menu.MenuError) as cm:
                    menu.normalize_session(bad)
                self.assertEqual(str(cm.exception), expected)

    # ---- validation ----
    def test_a_good_session_from_the_seed_has_no_problems(self):
        from app import menu, store
        normalized, _ = menu.normalize_session(self.seed_payload("tue"))
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])
        normalized, _ = menu.normalize_session(self.seed_payload("fri"))
        self.assertEqual(menu.validate_session(normalized, store.get_settings(self.conn)), [])

    def test_off_target_kcal_and_protein_are_reported(self):
        from app import menu, store
        args = self.payload(recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                     "main": self.recipe("Burritos", 9, 612, 51),
                                     "dessert": self.recipe("Pots", 3, 500, 44)})
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        self.assertEqual(problems, ["main: 612 kcal per portion, target 500 ±35.",
                                    "dessert: 44 g protein per portion, need at least 47."])

    def test_missing_and_empty_fields_are_reported(self):
        from app import menu, store
        args = self.payload()
        del args["recipes"]["dessert"]
        args["recipes"]["breakfast"]["title"] = "   "
        args["recipes"]["main"]["portions"] = 0
        args["recipes"]["main"]["ingredients"] = []
        args["recipes"]["main"]["steps"] = []
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        for line in ("recipes: dessert is missing.", "breakfast: title is empty.",
                     "main: portions must be 1 or more.", "main: needs at least one ingredient.",
                     "main: needs at least one step."):
            self.assertIn(line, problems)

    def test_length_caps_are_reported(self):
        from app import menu, store
        args = self.payload(
            timeline=[f"Line {n}." for n in range(13)],
            shopping=[{"item": f"Item {n}", "buy": "1", "aisle": "Pantry", "stock": False, "search": f"item {n}"}
                      for n in range(61)],
            leftovers=[f"About {n} g of something" for n in range(21)])
        args["recipes"]["main"]["title"] = "B" * 121
        args["recipes"]["dessert"]["portions"] = 41
        args["recipes"]["dessert"]["ingredients"] = [{"item": "Thing", "amount": "1 g", "kcal": 500, "protein": 51}
                                                    for _ in range(41)]
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        for line in ("main: title is too long (120 characters max).",
                     "dessert: too many ingredients (40 max).",
                     "timeline: too many lines (12 max).",
                     "shopping: too many items (60 max).",
                     "leftovers: too many lines (20 max)."):
            self.assertIn(line, problems)
        # one "too many" line, not one per item over the cap
        self.assertEqual(len([p for p in problems if p.startswith("shopping item ")]), 0)

    def test_a_week_that_is_not_a_monday_is_reported(self):
        from app import menu, store
        normalized, _ = menu.normalize_session(self.payload(week="2026-09-30"))
        self.assertIn("week: 2026-09-30 is a Wednesday; a week is identified by its Monday (2026-09-28).",
                      menu.validate_session(normalized, store.get_settings(self.conn)))
        normalized, _ = menu.normalize_session(self.payload(week="2026-13-01"))
        self.assertIn('week: "2026-13-01" is not a date in YYYY-MM-DD form.',
                      menu.validate_session(normalized, store.get_settings(self.conn)))

    def test_several_problems_are_listed_under_one_heading(self):
        from app import menu, store
        args = self.payload(week="2026-09-30", session="wed",
                            recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                     "main": self.recipe("Burritos", 9, 612, 51),
                                     "dessert": self.recipe("Pots", 3, 500, 44)})
        normalized, _ = menu.normalize_session(args)
        problems = menu.validate_session(normalized, store.get_settings(self.conn))
        self.assertEqual(menu.problem_report(problems), (
            "Nothing was saved. Fix these and call save_session again:\n"
            "week: 2026-09-30 is a Wednesday; a week is identified by its Monday (2026-09-28).\n"
            'session: must be "tue" or "fri".\n'
            "main: 612 kcal per portion, target 500 ±35.\n"
            "dessert: 44 g protein per portion, need at least 47."))

    # ---- saving ----
    def test_save_session_leaves_the_other_session_alone(self):
        from app import menu, store
        from app.db import tx
        settings = store.get_settings(self.conn)
        week = "2026-10-05"
        fri = self.payload(week=week, session="fri",
                           recipes={"breakfast": self.recipe("Berry oats", 4, 500, 52),
                                    "main": self.recipe("Beef chilli", 12, 500, 51),
                                    "dessert": self.recipe("Cheesecake pots", 4, 500, 49)},
                           leftovers=["About 80 g Parmesan"])
        normalized, shopping = menu.normalize_session(fri)
        self.assertEqual(menu.validate_session(normalized, settings), [])
        with tx(self.conn):
            store.save_session(self.conn, week, "fri", menu.session_data(normalized, settings), shopping, None)
        self.assertEqual([i["id"] for i in shopping], ["f01", "f02"])
        self.conn.execute("UPDATE shopping_items SET got = 1 WHERE week = ? AND id = 'f01'", (week,))

        tue, tue_shopping = menu.normalize_session(self.payload(week=week))
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(tue, settings), tue_shopping, None)

        plan = store.get_plan(self.conn, week, include_shopping=True)
        self.assertEqual(sorted(plan["sessions"]), ["fri", "tue"])
        self.assertEqual(plan["sessions"]["fri"]["recipes"]["main"]["title"], "Beef chilli")
        self.assertEqual(plan["sessions"]["fri"]["leftovers"], ["About 80 g Parmesan"])
        self.assertEqual(plan["sessions"]["fri"]["covers"], "Sat, Sun, Mon, Tue")
        self.assertEqual(plan["sessions"]["fri"]["date"], "2026-10-09")
        self.assertEqual(plan["sessions"]["tue"]["date"], "2026-10-06")
        self.assertEqual({i["id"] for i in plan["shopping"]}, {"f01", "f02", "t01", "t02"})
        self.assertTrue(plan["got"].get("f01"))     # Friday's tick survived Tuesday's save
        self.assertFalse(plan["got"].get("t01"))    # new rows start unticked
        self.assertEqual(plan["source"], "Claude")

    def test_saving_a_session_twice_replaces_it_and_clears_the_stars(self):
        from app import menu, store
        from app.db import tx
        settings = store.get_settings(self.conn)
        week = "2026-10-05"
        first, first_shopping = menu.normalize_session(self.payload(week=week))
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(first, settings), first_shopping, None)
        with tx(self.conn):
            self.assertTrue(store.set_recipe_fav(self.conn, week, "tue", "main", True))
        again = self.payload(week=week,
                             recipes={"breakfast": self.recipe("Peach oats", 3, 500, 52),
                                      "main": self.recipe("Chicken bowls", 9, 500, 51),
                                      "dessert": self.recipe("Brownie pots", 3, 500, 49)},
                             shopping=[{"item": "Just Bare Chicken Breast Tenderloins, 2 lb", "buy": "1 bag",
                                        "aisle": "Meat", "stock": False,
                                        "search": "Just Bare chicken breast tenderloins 2 lb"}])
        second, second_shopping = menu.normalize_session(again)
        with tx(self.conn):
            store.save_session(self.conn, week, "tue", menu.session_data(second, settings), second_shopping, None)
        plan = store.get_plan(self.conn, week, include_shopping=True)
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["main"]["title"], "Chicken bowls")
        self.assertIs(plan["sessions"]["tue"]["recipes"]["main"]["fav"], False)  # new recipes, no old stars
        self.assertEqual([i["id"] for i in plan["shopping"]], ["t01"])

    def test_save_result_summarises_the_session(self):
        from app import menu, store
        settings = store.get_settings(self.conn)
        normalized, shopping = menu.normalize_session(self.payload(week="2026-09-28"))
        text, structured = menu.save_result("2026-09-28", "tue", menu.session_data(normalized, settings), shopping)
        self.assertEqual(text, "Saved Tuesday 29 Sep (week of 28 Sep): Vanilla blueberry overnight oats "
                               "(3 × 500 kcal / 52 g), Beef burritos (9 × 500 kcal / 51 g), "
                               "Chocolate overnight oats (3 × 500 kcal / 49 g). 2 shopping items. "
                               "Open http://testserver/#meals")
        self.assertEqual(structured, {
            "week": "2026-09-28", "session": "tue", "date": "2026-09-29",
            "recipes": {"breakfast": {"title": "Vanilla blueberry overnight oats", "portions": 3,
                                      "kcalPerPortion": 500, "proteinPerPortion": 52},
                        "main": {"title": "Beef burritos", "portions": 9,
                                 "kcalPerPortion": 500, "proteinPerPortion": 51},
                        "dessert": {"title": "Chocolate overnight oats", "portions": 3,
                                    "kcalPerPortion": 500, "proteinPerPortion": 49}},
            "shoppingCount": 2, "url": "http://testserver/#meals"})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_menu -v`
Expected: the 11 new tests FAIL with `AttributeError: module 'app.menu' has no attribute 'normalize_session'`; the 8 from Task 3 still pass.

- [ ] **Step 3: Write the validation code**

Append to `app/menu.py` (after `brief`), and add `from .config import get_config` to its imports:

```python
SAVE_SESSION_SCHEMA = {
    "type": "object",
    "properties": {
        "week": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$",
                 "description": "Monday of the week, from get_brief."},
        "session": {"type": "string", "enum": ["tue", "fri"]},
        "recipes": {
            "type": "object",
            "properties": {"breakfast": {"$ref": "#/$defs/recipe"},
                           "main": {"$ref": "#/$defs/recipe"},
                           "dessert": {"$ref": "#/$defs/recipe"}},
            "required": ["breakfast", "main", "dessert"],
            "additionalProperties": False,
        },
        "timeline": {"type": "array", "items": {"type": "string"},
                     "description": "4 to 6 lines ordering the session's work."},
        "shopping": {"type": "array", "items": {"$ref": "#/$defs/item"}},
        "leftovers": {"type": "array", "items": {"type": "string"},
                      "description": "Realistic amounts left over after this session."},
    },
    "required": ["week", "session", "recipes", "timeline", "shopping", "leftovers"],
    "additionalProperties": False,
    "$defs": {
        "recipe": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "blurb": {"type": "string", "description": "One short line."},
                "portions": {"type": "integer", "minimum": 1},
                "portionNote": {"type": "string",
                                "description": 'e.g. "9 containers: 3 a day for Wed, Thu, Fri".'},
                "ingredients": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item": {"type": "string"},
                            "amount": {"type": "string",
                                       "description": "WHOLE BATCH amount, US units with grams in brackets."},
                            "kcal": {"type": "number", "minimum": 0,
                                     "description": "kcal in that whole-batch amount."},
                            "protein": {"type": "number", "minimum": 0,
                                        "description": "grams of protein in that whole-batch amount."},
                        },
                        "required": ["item", "amount", "kcal", "protein"],
                        "additionalProperties": False,
                    },
                },
                "steps": {"type": "array", "items": {"type": "string"}},
                "storage": {"type": "string"},
            },
            "required": ["title", "blurb", "portions", "portionNote", "ingredients", "steps", "storage"],
            "additionalProperties": False,
        },
        "item": {
            "type": "object",
            "properties": {
                "item": {"type": "string",
                         "description": "Worded the way Amazon Fresh sells it, with a real pack size."},
                "buy": {"type": "string", "description": 'How many of that pack to order, e.g. "2 bags".'},
                "aisle": {"type": "string", "enum": list(AISLES)},
                "stock": {"type": "boolean", "description": "true only for long-life items."},
                "search": {"type": "string", "description": "Short Amazon Fresh search phrase, no quantity."},
            },
            "required": ["item", "buy", "aisle", "stock", "search"],
            "additionalProperties": False,
        },
    },
}

SHAPE_PROBLEM = "save_session needs an object with week, session, recipes, timeline, shopping and leftovers."
# Caps: long enough for a real session, short enough that one call can't fill the database.
MAX_TITLE, MAX_BLURB, MAX_NOTE, MAX_STORAGE = 120, 300, 300, 500
MAX_INGREDIENTS, MAX_STEPS, MAX_STEP = 40, 30, 500
MAX_ING_ITEM, MAX_ING_AMOUNT = 120, 120
MAX_TIMELINE, MAX_TIMELINE_LINE = 12, 300
MAX_SHOPPING, MAX_SHOP_ITEM, MAX_SHOP_BUY, MAX_SHOP_SEARCH = 60, 160, 60, 160
MAX_LEFTOVERS, MAX_LEFTOVER_LINE = 20, 160


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def normalize_session(args) -> tuple[dict, list[dict]]:
    """Coerce Claude's arguments into the shapes the app stores, and number the shopping list.

    Never truncates: validate_session reports anything too long so Claude can shorten it itself.
    Returns (normalized, normalized["shopping"]) — the second value goes straight to store.save_session.
    """
    if not isinstance(args, dict) or not isinstance(args.get("recipes"), dict) \
            or not isinstance(args.get("timeline"), list) or not isinstance(args.get("shopping"), list) \
            or not isinstance(args.get("leftovers"), list):
        raise MenuError(SHAPE_PROBLEM)
    session = _text(args.get("session"))
    recipes: dict[str, dict] = {}
    for slot in SLOTS:
        r = args["recipes"].get(slot)
        if not isinstance(r, dict):
            continue  # validate_session reports "recipes: <slot> is missing."
        try:
            portions = int(r.get("portions") or 0)
        except (TypeError, ValueError):
            portions = 0
        raw_ings = r.get("ingredients") if isinstance(r.get("ingredients"), list) else []
        raw_steps = r.get("steps") if isinstance(r.get("steps"), list) else []
        recipes[slot] = {
            "title": _text(r.get("title")), "blurb": _text(r.get("blurb")), "portions": portions,
            "portionNote": _text(r.get("portionNote")), "storage": _text(r.get("storage")),
            "ingredients": [{"item": _text(i.get("item")), "amount": _text(i.get("amount")),
                             "kcal": _num(i.get("kcal")), "protein": _num(i.get("protein"))}
                            for i in raw_ings if isinstance(i, dict)],
            "steps": [_text(x) for x in raw_steps],
            "fav": False,  # replacing a session resets its stars; the recipes are new
        }
    prefix = "f" if session == "fri" else "t"  # t01…/f01… — two digits is enough at a 60-item cap
    shopping = []
    for n, i in enumerate(x for x in args["shopping"] if isinstance(x, dict)):
        shopping.append({"id": f"{prefix}{n + 1:02d}", "item": _text(i.get("item")), "buy": _text(i.get("buy")),
                         "aisle": i["aisle"] if i.get("aisle") in AISLES else "Pantry",
                         "stock": bool(i.get("stock")), "search": _text(i.get("search"))})
    normalized = {"week": _text(args.get("week")), "session": session, "recipes": recipes,
                  "timeline": [_text(x) for x in args["timeline"]], "shopping": shopping,
                  "leftovers": [_text(x) for x in args["leftovers"]]}
    return normalized, shopping


def validate_session(normalized: dict, settings: dict) -> list[str]:
    """Every problem with a posted session, one line each, in field order. Empty list means save it."""
    problems: list[str] = []
    week = normalized["week"]
    try:
        d = date.fromisoformat(week)
    except ValueError:
        problems.append(f'week: "{week}" is not a date in YYYY-MM-DD form.')
    else:
        if d.weekday() != 0:
            monday = (d - timedelta(days=d.weekday())).isoformat()
            problems.append(f"week: {week} is a {WEEKDAYS[d.weekday()]}; "
                            f"a week is identified by its Monday ({monday}).")
    if normalized["session"] not in ("tue", "fri"):
        problems.append('session: must be "tue" or "fri".')
    t_kcal, t_protein = int(settings["kcal"]), int(settings["protein"])
    for slot in SLOTS:
        r = normalized["recipes"].get(slot)
        if r is None:
            problems.append(f"recipes: {slot} is missing.")
            continue
        if not r["title"]:
            problems.append(f"{slot}: title is empty.")
        elif len(r["title"]) > MAX_TITLE:
            problems.append(f"{slot}: title is too long ({MAX_TITLE} characters max).")
        if len(r["blurb"]) > MAX_BLURB:
            problems.append(f"{slot}: blurb is too long ({MAX_BLURB} characters max).")
        if len(r["portionNote"]) > MAX_NOTE:
            problems.append(f"{slot}: portionNote is too long ({MAX_NOTE} characters max).")
        if len(r["storage"]) > MAX_STORAGE:
            problems.append(f"{slot}: storage is too long ({MAX_STORAGE} characters max).")
        if r["portions"] < 1:
            problems.append(f"{slot}: portions must be 1 or more.")
        if not r["ingredients"]:
            problems.append(f"{slot}: needs at least one ingredient.")
        elif len(r["ingredients"]) > MAX_INGREDIENTS:
            problems.append(f"{slot}: too many ingredients ({MAX_INGREDIENTS} max).")
        if not r["steps"]:
            problems.append(f"{slot}: needs at least one step.")
        elif len(r["steps"]) > MAX_STEPS:
            problems.append(f"{slot}: too many steps ({MAX_STEPS} max).")
        # Per-item checks stop at the cap: one "too many" line beats two hundred "too long" ones.
        for n, i in enumerate(r["ingredients"][:MAX_INGREDIENTS], start=1):
            if len(i["item"]) > MAX_ING_ITEM:
                problems.append(f"{slot} ingredient {n}: item is too long ({MAX_ING_ITEM} characters max).")
            if len(i["amount"]) > MAX_ING_AMOUNT:
                problems.append(f"{slot} ingredient {n}: amount is too long ({MAX_ING_AMOUNT} characters max).")
            if i["kcal"] < 0:
                problems.append(f"{slot} ingredient {n}: kcal must be a number of 0 or more.")
            if i["protein"] < 0:
                problems.append(f"{slot} ingredient {n}: protein must be a number of 0 or more.")
        for n, step in enumerate(r["steps"][:MAX_STEPS], start=1):
            if len(step) > MAX_STEP:
                problems.append(f"{slot} step {n}: too long ({MAX_STEP} characters max).")
        if r["ingredients"] and r["portions"] >= 1:
            # Identical to frontend/src/lib/macros.ts: macroStatus. Both sides are rounded to whole
            # numbers for the comparison and for the message, so an error line and the Meals pill agree.
            kcal_pp = round(sum(i["kcal"] for i in r["ingredients"]) / r["portions"])
            protein_pp = round(sum(i["protein"] for i in r["ingredients"]) / r["portions"])
            if abs(kcal_pp - t_kcal) > t_kcal * 0.07:
                problems.append(f"{slot}: {kcal_pp} kcal per portion, target {t_kcal} ±{round(t_kcal * 0.07)}.")
            if protein_pp < t_protein - 3:
                problems.append(f"{slot}: {protein_pp} g protein per portion, need at least {t_protein - 3}.")
    if len(normalized["timeline"]) > MAX_TIMELINE:
        problems.append(f"timeline: too many lines ({MAX_TIMELINE} max).")
    for n, line in enumerate(normalized["timeline"][:MAX_TIMELINE], start=1):
        if len(line) > MAX_TIMELINE_LINE:
            problems.append(f"timeline line {n}: too long ({MAX_TIMELINE_LINE} characters max).")
    if len(normalized["shopping"]) > MAX_SHOPPING:
        problems.append(f"shopping: too many items ({MAX_SHOPPING} max).")
    for n, i in enumerate(normalized["shopping"][:MAX_SHOPPING], start=1):
        if len(i["item"]) > MAX_SHOP_ITEM:
            problems.append(f"shopping item {n}: item is too long ({MAX_SHOP_ITEM} characters max).")
        if len(i["buy"]) > MAX_SHOP_BUY:
            problems.append(f"shopping item {n}: buy is too long ({MAX_SHOP_BUY} characters max).")
        if len(i["search"]) > MAX_SHOP_SEARCH:
            problems.append(f"shopping item {n}: search is too long ({MAX_SHOP_SEARCH} characters max).")
    if len(normalized["leftovers"]) > MAX_LEFTOVERS:
        problems.append(f"leftovers: too many lines ({MAX_LEFTOVERS} max).")
    for n, line in enumerate(normalized["leftovers"][:MAX_LEFTOVERS], start=1):
        if len(line) > MAX_LEFTOVER_LINE:
            problems.append(f"leftovers line {n}: too long ({MAX_LEFTOVER_LINE} characters max).")
    return problems


def problem_report(problems: list[str]) -> str:
    """What save_session says when it refuses. Nothing is saved when this is returned."""
    return "Nothing was saved. Fix these and call save_session again:\n" + "\n".join(problems)


def session_data(normalized: dict, settings: dict) -> dict:
    """The JSON stored at meal_plans.data["sessions"][session]. Only call this once it validates."""
    week, session = normalized["week"], normalized["session"]
    return {"date": add_days(week, 1 if session == "tue" else 4),
            "covers": settings[session]["covers"],
            "recipes": {slot: dict(normalized["recipes"][slot]) for slot in SLOTS},
            "timeline": list(normalized["timeline"]),
            "leftovers": list(normalized["leftovers"])}


def save_result(week: str, session: str, session_data: dict, shopping: list[dict]) -> tuple[str, dict]:
    """(summary text, structuredContent) for a saved session."""
    url = f"{get_config().public_url}/#meals"
    d = date.fromisoformat(session_data["date"])
    w = date.fromisoformat(week)
    recipes, parts = {}, []
    for slot in SLOTS:
        r = session_data["recipes"][slot]
        kcal = round(sum(i["kcal"] for i in r["ingredients"]) / r["portions"])
        protein = round(sum(i["protein"] for i in r["ingredients"]) / r["portions"])
        recipes[slot] = {"title": r["title"], "portions": r["portions"],
                         "kcalPerPortion": kcal, "proteinPerPortion": protein}
        parts.append(f"{r['title']} ({r['portions']} × {kcal} kcal / {protein} g)")
    text = (f"Saved {WEEKDAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]} "
            f"(week of {w.day} {MONTHS[w.month - 1]}): {', '.join(parts)}. "
            f"{len(shopping)} shopping items. Open {url}")
    structured = {"week": week, "session": session, "date": session_data["date"], "recipes": recipes,
                  "shoppingCount": len(shopping), "url": url}
    return text, structured
```

- [ ] **Step 4: Write `store.save_session`**

Add to `app/store.py`, immediately after `save_plan`:

```python
def save_session(conn: sqlite3.Connection, week: str, session: str, session_data: dict, shopping: list[dict],
                 user_id: int | None) -> None:
    """Merge one prep session into a week. Call inside a transaction.

    The week's other session, and its shopping ticks, are left exactly as they were: only this
    session's meal_plans entry and its shopping_items rows are replaced. An existing row keeps its
    original source, note and created_by — only data and created_at move.
    """
    row = conn.execute("SELECT data FROM meal_plans WHERE week = ?", (week,)).fetchone()
    if row is None:
        data = {"sessions": {}}
        conn.execute("INSERT INTO meal_plans (week, data, source, note, created_at, created_by) "
                     "VALUES (?, ?, 'Claude', '', ?, ?)", (week, json.dumps(data), now_iso(), user_id))
    else:
        data = json.loads(row["data"])
    data.setdefault("sessions", {})[session] = session_data
    conn.execute("UPDATE meal_plans SET data = ?, created_at = ? WHERE week = ?",
                 (json.dumps(data), now_iso(), week))
    conn.execute("DELETE FROM shopping_items WHERE week = ? AND for_session = ?", (week, session))
    for n, i in enumerate(shopping):
        conn.execute(
            """INSERT INTO shopping_items (week, id, item, buy, aisle, for_session, stock, search, got, sort_order)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (week, i["id"], i["item"], i["buy"], i["aisle"], session, 1 if i["stock"] else 0, i["search"], n),
        )
```

- [ ] **Step 5: Run the menu tests to verify they pass**

Run: `python3 -m unittest tests.test_menu -v`
Expected: 19 tests, `OK`.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 111 tests` … `OK` (100 + 11).

- [ ] **Step 7: Commit**

```bash
git add app/menu.py app/store.py tests/test_menu.py
git commit -m "Validate a posted prep session and merge it into its week" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: `app/oauth.py` part 1 — metadata documents and dynamic client registration

**Files:**
- Create: `app/oauth.py`
- Modify: `app/auth.py:71-107` (`_token_hash` → `token_hash`, four call sites)
- Modify: `app/main.py` (mount `oauth.routes`, `NO_STORE_PREFIXES`, `OAUTH_CSP`)
- Create: `tests/test_oauth.py`

**Interfaces:**
- Consumes: `config.get_config().public_url` (Task 1); `app.db.connect`, `now_iso`, `tx`.
- Produces:
  - `auth.token_hash(token: str) -> str` — SHA-256 hex. No longer private; `app/oauth.py` imports it.
  - `oauth.routes: list[Route]` — three metadata routes plus `POST /oauth/register`. Tasks 6 and 7 append to this list.
  - `oauth.SCOPE = "menus"`, `oauth.CODE_TTL = 600`, `oauth.ACCESS_TTL = 3600`, `oauth.REFRESH_TTL = 2592000`, `oauth.REVOKED_KEEP = 604800`, `oauth.MAX_CLIENTS = 200`, `oauth.MAX_FORM_BYTES = 65536`, `oauth.CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"`.
  - `oauth.FormTooLarge(Exception)` and `async oauth.form_body(request) -> dict[str, str]`.
  - `oauth.sweep(conn) -> None`, `oauth._iso_ago(seconds: int) -> str`, `oauth._client(conn, client_id: str) -> sqlite3.Row | None`, `oauth._client_uris(client: sqlite3.Row) -> list[str]`.
  - `main.NO_STORE_PREFIXES = ("/api/", "/oauth/", "/mcp", "/.well-known/")` and `main.OAUTH_CSP`.
  - `tests/test_oauth.py` with `class OAuthTest` (the `ApiTest` fixture, plus `self.register(...)`).

**Context:** Claude's client is a **public** client registered by RFC 7591 dynamic registration, so `token_endpoint_auth_methods_supported` must include `"none"` and `client_id` is an identifier, not a credential — it is stored in the clear. Claude reads only the **first** entry of `authorization_servers`, and `resource` must equal the URL Owen typed, path included.

`form_body` exists because **Starlette 1.0's `request.form()` asserts `python-multipart` is installed before parsing any form body, urlencoded included**, and it is not a dependency. Nothing in this codebase may call `request.form()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_oauth.py`:

```python
"""OAuth server tests. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import os
import secrets
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = {"x-hrs": "1"}
CB = "https://claude.ai/api/mcp/auth_callback"
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL")


class OAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
        from app.auth import throttle
        throttle._fails.clear()
        from app import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["import-seed", str(ROOT / "seed")])
            cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner",
                      "--password", "owner-pass"])
            cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                      "--password", "staff-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def login(self, who: str) -> dict:
        pw = {"owen": "owner-pass", "maria": "staff-pass"}[who]
        r = self.c.post("/api/login", json={"username": who, "password": pw}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["me"]

    def register(self, uris: list[str] | None = None, name: str = "Claude") -> str:
        r = self.c.post("/oauth/register", json={"client_name": name, "redirect_uris": uris or [CB]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["client_id"]

    # ---- metadata ----
    def test_protected_resource_metadata(self):
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-protected-resource/mcp"):
            with self.subTest(path=path):
                r = self.c.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.headers["cache-control"], "no-store")
                self.assertEqual(r.json(), {"resource": "http://testserver/mcp",
                                            "authorization_servers": ["http://testserver"],
                                            "scopes_supported": ["menus"],
                                            "bearer_methods_supported": ["header"],
                                            "resource_name": "Galleon"})
                self.assertTrue(r.json()["resource"].endswith("/mcp"))

    def test_authorization_server_metadata(self):
        r = self.c.get("/.well-known/oauth-authorization-server")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {
            "issuer": "http://testserver",
            "authorization_endpoint": "http://testserver/oauth/authorize",
            "token_endpoint": "http://testserver/oauth/token",
            "registration_endpoint": "http://testserver/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": ["menus"]})

    # ---- registration ----
    def test_register_returns_a_client(self):
        r = self.c.post("/oauth/register", json={
            "client_name": "Claude", "redirect_uris": [CB],
            "client_uri": "https://claude.ai", "token_endpoint_auth_method": "none",
            "something_else": {"ignored": True}})   # every other RFC 7591 field is accepted and ignored
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertTrue(body["client_id"])
        self.assertIsInstance(body["client_id_issued_at"], int)
        self.assertEqual(body["client_name"], "Claude")
        self.assertEqual(body["redirect_uris"], [CB])
        self.assertEqual(body["token_endpoint_auth_method"], "none")
        self.assertEqual(body["grant_types"], ["authorization_code", "refresh_token"])
        self.assertEqual(body["response_types"], ["code"])

    def test_register_accepts_a_loopback_redirect(self):
        for uri in ("http://localhost:53127/callback", "http://127.0.0.1:8912/callback"):
            with self.subTest(uri=uri):
                self.assertTrue(self.register([uri]))

    def test_register_refuses_a_foreign_redirect(self):
        for uri in ("https://evil.example/cb", "https://claude.ai/api/mcp/auth_callback?next=evil",
                    "http://localhost:1234/cb?x=1", "javascript:alert(1)"):
            with self.subTest(uri=uri):
                r = self.c.post("/oauth/register", json={"client_name": "X", "redirect_uris": [uri]})
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_redirect_uri")
                self.assertTrue(r.json()["error_description"])

    def test_register_needs_redirect_uris(self):
        for body in ({"client_name": "X"}, {"client_name": "X", "redirect_uris": []},
                     {"redirect_uris": "https://claude.ai/api/mcp/auth_callback"}):
            with self.subTest(body=body):
                r = self.c.post("/oauth/register", json=body)
                self.assertEqual(r.status_code, 400, r.text)
                self.assertEqual(r.json()["error"], "invalid_client_metadata")


if __name__ == "__main__":
    unittest.main()
```

(`base64`, `hashlib`, `secrets` and `urllib.parse` are imported now because Tasks 6 and 7 add tests to this file that use them. If your linter objects, leave them — they will be used two tasks from now.)

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: all 6 FAIL — the metadata tests with `404` (no route), the registration tests with `AssertionError: 404 != 201`.

- [ ] **Step 3: Make `token_hash` public**

In `app/auth.py`, rename the helper and its four call sites:

```python
def token_hash(token: str) -> str:
    """SHA-256 hex. Session cookies, OAuth codes and OAuth tokens are only ever stored hashed."""
    return hashlib.sha256(token.encode()).hexdigest()
```

Then replace `_token_hash(` with `token_hash(` in `create_session`, `user_for_token`, `delete_session` and `delete_user_sessions`:

```bash
perl -pi -e 's/\b_token_hash\(/token_hash(/g' app/auth.py
grep -n "token_hash" app/auth.py
```

Expected from the grep: the `def token_hash` line plus exactly four call sites, and no underscore-prefixed name left.

- [ ] **Step 4: Write `app/oauth.py`**

Create `app/oauth.py`:

```python
"""The app as its own OAuth 2.1 authorization server for the Claude connector.

Every route here lives outside /api on purpose: a top-level browser navigation and a plain form post
cannot carry the X-HRS header the API requires, so POST /oauth/authorize checks Sec-Fetch-Site instead.
Codes and tokens are secrets.token_urlsafe(32) — 256 bits — and are stored only as SHA-256 hex.
Never log a code or a token.
"""
from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .auth import token_hash
from .config import get_config
from .db import connect, now_iso, tx

log = logging.getLogger("house_run_sheet")

SCOPE = "menus"
CODE_TTL = 600             # authorization codes: 10 minutes, single use
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
REVOKED_KEEP = 7 * 86400   # keep revoked rows a week so refresh-token reuse is still detectable
MAX_CLIENTS = 200
MAX_FORM_BYTES = 64 * 1024
CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"


class FormTooLarge(Exception):
    """A form body over MAX_FORM_BYTES. The caller answers 400 in its own shape."""


async def form_body(request: Request) -> dict[str, str]:
    """Parse an application/x-www-form-urlencoded body with the standard library.

    Never call request.form(): Starlette 1.0 asserts python-multipart is installed before parsing any
    form body, urlencoded included, and it is not in requirements.txt.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_FORM_BYTES:
        raise FormTooLarge()
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        raise FormTooLarge()
    pairs = urllib.parse.parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in pairs.items() if v}


def _iso_ago(seconds: int) -> str:
    """An ISO stamp in the same format as now_iso(), `seconds` in the past. Comparable as text."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sweep(conn: sqlite3.Connection) -> None:
    """Drop expired codes and tokens. Revoked rows are kept a week so reuse detection still works."""
    now = int(time.time())
    conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
    conn.execute("DELETE FROM oauth_tokens WHERE revoked_at IS NOT NULL AND revoked_at < ?",
                 (_iso_ago(REVOKED_KEEP),))


def _client(conn: sqlite3.Connection, client_id: str) -> sqlite3.Row | None:
    if not client_id:
        return None
    return conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()


def _client_uris(client: sqlite3.Row) -> list[str]:
    try:
        uris = json.loads(client["redirect_uris"])
    except ValueError:
        return []
    return [u for u in uris if isinstance(u, str)]


# ---------- metadata (public: no cookie, no bearer) ----------
async def protected_resource_metadata(request: Request) -> Response:
    """RFC 9728. `resource` must equal the URL Owen typed into Claude, path included."""
    base = get_config().public_url
    return JSONResponse({"resource": f"{base}/mcp", "authorization_servers": [base],
                         "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
                         "resource_name": "Galleon"})


async def authorization_server_metadata(request: Request) -> Response:
    """RFC 8414. Claude requires S256 in code_challenge_methods_supported and "none" as an auth method."""
    base = get_config().public_url
    return JSONResponse({"issuer": base,
                         "authorization_endpoint": f"{base}/oauth/authorize",
                         "token_endpoint": f"{base}/oauth/token",
                         "registration_endpoint": f"{base}/oauth/register",
                         "response_types_supported": ["code"],
                         "grant_types_supported": ["authorization_code", "refresh_token"],
                         "code_challenge_methods_supported": ["S256"],
                         "token_endpoint_auth_methods_supported": ["none"],
                         "scopes_supported": [SCOPE]})


# ---------- dynamic client registration (RFC 7591) ----------
def _registration_error(code: str, description: str) -> JSONResponse:
    return JSONResponse({"error": code, "error_description": description}, status_code=400)


def _redirect_allowed(uri: str) -> bool:
    """Claude's hosted callback, or a loopback callback for Claude Code (any port, any path)."""
    if uri == CLAUDE_REDIRECT:
        return True
    try:
        p = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    return (p.scheme == "http" and p.hostname in ("localhost", "127.0.0.1")
            and not p.query and not p.fragment)


async def register(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_FORM_BYTES:
        return _registration_error("invalid_client_metadata", "That registration request is too large.")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = None
    if not isinstance(body, dict):
        return _registration_error("invalid_client_metadata", "The registration request must be a JSON object.")
    uris = body.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and u for u in uris):
        return _registration_error("invalid_client_metadata", "redirect_uris must be a non-empty array of strings.")
    for uri in uris:
        if not _redirect_allowed(uri):
            return _registration_error("invalid_redirect_uri", f"{uri} is not a redirect URI this server accepts.")
    name = body.get("client_name")
    name = name.strip()[:120] if isinstance(name, str) else ""
    issued = int(time.time())
    # Everything else RFC 7591 allows (client_uri, logo_uri, scope, contacts, software_id…) is accepted
    # and ignored: this server has one resource, one scope and one kind of client.

    def run() -> Response:
        conn = connect()
        try:
            client_id = secrets.token_urlsafe(24)  # an identifier, not a credential: stored in the clear
            with tx(conn):
                if conn.execute("SELECT COUNT(*) FROM oauth_clients").fetchone()[0] > MAX_CLIENTS:
                    # Re-adding the connector registers again, so old unused rows accumulate. Anything
                    # a month old with no tokens behind it is dead weight.
                    conn.execute("DELETE FROM oauth_clients WHERE created_at < ? "
                                 "AND client_id NOT IN (SELECT client_id FROM oauth_tokens)",
                                 (_iso_ago(30 * 86400),))
                conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) "
                             "VALUES (?, ?, ?, ?)", (client_id, name, json.dumps(uris), now_iso()))
            log.info("registered OAuth client %s (%s)", client_id, name or "unnamed")
            return JSONResponse({"client_id": client_id, "client_id_issued_at": issued, "client_name": name,
                                 "redirect_uris": uris, "token_endpoint_auth_method": "none",
                                 "grant_types": ["authorization_code", "refresh_token"],
                                 "response_types": ["code"]}, status_code=201)
        finally:
            conn.close()

    return await run_in_threadpool(run)


routes = [
    Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
    Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
    Route("/.well-known/oauth-authorization-server", authorization_server_metadata),
    Route("/oauth/register", register, methods=["POST"]),
]
```

- [ ] **Step 5: Mount it and widen the headers**

In `app/main.py`: change `from .api import routes` to `from . import api, oauth`, add the two constants next to `CSP`, rewrite `SecurityHeaders.__call__`, and build the route list from the modules.

```python
from . import api, oauth
```

```python
# Widened beyond /api/: the OAuth endpoints, the metadata documents and the MCP endpoint must never
# be cached by a proxy either.
NO_STORE_PREFIXES = ("/api/", "/oauth/", "/mcp", "/.well-known/")

# The consent form's Allow button 302s to claude.ai, and form-action is checked against the
# submission target, so /oauth/ gets its own tighter policy. Its script is /oauth.js ('self').
OAUTH_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self'; connect-src 'self'; "
             "form-action 'self' https://claude.ai http://localhost:* http://127.0.0.1:*; "
             "frame-ancestors 'none'; base-uri 'none'")
```

```python
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        no_store = path.startswith(NO_STORE_PREFIXES)
        csp = OAUTH_CSP if path.startswith("/oauth/") else CSP

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers += [
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"same-origin"),
                    (b"x-frame-options", b"DENY"),
                    (b"content-security-policy", csp.encode()),
                ]
                if no_store:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)
```

```python
def create_app() -> Starlette:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)  # the frontend build writes here; run without it in dev
    app = Starlette(routes=[*api.routes, *oauth.routes,
                            Mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")],
                    lifespan=lifespan)
    return SecurityHeaders(app)
```

- [ ] **Step 6: Run the OAuth tests to verify they pass**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: 6 tests, `OK`.

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 117 tests` … `OK` (111 + 6).

- [ ] **Step 8: Commit**

```bash
git add app/oauth.py app/auth.py app/main.py tests/test_oauth.py
git commit -m "Publish the OAuth metadata and register Claude as a client" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 6: `app/oauth.py` part 2 — the consent page, and `frontend/public/oauth.js`

**Files:**
- Modify: `app/oauth.py` (append; `routes` gains two entries)
- Create: `frontend/public/oauth.js`
- Modify: `tests/test_oauth.py` (9 tests)

**Interfaces:**
- Consumes: `oauth.SCOPE`, `oauth.CODE_TTL`, `oauth.CLAUDE_REDIRECT`, `oauth.form_body`, `oauth.FormTooLarge`, `oauth._client`, `oauth._client_uris`, `oauth.routes` (Task 5); `auth.COOKIE_NAME`, `auth.user_for_token(conn, token) -> sqlite3.Row | None` (columns `id, username, display_name, role, label, phone, door_code, can_see_meals`); `auth.token_hash`; `app.db.connect`, `now_iso`, `tx`.
- Produces:
  - `GET /oauth/authorize` and `POST /oauth/authorize` appended to `oauth.routes`.
  - `oauth.PAGE: str`, `oauth.STYLE: str`, `oauth.LOGIN_BODY: str`, `oauth.BAD_CLIENT: str`, `oauth.PARAM_KEYS: tuple[str, ...]`.
  - `oauth.page(heading: str, body_html: str, status: int = 200) -> HTMLResponse`, `oauth.error_page(message: str, status: int) -> HTMLResponse`, `oauth._with_params(uri: str, params: dict[str, str]) -> str`, `oauth._redirect_error(redirect_uri: str, error: str, state: str) -> Response`.
  - `frontend/public/oauth.js`, served at `/oauth.js` by the existing static mount (Vite copies `frontend/public/` verbatim into `static/`).
- Page contract the JS depends on — ids `phone`, `code`, `smsForm`, `codeForm`, `sendCode`, `signIn`, `pwForm`, `username`, `password`, `signOut`, and one `[role="alert"]` block.

**Context:** The existing sign-in endpoints this page calls, with their exact bodies (read from `app/api.py`):

| Endpoint | Request JSON | 200 | Failure |
|---|---|---|---|
| `POST /api/login/sms/start` | `{"phone": "…"}` | `{"ok": true}` | 400/429/503 `{"error": "…"}` |
| `POST /api/login/sms/check` | `{"phone": "…", "code": "123456"}` | `{"me": {…}}` + sets `hrs_session` | 400/401/429/502/503 `{"error": "…"}` |
| `POST /api/login` | `{"username": "…", "password": "…"}` | `{"me": {…}}` + sets `hrs_session` | 401/429 `{"error": "…"}` |
| `POST /api/logout` | `{}` | `{"ok": true}` + clears the cookie | — |

All four need the `X-HRS: 1` header and a JSON body, and all of them answer JSON — which is why the staff page's "Sign out" is a `<button type="button">` driven by `fetch`, not a plain form post.

The `hrs_session` cookie is `SameSite=Lax` (`app/api.py: _session_response`), so it travels on Claude's top-level redirect into `GET /oauth/authorize` and a signed-in owner lands straight on the consent state.

- [ ] **Step 1: Write the failing tests**

Add this helper to `class OAuthTest` in `tests/test_oauth.py`, after `register`:

```python
    def params(self, client_id: str, **overrides) -> dict:
        """A valid authorize query. The challenge is RFC 7636's example; Task 7 uses a real verifier."""
        p = {"response_type": "code", "client_id": client_id, "redirect_uri": CB,
             "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
             "code_challenge_method": "S256", "state": "xyz123", "scope": "menus"}
        p.update(overrides)
        return p

    def query_of(self, response) -> dict[str, list[str]]:
        self.assertEqual(response.status_code, 302, response.text)
        return urllib.parse.parse_qs(urllib.parse.urlsplit(response.headers["location"]).query)
```

Then add these 9 tests:

```python
    # ---- the consent page ----
    def test_an_owner_sees_the_consent_page(self):
        client_id = self.register()
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("form-action 'self' https://claude.ai", r.headers["content-security-policy"])
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("Claude wants to read and write Galleon menus", r.text)
        self.assertIn(">Allow</button>", r.text)
        self.assertIn(">Deny</button>", r.text)
        self.assertIn('<script src="/oauth.js" defer></script>', r.text)
        self.assertIn(f'name="client_id" value="{client_id}"', r.text)
        self.assertIn('name="state" value="xyz123"', r.text)
        self.assertIn("Owen", r.text)

    def test_no_cookie_shows_the_sign_in_form(self):
        client_id = self.register()
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn('id="phone"', r.text)
        self.assertIn("Text me a code", r.text)
        self.assertIn("Owner? Sign in with a password", r.text)
        self.assertIn('src="/oauth.js"', r.text)
        self.assertNotIn(">Allow</button>", r.text)

    def test_staff_are_refused(self):
        client_id = self.register()
        self.login("maria")
        r = self.c.get("/oauth/authorize", params=self.params(client_id))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Only an owner can connect Claude.", r.text)
        self.assertNotIn(">Allow</button>", r.text)
        self.assertIn('id="signOut"', r.text)

    def test_the_consent_page_escapes_everything_it_echoes(self):
        client_id = self.register(name="<script>alert(1)</script>")
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params(client_id, state='"><b>x'))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("<script>alert(1)</script>", r.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", r.text)
        self.assertNotIn('"><b>x', r.text)
        self.assertIn("&quot;&gt;&lt;b&gt;x", r.text)

    def test_an_unknown_client_gets_an_error_page_and_no_redirect(self):
        self.login("owen")
        r = self.c.get("/oauth/authorize", params=self.params("not-a-client"), follow_redirects=False)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn("location", r.headers)
        self.assertIn("the app asking isn't registered here", r.text)
        # a redirect_uri the client never registered is the same class of failure
        client_id = self.register()
        r = self.c.get("/oauth/authorize",
                       params=self.params(client_id, redirect_uri="https://evil.example/cb"),
                       follow_redirects=False)
        self.assertEqual(r.status_code, 400, r.text)
        self.assertNotIn("location", r.headers)

    def test_a_bad_parameter_redirects_with_an_error(self):
        client_id = self.register()
        self.login("owen")
        cases = [({"response_type": "token"}, "unsupported_response_type"),
                 ({"code_challenge": ""}, "invalid_request"),
                 ({"code_challenge_method": "plain"}, "invalid_request"),
                 ({"scope": "menus admin"}, "invalid_scope")]
        for overrides, expected in cases:
            with self.subTest(expected=expected):
                q = self.query_of(self.c.get("/oauth/authorize", params=self.params(client_id, **overrides),
                                             follow_redirects=False))
                self.assertEqual(q["error"], [expected])
                self.assertEqual(q["state"], ["xyz123"])

    def test_allow_issues_a_code_and_deny_does_not(self):
        client_id = self.register()
        self.login("owen")
        p = self.params(client_id)
        q = self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "allow"},
                                      follow_redirects=False))
        self.assertTrue(q["code"][0])
        self.assertEqual(q["state"], ["xyz123"])
        q = self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "deny"},
                                      follow_redirects=False))
        self.assertEqual(q["error"], ["access_denied"])
        self.assertEqual(q["state"], ["xyz123"])
        self.assertNotIn("code", q)

    def test_post_authorize_needs_an_owner_session(self):
        client_id = self.register()
        p = {**self.params(client_id), "decision": "allow"}
        r = self.c.post("/oauth/authorize", data=p, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("Please sign in as an owner first.", r.text)
        self.login("maria")
        r = self.c.post("/oauth/authorize", data=p, follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("Only an owner can connect Claude.", r.text)

    def test_post_authorize_checks_sec_fetch_site(self):
        client_id = self.register()
        self.login("owen")
        p = {**self.params(client_id), "decision": "allow"}
        r = self.c.post("/oauth/authorize", data=p, headers={"sec-fetch-site": "cross-site"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 403, r.text)
        self.assertIn("That request didn't come from this page.", r.text)
        # the browser's own value for a form on this page is allowed
        r = self.c.post("/oauth/authorize", data=p, headers={"sec-fetch-site": "same-origin"},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: the 9 new tests FAIL with `404` (no `/oauth/authorize` route yet); the 6 from Task 5 still pass.

- [ ] **Step 3: Write the page template**

Append to `app/oauth.py`, and add `import html` to its imports and `HTMLResponse, RedirectResponse` to the `starlette.responses` import, plus `from .auth import COOKIE_NAME, token_hash, user_for_token`:

```python
# ---------- the consent page ----------
# Its CSS is inline (style-src allows 'unsafe-inline'); its script cannot be, so it lives at
# /oauth.js — frontend/public/oauth.js, which Vite copies verbatim into static/.
STYLE = """<style>
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 24px 16px; background: #f6f6f4; color: #1b1b1a; display: flex;
       justify-content: center; font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { width: 100%; max-width: 26rem; background: #fff; border: 1px solid #e2e1dd; border-radius: 14px; padding: 20px; }
h1 { font-size: 1.25rem; margin: 0 0 8px; }
p { margin: 8px 0; }
.muted { color: #5f5e59; font-size: 0.9rem; }
label { display: block; margin: 12px 0 4px; font-weight: 600; font-size: 0.9rem; }
input { width: 100%; padding: 10px; font-size: 1rem; border: 1px solid #c8c7c1; border-radius: 8px;
        background: #fff; color: inherit; }
button { margin-top: 12px; width: 100%; padding: 11px; font-size: 1rem; border-radius: 8px;
         border: 1px solid #c8c7c1; background: #fff; color: inherit; cursor: pointer; }
button.primary { background: #1b5e4b; border-color: #1b5e4b; color: #fff; font-weight: 600; }
.row { display: flex; gap: 8px; }
.row button { flex: 1; }
.alert { margin-top: 12px; padding: 10px; border-radius: 8px; background: #fdecea; color: #7f1d1d; font-size: 0.9rem; }
.alert:empty { display: none; }
details { margin-top: 16px; }
summary { cursor: pointer; font-size: 0.9rem; }
@media (prefers-color-scheme: dark) {
  body { background: #191918; color: #ececea; }
  main { background: #232322; border-color: #3a3a38; }
  input, button { background: #191918; border-color: #4a4a47; }
  button.primary { background: #2f7a62; border-color: #2f7a62; }
  .alert { background: #4a1d1b; color: #ffd9d6; }
  .muted { color: #a9a8a3; }
}
</style>"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Claude · Galleon</title>
{style}
</head>
<body>
<main>
<h1>{heading}</h1>
{body}
<div class="alert" role="alert"></div>
</main>
<script src="/oauth.js" defer></script>
</body>
</html>
"""

LOGIN_BODY = """<p class="muted">Sign in to connect Claude to Galleon.</p>
<form id="smsForm">
  <label for="phone">Mobile number</label>
  <input id="phone" name="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="(310) 555-1234">
  <button class="primary" id="sendCode" type="submit">Text me a code</button>
</form>
<form id="codeForm" hidden>
  <label for="code">6-digit code</label>
  <input id="code" name="code" type="text" inputmode="numeric" autocomplete="one-time-code" maxlength="6">
  <button class="primary" id="signIn" type="submit">Sign in</button>
</form>
<details>
  <summary>Owner? Sign in with a password</summary>
  <form id="pwForm">
    <label for="username">Username</label>
    <input id="username" name="username" type="text" autocomplete="username">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password">
    <button type="submit">Sign in</button>
  </form>
</details>"""

STAFF_BODY = """<p>Only an owner can connect Claude.</p>
<button id="signOut" type="button">Sign out and use a different account</button>"""

BAD_CLIENT = ("Galleon can't complete this connection: the app asking isn't registered here. "
              "Remove the connector in Claude and add it again.")

PARAM_KEYS = ("response_type", "client_id", "redirect_uri", "code_challenge", "code_challenge_method",
              "state", "scope", "resource")


def page(heading: str, body_html: str, status: int = 200) -> HTMLResponse:
    """One template, three states. body_html is already escaped by its builder.

    Text nodes are escaped with quote=False and attribute values with quote=True. Both are safe;
    quote=False keeps apostrophes readable ("didn't", not "didn&#x27;t") in prose the user reads.
    """
    return HTMLResponse(PAGE.format(style=STYLE, heading=html.escape(heading, quote=False), body=body_html),
                        status_code=status)


def error_page(message: str, status: int) -> HTMLResponse:
    return page("Can't connect Claude", f"<p>{html.escape(message, quote=False)}</p>", status=status)


def _hidden(params: dict[str, str]) -> str:
    return "".join(f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
                   for k, v in params.items())


def _consent_body(params: dict[str, str], client_name: str, display_name: str) -> str:
    who = client_name or "A Claude app"
    return (f'<p>{html.escape(who, quote=False)} is asking. '
            f'You are signed in as {html.escape(display_name, quote=False)}.</p>'
            f'<p class="muted">It will be able to read your menus, settings and shopping list, and to '
            f'replace one prep session at a time.</p>'
            f'<form method="post" action="/oauth/authorize">{_hidden(params)}'
            f'<div class="row">'
            f'<button class="primary" type="submit" name="decision" value="allow">Allow</button>'
            f'<button type="submit" name="decision" value="deny">Deny</button>'
            f'</div></form>')


def _with_params(uri: str, params: dict[str, str]) -> str:
    """Append params to a redirect URI. Empty values are dropped; a URI with a query gets '&'."""
    parts = [f"{k}={urllib.parse.quote(v, safe='')}" for k, v in params.items() if v]
    return uri + ("&" if "?" in uri else "?") + "&".join(parts)


def _redirect_error(redirect_uri: str, error: str, state: str) -> Response:
    return RedirectResponse(_with_params(redirect_uri, {"error": error, "state": state}), status_code=302)
```

- [ ] **Step 4: Write the two authorize handlers**

Append to `app/oauth.py`:

```python
async def authorize_get(request: Request) -> Response:
    q = {k: request.query_params.get(k, "") for k in PARAM_KEYS}

    def run() -> Response:
        conn = connect()
        try:
            # RFC 6749 §4.1.2.1: an unknown client or an unregistered redirect_uri must NEVER be
            # redirected to — that is how an open redirector is built. Everything else goes back to
            # the client as an error parameter.
            client = _client(conn, q["client_id"])
            if client is None or q["redirect_uri"] not in _client_uris(client):
                return error_page(BAD_CLIENT, 400)
            if q["response_type"] != "code":
                return _redirect_error(q["redirect_uri"], "unsupported_response_type", q["state"])
            if not q["code_challenge"] or q["code_challenge_method"] != "S256":
                return _redirect_error(q["redirect_uri"], "invalid_request", q["state"])
            scope = q["scope"] or SCOPE
            if set(scope.split()) - {SCOPE}:
                return _redirect_error(q["redirect_uri"], "invalid_scope", q["state"])
            user = user_for_token(conn, request.cookies.get(COOKIE_NAME))
            if user is None:
                return page("Sign in to Galleon", LOGIN_BODY)
            if user["role"] != "owner":
                return page("Can't connect Claude", STAFF_BODY)
            return page("Claude wants to read and write Galleon menus",
                        _consent_body({**q, "scope": scope}, client["client_name"], user["display_name"]))
        finally:
            conn.close()

    return await run_in_threadpool(run)


async def authorize_post(request: Request) -> Response:
    # The CSRF defence in place of X-HRS, which a plain form post cannot carry. An absent header is
    # allowed: old browsers send none.
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in ("same-origin", "none"):
        return error_page("That request didn't come from this page.", 403)
    try:
        form = await form_body(request)
    except FormTooLarge:
        return error_page("That request was too large.", 400)

    def run() -> Response:
        conn = connect()
        try:
            user = user_for_token(conn, request.cookies.get(COOKIE_NAME))
            if user is None:
                return error_page("Please sign in as an owner first.", 403)
            if user["role"] != "owner":
                return error_page("Only an owner can connect Claude.", 403)
            client = _client(conn, form.get("client_id", ""))
            redirect_uri = form.get("redirect_uri", "")
            if client is None or redirect_uri not in _client_uris(client):
                return error_page(BAD_CLIENT, 400)
            state = form.get("state", "")
            if form.get("decision") != "allow":
                return _redirect_error(redirect_uri, "access_denied", state)
            code = secrets.token_urlsafe(32)
            with tx(conn):
                conn.execute(
                    """INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri, code_challenge,
                                                scope, resource, expires_at, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (token_hash(code), client["client_id"], user["id"], redirect_uri,
                     form.get("code_challenge", ""), form.get("scope", "") or SCOPE,
                     form.get("resource", ""), int(time.time()) + CODE_TTL, now_iso()))
            log.info("issued an authorization code to client %s for user %s", client["client_id"], user["id"])
            return RedirectResponse(_with_params(redirect_uri, {"code": code, "state": state}), status_code=302)
        finally:
            conn.close()

    return await run_in_threadpool(run)
```

and add the two routes to `routes`, so it reads:

```python
routes = [
    Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
    Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
    Route("/.well-known/oauth-authorization-server", authorization_server_metadata),
    Route("/oauth/register", register, methods=["POST"]),
    Route("/oauth/authorize", authorize_get),
    Route("/oauth/authorize", authorize_post, methods=["POST"]),
]
```

- [ ] **Step 5: Write `frontend/public/oauth.js`**

Create `frontend/public/oauth.js`:

```javascript
"use strict";
// The consent page's script. It lives here, not in an inline <script>, because /oauth/ is served with
// script-src 'self'. Vite copies frontend/public/ verbatim into static/, so this is served at /oauth.js
// with no build step — plain browser JavaScript, no framework, no bundler.
(function () {
  var alertBox = document.querySelector('[role="alert"]');
  var phone = document.getElementById("phone");
  var code = document.getElementById("code");
  var smsForm = document.getElementById("smsForm");
  var codeForm = document.getElementById("codeForm");
  var sendCode = document.getElementById("sendCode");
  var signIn = document.getElementById("signIn");
  var pwForm = document.getElementById("pwForm");
  var signOut = document.getElementById("signOut");

  function say(message) {
    if (alertBox) alertBox.textContent = message || "";
  }

  // Every endpoint here is the same one the app itself uses: JSON in, JSON out, X-HRS for CSRF.
  async function post(path, body) {
    var resp;
    try {
      resp = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json", "X-HRS": "1" },
        body: JSON.stringify(body),
      });
    } catch (err) {
      return { ok: false, error: "Couldn't reach Galleon. Check your connection and try again." };
    }
    var data = {};
    try {
      data = await resp.json();
    } catch (err) {
      data = {};
    }
    if (!resp.ok) return { ok: false, error: data.error || "Something went wrong. Try again." };
    return { ok: true, data: data };
  }

  if (smsForm) {
    smsForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      sendCode.disabled = true;
      var result = await post("/api/login/sms/start", { phone: phone.value });
      sendCode.disabled = false;
      if (!result.ok) {
        say(result.error);
        return;
      }
      codeForm.hidden = false;
      sendCode.textContent = "Text me another code";
      code.focus();
    });
  }

  if (codeForm) {
    codeForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      signIn.disabled = true;
      var result = await post("/api/login/sms/check", { phone: phone.value, code: code.value });
      signIn.disabled = false;
      if (!result.ok) {
        say(result.error);
        return;
      }
      // The cookie is set; reloading this same URL keeps every OAuth parameter and shows the consent state.
      location.reload();
    });
  }

  if (pwForm) {
    pwForm.addEventListener("submit", async function (event) {
      event.preventDefault();
      say("");
      var result = await post("/api/login", {
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
      });
      if (!result.ok) {
        say(result.error);
        return;
      }
      location.reload();
    });
  }

  if (signOut) {
    signOut.addEventListener("click", async function () {
      await post("/api/logout", {});
      location.reload();
    });
  }
})();
```

- [ ] **Step 6: Run the OAuth tests to verify they pass**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: 15 tests, `OK`.

- [ ] **Step 7: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 126 tests` … `OK` (117 + 9).

- [ ] **Step 8: Commit**

```bash
git add app/oauth.py frontend/public/oauth.js tests/test_oauth.py
git commit -m "Ask the owner to allow Claude on a server-rendered consent page" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 7: `app/oauth.py` part 3 — the token endpoint and the bearer check

**Files:**
- Modify: `app/oauth.py` (append; `routes` gains `POST /oauth/token`)
- Modify: `tests/test_oauth.py` (10 tests + three helpers)

**Interfaces:**
- Consumes: everything Tasks 5 and 6 produced — `SCOPE`, `ACCESS_TTL`, `REFRESH_TTL`, `form_body`, `FormTooLarge`, `sweep`, `_client`, `_iso_ago`, `routes`; `auth.token_hash`; `config.get_config().public_url`; `app.db.connect`, `now_iso`, `tx`.
- Produces:
  - `POST /oauth/token` appended to `oauth.routes`.
  - `oauth.token_error(code: str, description: str) -> JSONResponse` — always **400**.
  - `oauth.unauthorized(token_presented: bool) -> Response` — the 401 that starts Claude's sign-in.
  - `oauth.owner_for_bearer(conn, header: str | None) -> sqlite3.Row | None` — the row carries `id, username, display_name, role, label, phone, door_code, can_see_meals` (the same columns `auth.user_for_token` returns) plus `token_hash` and `last_used_at`.
  - `async oauth.require_owner_bearer(request) -> tuple[sqlite3.Row | None, Response | None]` — `(owner, None)` or `(None, 401 response)`. Task 9 calls this from `POST /mcp`.

**Context:** An unknown `client_id` answers **400 `invalid_client`**, not 401: the client authenticates with `none`, so no HTTP authentication scheme was attempted, and RFC 6749 §5.2 would require a `WWW-Authenticate` header on a 401 — which Claude would mistake for a protected-resource challenge and restart discovery.

A `WWW-Authenticate` header is only honoured by Claude **on a 401**; one on a 200 is ignored. A staff-owned token is `invalid_token`, not 403 — a connector with no owner behind it can never do anything.

Refresh tokens must rotate for public clients. Presenting a token whose row is already `revoked_at` means the old one leaked, so the whole `family` dies.

- [ ] **Step 1: Write the failing tests**

Add `import json` and `import time` to `tests/test_oauth.py`'s imports, then these three helpers to `class OAuthTest`:

```python
    def pkce(self) -> tuple[str, str]:
        """(verifier, S256 challenge) — a real pair, exactly as Claude computes it."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        return verifier, challenge

    def code_for(self, client_id: str, challenge: str, **overrides) -> str:
        """Sign in as the owner and press Allow. Returns the authorization code from the redirect."""
        self.login("owen")
        p = self.params(client_id, code_challenge=challenge, **overrides)
        return self.query_of(self.c.post("/oauth/authorize", data={**p, "decision": "allow"},
                                         follow_redirects=False))["code"][0]

    def dance(self) -> dict:
        """Register, consent and exchange. The token response plus the "client_id" that earned it."""
        client_id = self.register()
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.status_code, 200, r.text)
        return {**r.json(), "client_id": client_id}
```

Then these 10 tests:

```python
    # ---- the token endpoint ----
    def test_the_full_dance_returns_tokens(self):
        tokens = self.dance()
        self.assertTrue(tokens["access_token"])
        self.assertTrue(tokens["refresh_token"])
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(tokens["expires_in"], 3600)
        self.assertEqual(tokens["scope"], "menus")

    def test_a_wrong_verifier_burns_the_code(self):
        client_id = self.register()
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": "not-the-verifier"})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_grant")
        # the row is deleted, so even the right verifier can't rescue it
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_a_reused_code_is_refused(self):
        client_id = self.register()
        verifier, challenge = self.pkce()
        data = {"grant_type": "authorization_code", "code": self.code_for(client_id, challenge),
                "client_id": client_id, "redirect_uri": CB, "code_verifier": verifier}
        self.assertEqual(self.c.post("/oauth/token", data=data).status_code, 200)
        r = self.c.post("/oauth/token", data=data)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_the_code_is_bound_to_its_client_and_redirect_uri(self):
        client_id = self.register()
        other = self.register(name="Another app")
        verifier, challenge = self.pkce()
        code = self.code_for(client_id, challenge)
        base = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier}
        r = self.c.post("/oauth/token", data={**base, "client_id": other, "redirect_uri": CB})
        self.assertEqual(r.json()["error"], "invalid_grant")
        r = self.c.post("/oauth/token", data={**base, "client_id": client_id,
                                              "redirect_uri": "http://localhost:1/callback"})
        self.assertEqual(r.json()["error"], "invalid_grant")
        # neither attempt burned it: the honest exchange still works
        r = self.c.post("/oauth/token", data={**base, "client_id": client_id, "redirect_uri": CB})
        self.assertEqual(r.status_code, 200, r.text)

    def test_missing_fields_are_invalid_request(self):
        client_id = self.register()
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "client_id": client_id})
        self.assertEqual(r.json()["error"], "invalid_request")
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token", "client_id": client_id})
        self.assertEqual(r.json()["error"], "invalid_request")

    def test_a_json_body_and_a_bad_grant_type_are_refused(self):
        client_id = self.register()
        r = self.c.post("/oauth/token", json={"grant_type": "refresh_token", "refresh_token": "x",
                                              "client_id": client_id})
        self.assertEqual(r.status_code, 400, r.text)
        self.assertEqual(r.json()["error"], "invalid_request")
        self.assertEqual(r.json()["error_description"],
                         "The token endpoint takes application/x-www-form-urlencoded.")
        r = self.c.post("/oauth/token", data={"grant_type": "password", "username": "owen",
                                              "password": "owner-pass"})
        self.assertEqual(r.json()["error"], "unsupported_grant_type")
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": "x",
                                              "client_id": "never-registered", "redirect_uri": CB,
                                              "code_verifier": "y"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_client")

    def test_refresh_rotates_the_pair(self):
        tokens = self.dance()
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        fresh = r.json()
        self.assertNotEqual(fresh["refresh_token"], tokens["refresh_token"])
        self.assertNotEqual(fresh["access_token"], tokens["access_token"])
        self.assertEqual(fresh["expires_in"], 3600)
        self.assertEqual(fresh["scope"], "menus")
        self.assertEqual(fresh["token_type"], "Bearer")
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")

    def test_reusing_a_rotated_refresh_token_kills_the_family(self):
        tokens = self.dance()
        fresh = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                                  "refresh_token": tokens["refresh_token"],
                                                  "client_id": tokens["client_id"]}).json()
        # presenting the rotated-away token is treated as a leak
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")
        # …and it takes the rest of the family with it
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": fresh["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")

    # ---- the bearer check ----
    def test_owner_for_bearer_accepts_only_a_live_owner_access_token(self):
        from app import oauth
        from app.auth import token_hash
        from app.db import connect, now_iso
        tokens = self.dance()
        conn = connect()
        try:
            row = oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"])
            self.assertIsNotNone(row)
            self.assertEqual(row["username"], "owen")
            self.assertEqual(row["role"], "owner")
            self.assertIsNotNone(conn.execute("SELECT last_used_at FROM oauth_tokens WHERE token_hash = ?",
                                              (token_hash(tokens["access_token"]),)).fetchone()["last_used_at"])
            self.assertIsNotNone(oauth.owner_for_bearer(conn, "bearer " + tokens["access_token"]))  # case-insensitive
            self.assertIsNone(oauth.owner_for_bearer(conn, tokens["access_token"]))                 # no scheme
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer "))
            self.assertIsNone(oauth.owner_for_bearer(conn, None))
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer nonsense"))
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["refresh_token"]))    # wrong kind
            for name, username, expires_in in (("expired", "owen", -10), ("staff-owned", "maria", 3600)):
                with self.subTest(token=name):
                    conn.execute(
                        "INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, scope, "
                        "expires_at, created_at) VALUES (?, 'access', ?, "
                        "(SELECT id FROM users WHERE username = ?), ?, 'menus', ?, ?)",
                        (token_hash(name), tokens["client_id"], username, f"fam-{name}",
                         int(time.time()) + expires_in, now_iso()))
                    self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + name))
        finally:
            conn.close()

    def test_the_unauthorized_response_carries_the_resource_metadata(self):
        from app import oauth
        r = oauth.unauthorized(False)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(json.loads(r.body), {"error": "unauthorized"})
        r = oauth.unauthorized(True)
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer error="invalid_token", '
                         'resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(json.loads(r.body), {"error": "invalid_token"})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: the 10 new tests FAIL — the token ones with `AssertionError: 404 != 200`, the two bearer ones with `AttributeError: module 'app.oauth' has no attribute 'owner_for_bearer'` / `'unauthorized'`.

- [ ] **Step 3: Write the token endpoint**

Append to `app/oauth.py`, and add `import base64`, `import hashlib` and `import hmac` to its imports:

```python
# ---------- the token endpoint ----------
def token_error(code: str, description: str) -> JSONResponse:
    """Every token failure is a 400 with an RFC 6749 error code. Never a 401: see the module note."""
    return JSONResponse({"error": code, "error_description": description}, status_code=400)


def _issue(conn: sqlite3.Connection, client_id: str, user_id: int, scope: str, family: str) -> JSONResponse:
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    now = int(time.time())
    for token, kind, ttl in ((access, "access", ACCESS_TTL), (refresh, "refresh", REFRESH_TTL)):
        conn.execute("""INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, scope,
                                                  expires_at, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                     (token_hash(token), kind, client_id, user_id, family, scope, now + ttl, now_iso()))
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ACCESS_TTL,
                         "refresh_token": refresh, "scope": scope})


def _exchange_code(conn: sqlite3.Connection, form: dict[str, str]) -> Response:
    code = form.get("code", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    verifier = form.get("code_verifier", "")
    if not (code and client_id and redirect_uri and verifier):
        return token_error("invalid_request", "code, client_id, redirect_uri and code_verifier are all required.")
    if _client(conn, client_id) is None:
        return token_error("invalid_client", "That client_id is not registered here.")
    row = conn.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (token_hash(code),)).fetchone()
    if (row is None or row["expires_at"] < int(time.time()) or row["client_id"] != client_id
            or row["redirect_uri"] != redirect_uri):
        return token_error("invalid_grant", "That authorization code is not valid.")
    # Single use: the row goes now, so a wrong verifier burns the code rather than allowing guesses.
    conn.execute("DELETE FROM oauth_codes WHERE code_hash = ?", (row["code_hash"],))
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    if not hmac.compare_digest(challenge, row["code_challenge"]):
        return token_error("invalid_grant", "The code_verifier does not match the code_challenge.")
    return _issue(conn, client_id, row["user_id"], row["scope"] or SCOPE, secrets.token_urlsafe(16))


def _refresh(conn: sqlite3.Connection, form: dict[str, str]) -> Response:
    presented = form.get("refresh_token", "")
    client_id = form.get("client_id", "")
    if not (presented and client_id):
        return token_error("invalid_request", "refresh_token and client_id are both required.")
    if _client(conn, client_id) is None:
        return token_error("invalid_client", "That client_id is not registered here.")
    row = conn.execute("SELECT * FROM oauth_tokens WHERE token_hash = ?", (token_hash(presented),)).fetchone()
    stale = token_error("invalid_grant", "That refresh token is no longer valid. Sign in again.")
    if (row is None or row["kind"] != "refresh" or row["client_id"] != client_id
            or row["expires_at"] < int(time.time())):
        return stale
    if row["revoked_at"]:
        # A rotated-away token came back: assume it leaked and kill the whole grant chain.
        conn.execute("UPDATE oauth_tokens SET revoked_at = ? WHERE family = ? AND revoked_at IS NULL",
                     (now_iso(), row["family"]))
        log.warning("revoked OAuth token family %s after a refresh-token replay", row["family"])
        return stale
    conn.execute("UPDATE oauth_tokens SET revoked_at = ? WHERE token_hash = ?", (now_iso(), row["token_hash"]))
    # Access tokens from the previous rotation are left to expire on their own — an hour at most.
    return _issue(conn, client_id, row["user_id"], row["scope"] or SCOPE, row["family"])


async def token(request: Request) -> Response:
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return token_error("invalid_request", "The token endpoint takes application/x-www-form-urlencoded.")
    try:
        form = await form_body(request)
    except FormTooLarge:
        return token_error("invalid_request", "That request body was too large.")

    def run() -> Response:
        conn = connect()
        try:
            # One transaction for the sweep and the grant. Fields beyond the ones read here (resource,
            # scope, client_secret…) are accepted and ignored: MCP clients send resource per RFC 8707
            # and this server has exactly one resource.
            with tx(conn):
                sweep(conn)
                grant = form.get("grant_type", "")
                if grant == "authorization_code":
                    return _exchange_code(conn, form)
                if grant == "refresh_token":
                    return _refresh(conn, form)
                return token_error("unsupported_grant_type",
                                   f'"{grant}" is not a grant type this server supports.')
        finally:
            conn.close()

    return await run_in_threadpool(run)
```

Add the route, so `routes` ends:

```python
    Route("/oauth/authorize", authorize_get),
    Route("/oauth/authorize", authorize_post, methods=["POST"]),
    Route("/oauth/token", token, methods=["POST"]),
]
```

- [ ] **Step 4: Write the bearer check**

Append to `app/oauth.py`:

```python
# ---------- the resource-server side ----------
def unauthorized(token_presented: bool) -> Response:
    """The 401 that starts Claude's sign-in. Claude only honours WWW-Authenticate on a 401."""
    base = get_config().public_url
    parts = [f'resource_metadata="{base}/.well-known/oauth-protected-resource"', f'scope="{SCOPE}"']
    if token_presented:
        parts.insert(0, 'error="invalid_token"')
    body = {"error": "invalid_token" if token_presented else "unauthorized"}
    return JSONResponse(body, status_code=401, headers={"WWW-Authenticate": "Bearer " + ", ".join(parts)})


def owner_for_bearer(conn: sqlite3.Connection, header: str | None) -> sqlite3.Row | None:
    """The owner behind a Bearer access token, or None. A staff-owned token counts as invalid."""
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return None
    row = conn.execute(
        """SELECT t.token_hash, t.last_used_at, u.id, u.username, u.display_name, u.role, u.label,
                  u.phone, u.door_code, u.can_see_meals
             FROM oauth_tokens t JOIN users u ON u.id = t.user_id
            WHERE t.token_hash = ? AND t.kind = 'access' AND t.expires_at > ?
              AND t.revoked_at IS NULL AND u.active = 1 AND u.role = 'owner'""",
        (token_hash(token), int(time.time()))).fetchone()
    if row is None:
        return None
    # A chatty client would otherwise write on every call; a minute's resolution is plenty for the
    # "last used" line in Setup → Claude.
    if not row["last_used_at"] or row["last_used_at"] < _iso_ago(60):
        conn.execute("UPDATE oauth_tokens SET last_used_at = ? WHERE token_hash = ?",
                     (now_iso(), row["token_hash"]))
    return row


async def require_owner_bearer(request: Request) -> tuple[sqlite3.Row | None, Response | None]:
    """(owner, None) for a good Bearer access token, else (None, the 401 that starts sign-in)."""
    header = request.headers.get("authorization")

    def run():
        conn = connect()
        try:
            return owner_for_bearer(conn, header)
        finally:
            conn.close()

    user = await run_in_threadpool(run)
    if user is None:
        return None, unauthorized(bool(header))
    return user, None
```

- [ ] **Step 5: Run the OAuth tests to verify they pass**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: 25 tests, `OK`.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 136 tests` … `OK` (126 + 10).

- [ ] **Step 7: Commit**

```bash
git add app/oauth.py tests/test_oauth.py
git commit -m "Issue and rotate OAuth tokens, and check the bearer on every call" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 8: the connections API under `/api`

**Files:**
- Modify: `app/api.py` (imports, two handlers after `set_got`, two routes)
- Modify: `tests/test_oauth.py` (3 tests)
- Modify: `tests/test_api.py` (one assertion in `test_staff_limits`)

**Interfaces:**
- Consumes: `api.endpoint(role="owner")`, `api.ApiError`; `config.get_config().public_url`; `app.db.now_iso`; the `oauth_tokens`/`oauth_clients` tables (Task 2); `oauth.owner_for_bearer` (Task 7, for the test only).
- Produces:
  - `GET /api/oauth/connections` → `{"connections": [{"family", "clientName", "connectedAt", "lastUsedAt"}], "mcpUrl": "<public_url>/mcp"}`, newest `connectedAt` first.
  - `DELETE /api/oauth/connections/{family}` → `{"ok": true}`, or **404** `"That connection doesn't exist."`.
  - `api.FAMILY_RE`.

**Context:** These are ordinary API routes, so they use the normal `endpoint(role="owner")` decorator, require `X-HRS: 1` on the DELETE and answer a role mismatch with exactly `Only an owner can do that.` (AGENTS.md). `mcpUrl` rides here rather than in `/api/state` so the connector URL is never sent to staff and no new route is needed.

One entry per `family` that still has an unrevoked, unexpired `refresh` row belonging to the caller: `connectedAt` is the family's oldest `created_at` (the original grant), `lastUsedAt` the newest non-null `last_used_at` across the family (SQLite's `MAX` skips NULLs, so it is `null` only when nothing in the family has been used).

- [ ] **Step 1: Write the failing tests**

Add these 3 tests to `class OAuthTest` in `tests/test_oauth.py`:

```python
    # ---- the connections list ----
    def test_connections_lists_the_grant(self):
        self.dance()
        r = self.c.get("/api/oauth/connections")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["mcpUrl"], "http://testserver/mcp")
        self.assertEqual(len(body["connections"]), 1)
        entry = body["connections"][0]
        self.assertEqual(entry["clientName"], "Claude")
        self.assertTrue(entry["connectedAt"])
        self.assertIsNone(entry["lastUsedAt"])  # the access token has not been used yet
        self.assertRegex(entry["family"], r"^[A-Za-z0-9_-]{8,64}$")

    def test_disconnecting_revokes_the_whole_family(self):
        from app import oauth
        from app.db import connect
        tokens = self.dance()
        family = self.c.get("/api/oauth/connections").json()["connections"][0]["family"]
        conn = connect()
        try:
            self.assertIsNotNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"]))
            r = self.c.delete(f"/api/oauth/connections/{family}", headers=H)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json(), {"ok": True})
            self.assertIsNone(oauth.owner_for_bearer(conn, "Bearer " + tokens["access_token"]))
        finally:
            conn.close()
        self.assertEqual(self.c.get("/api/oauth/connections").json()["connections"], [])
        r = self.c.post("/oauth/token", data={"grant_type": "refresh_token",
                                              "refresh_token": tokens["refresh_token"],
                                              "client_id": tokens["client_id"]})
        self.assertEqual(r.json()["error"], "invalid_grant")
        for bad in ("nope", "short", "not-a-real-family"):
            with self.subTest(family=bad):
                r = self.c.delete(f"/api/oauth/connections/{bad}", headers=H)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertEqual(r.json()["error"], "That connection doesn't exist.")

    def test_staff_cannot_touch_connections(self):
        self.dance()
        family = self.c.get("/api/oauth/connections").json()["connections"][0]["family"]
        self.c.post("/api/logout", headers=H)
        self.login("maria")
        r = self.c.get("/api/oauth/connections")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Only an owner can do that.")
        r = self.c.delete(f"/api/oauth/connections/{family}", headers=H)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["error"], "Only an owner can do that.")
```

and add one line to `test_staff_limits` in `tests/test_api.py`, after the `/api/users` assertion:

```python
        self.assertEqual(self.c.get("/api/oauth/connections").status_code, 403)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_oauth -v`
Expected: the 3 new tests FAIL with `AssertionError: 404 != 200` (no route yet).

- [ ] **Step 3: Write the handlers**

In `app/api.py`, add `import time` to the imports and `FAMILY_RE` next to the other regexes:

```python
FAMILY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")  # oauth_tokens.family is secrets.token_urlsafe(16)
```

Then add both handlers immediately after `set_got` (where `generate` used to be):

```python
# ---------- the Claude connector (owner) ----------
@endpoint(role="owner")
def list_connections(request, conn, user, _body):
    """One entry per live grant chain. mcpUrl rides here so staff never see the connector URL."""
    rows = conn.execute(
        """SELECT t.family AS family, MIN(t.created_at) AS connected_at,
                  MAX(t.last_used_at) AS last_used_at, MAX(c.client_name) AS client_name
             FROM oauth_tokens t
             LEFT JOIN oauth_clients c ON c.client_id = t.client_id
            WHERE t.user_id = ?
              AND t.family IN (SELECT family FROM oauth_tokens
                                WHERE user_id = ? AND kind = 'refresh'
                                  AND revoked_at IS NULL AND expires_at > ?)
            GROUP BY t.family
            ORDER BY connected_at DESC""",
        (user["id"], user["id"], int(time.time()))).fetchall()
    return {"connections": [{"family": r["family"], "clientName": r["client_name"] or "Claude",
                             "connectedAt": r["connected_at"], "lastUsedAt": r["last_used_at"]}
                            for r in rows],
            "mcpUrl": f"{get_config().public_url}/mcp"}


@endpoint(role="owner")
def delete_connection(request, conn, user, _body):
    family = request.path_params["family"]
    missing = ApiError(404, "That connection doesn't exist.")
    if not FAMILY_RE.match(family):
        raise missing
    if not conn.execute("SELECT 1 FROM oauth_tokens WHERE family = ? AND user_id = ?",
                        (family, user["id"])).fetchone():
        raise missing
    conn.execute("UPDATE oauth_tokens SET revoked_at = ? WHERE family = ? AND user_id = ? AND revoked_at IS NULL",
                 (now_iso(), family, user["id"]))
    return {"ok": True}
```

and the two routes, after the `/api/plans/…` block:

```python
    Route("/api/oauth/connections", list_connections),
    Route("/api/oauth/connections/{family}", delete_connection, methods=["DELETE"]),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_oauth tests.test_api -v`
Expected: 28 OAuth tests and 62 API tests (`Ran 90 tests`), `OK`.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 139 tests` … `OK` (136 + 3).

- [ ] **Step 6: Commit**

```bash
git add app/api.py tests/test_oauth.py tests/test_api.py
git commit -m "List and disconnect the Claude connections an owner has" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 9: `app/mcp.py` — the JSON-RPC envelope, the handshake and `tools/list`

**Files:**
- Create: `app/mcp.py`
- Modify: `app/main.py` (import and mount `mcp.routes`)
- Create: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `oauth.require_owner_bearer(request) -> tuple[Row | None, Response | None]`, `oauth.unauthorized` (via that helper) from Task 7; `menu.GET_BRIEF_SCHEMA` (Task 3), `menu.SAVE_SESSION_SCHEMA` (Task 4).
- Produces:
  - `mcp.routes: list[Route]` — `POST /mcp` plus a `405` for `GET`/`DELETE`.
  - `mcp.MAX_BODY_BYTES = 262144`, `mcp.SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")`, `mcp.LATEST`, `mcp.SERVER_INFO`, `mcp.INSTRUCTIONS`, `mcp.TOOLS`, `mcp.TOOL_NAMES`.
  - `mcp.jsonrpc_result(rid, result: dict) -> JSONResponse`, `mcp.jsonrpc_error(rid, code: int, message: str, status: int = 200) -> JSONResponse` — Task 10 uses both.

**Decision to state plainly:** the two JSON Schemas live in **`app/menu.py`** (`GET_BRIEF_SCHEMA`, `SAVE_SESSION_SCHEMA`), next to the validation that enforces them, and `app/mcp.py` only references them. `tools/call` is **not** part of this task: until Task 10 wires it, `tools/call` answers `-32601 Method not found: tools/call.`, which is the honest answer for a method this build does not implement. Do not invent a placeholder tool result.

**Context:** Transport is Streamable HTTP in its stateless JSON form: one JSON-RPC request per POST, one JSON response per POST, `Content-Type: application/json` on every response, no SSE and no session ids. `Accept` is not required (Claude sends `application/json, text/event-stream`; we always answer JSON). `MCP-Protocol-Version`, when present, is logged and otherwise ignored.

Check order is fixed: **body size → bearer → JSON parse → dispatch.** Size first so an anonymous request cannot make a worker buffer 10 MB; bearer before parse so a token-less client always gets the sign-in handshake rather than a parse error.

Before implementing, check <https://modelcontextprotocol.io> for a protocol revision newer than `2025-06-18`; if there is one, prepend it to `SUPPORTED_PROTOCOL_VERSIONS` (the echo rule then handles old and new clients with no other change) and use it in the test below.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp.py`:

```python
"""The MCP endpoint. Run with:  python3 -m unittest discover -s tests"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import secrets
import tempfile
import time
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
H = {"x-hrs": "1"}
CB = "https://claude.ai/api/mcp/auth_callback"
JSON_CT = {"content-type": "application/json"}
ENV_KEYS = ("HRS_DB_PATH", "HRS_PHOTOS_DIR", "HRS_COOKIE_SECURE", "HRS_PUBLIC_URL")


class McpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ["HRS_DB_PATH"] = str(Path(self.tmp.name) / "test.db")
        os.environ["HRS_PHOTOS_DIR"] = str(Path(self.tmp.name) / "photos")
        os.environ["HRS_COOKIE_SECURE"] = "0"
        os.environ["HRS_PUBLIC_URL"] = "http://testserver"
        from app.auth import throttle
        throttle._fails.clear()
        from app import cli
        with contextlib.redirect_stdout(io.StringIO()):
            cli.main(["import-seed", str(ROOT / "seed")])
            cli.main(["create-user", "--username", "owen", "--name", "Owen", "--role", "owner",
                      "--password", "owner-pass"])
            cli.main(["create-user", "--username", "maria", "--name", "Maria", "--role", "staff",
                      "--password", "staff-pass"])
        from starlette.testclient import TestClient
        from app.main import create_app
        self.client_cm = TestClient(create_app())
        self.c = self.client_cm.__enter__()
        self.token = self.connect()["access_token"]

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.tmp.cleanup()
        for k, v in self.saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def connect(self) -> dict:
        """The whole OAuth dance, the way Claude does it. Returns the token response."""
        r = self.c.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [CB]})
        self.assertEqual(r.status_code, 201, r.text)
        client_id = r.json()["client_id"]
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        r = self.c.post("/api/login", json={"username": "owen", "password": "owner-pass"}, headers=H)
        self.assertEqual(r.status_code, 200, r.text)
        p = {"response_type": "code", "client_id": client_id, "redirect_uri": CB,
             "code_challenge": challenge, "code_challenge_method": "S256", "state": "s", "scope": "menus"}
        r = self.c.post("/oauth/authorize", data={**p, "decision": "allow"}, follow_redirects=False)
        self.assertEqual(r.status_code, 302, r.text)
        code = urllib.parse.parse_qs(urllib.parse.urlsplit(r.headers["location"]).query)["code"][0]
        r = self.c.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": client_id, "redirect_uri": CB,
                                              "code_verifier": verifier})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def insert_token(self, username: str, token: str, expires_in: int = 3600) -> None:
        """An access token straight into the table — for cases the consent page will not produce."""
        from app.auth import token_hash
        from app.db import connect, now_iso
        conn = connect()
        try:
            conn.execute("INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) "
                         "VALUES ('direct', 'Claude', '[]', ?) ON CONFLICT(client_id) DO NOTHING", (now_iso(),))
            conn.execute("INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, family, scope, "
                         "expires_at, created_at) VALUES (?, 'access', 'direct', "
                         "(SELECT id FROM users WHERE username = ?), ?, 'menus', ?, ?)",
                         (token_hash(token), username, f"fam-{token}", int(time.time()) + expires_in, now_iso()))
        finally:
            conn.close()

    def rpc(self, body, token: str | None = "", **kwargs):
        """POST one JSON-RPC message. token="" means this test's own owner token; None means no header."""
        headers = dict(JSON_CT)
        use = self.token if token == "" else token
        if use:
            headers["authorization"] = "Bearer " + use
        return self.c.post("/mcp", content=json.dumps(body), headers=headers, **kwargs)

    # ---- the 401 handshake ----
    def test_no_token_starts_the_sign_in(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, token=None)
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json(), {"error": "unauthorized"})
        self.assertEqual(r.headers["www-authenticate"],
                         'Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                         'scope="menus"')
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_a_bad_token_is_invalid_token(self):
        self.insert_token("maria", "staff-token")
        self.insert_token("owen", "stale-token", expires_in=-10)
        for token in ("garbage", "staff-token", "stale-token"):
            with self.subTest(token=token):
                r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, token=token)
                self.assertEqual(r.status_code, 401)
                self.assertEqual(r.json(), {"error": "invalid_token"})
                self.assertEqual(r.headers["www-authenticate"],
                                 'Bearer error="invalid_token", '
                                 'resource_metadata="http://testserver/.well-known/oauth-protected-resource", '
                                 'scope="menus"')

    # ---- the handshake ----
    def test_initialize_negotiates_the_protocol_version(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "claude-ai", "version": "1"}}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/json")
        result = r.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-03-26")   # a version we know is echoed back
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["serverInfo"], {"name": "Galleon", "title": "Galleon", "version": "1.0.0"})
        self.assertIn("call get_brief first", result["instructions"])
        r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "initialize",
                      "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(r.json()["id"], 2)
        self.assertEqual(r.json()["result"]["protocolVersion"], "2025-06-18")  # anything else gets LATEST

    def test_a_notification_gets_202_and_an_empty_body(self):
        r = self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(r.status_code, 202)
        self.assertEqual(r.content, b"")

    def test_ping_answers_an_empty_result(self):
        r = self.rpc({"jsonrpc": "2.0", "id": "p1", "method": "ping"})
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": "p1", "result": {}})

    def test_tools_list_describes_both_tools(self):
        result = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]
        self.assertNotIn("nextCursor", result)
        tools = result["tools"]
        self.assertEqual([t["name"] for t in tools], ["get_brief", "save_session"])
        self.assertEqual([t["title"] for t in tools], ["The household's prep brief", "Save a prep session"])
        self.assertIn("Call this first", tools[0]["description"])
        self.assertIn("call save_session again", tools[1]["description"])
        self.assertEqual(set(tools[0]["inputSchema"]["properties"]), {"date"})
        self.assertEqual(tools[1]["inputSchema"]["required"],
                         ["week", "session", "recipes", "timeline", "shopping", "leftovers"])
        self.assertEqual(set(tools[1]["inputSchema"]["properties"]),
                         set(tools[1]["inputSchema"]["required"]))
        self.assertIs(tools[1]["inputSchema"]["additionalProperties"], False)
        for tool in tools:
            self.assertNotIn("outputSchema", tool)  # deliberately none; structuredContent is still returned

    # ---- the envelope ----
    def test_a_malformed_envelope_is_a_json_rpc_error(self):
        r = self.c.post("/mcp", content=b"{not json", headers={**JSON_CT,
                                                               "authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32700, "message": "Parse error."}})
        r = self.rpc([{"jsonrpc": "2.0", "id": 1, "method": "ping"}])
        self.assertEqual(r.json()["error"], {"code": -32600,
                                             "message": "This server does not support batched requests."})
        r = self.rpc("just a string")
        self.assertEqual(r.json()["error"]["code"], -32600)
        self.assertEqual(r.json()["error"]["message"], "Invalid request.")
        r = self.rpc({"jsonrpc": "1.0", "id": 1, "method": "ping"})
        self.assertEqual(r.json()["error"]["message"], "Invalid request.")
        r = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "nope"})
        self.assertEqual(r.json()["error"], {"code": -32601, "message": "Method not found: nope."})

    def test_get_and_delete_are_not_allowed(self):
        for method in ("get", "delete"):
            with self.subTest(method=method):
                r = getattr(self.c, method)("/mcp", headers={"authorization": "Bearer " + self.token})
                self.assertEqual(r.status_code, 405)
                self.assertEqual(r.headers["allow"], "POST")
                self.assertEqual(r.json(), {"error": "method_not_allowed"})

    def test_a_huge_body_is_refused(self):
        r = self.c.post("/mcp", content=b"x" * 300_000,
                        headers={**JSON_CT, "authorization": "Bearer " + self.token})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json(), {"jsonrpc": "2.0", "id": None,
                                    "error": {"code": -32600, "message": "Request body too large."}})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_mcp -v`
Expected: all 9 FAIL — most with `AssertionError: 405 != 200` or `404`, because `/mcp` is not mounted yet (the static mount answers it).

- [ ] **Step 3: Write `app/mcp.py`**

Create `app/mcp.py`:

```python
"""The MCP endpoint Claude talks to.

JSON-RPC 2.0 over POST: one request in, one JSON response out. Streamable HTTP in its stateless JSON
form — no SSE, no session ids, no server-initiated notifications. Owner-only, bearer-authenticated.
"""
from __future__ import annotations

import json
import logging

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import menu, oauth

log = logging.getLogger("house_run_sheet")

MAX_BODY_BYTES = 256 * 1024
# If modelcontextprotocol.io has published a newer revision, prepend it here: the echo rule below
# then serves old and new clients with no other change.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
LATEST = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INFO = {"name": "Galleon", "title": "Galleon", "version": "1.0.0"}
INSTRUCTIONS = ("Galleon is a household app for one home in Los Angeles. To plan a Tuesday or Friday "
                "batch-prep session, call get_brief first, work the recipes and macros out with the user "
                "in the chat, then post the session with save_session.")

GET_BRIEF_DESCRIPTION = (
    "Call this first, before planning anything. Returns the household's brief for one batch-prep "
    "session: the date and week, how many portions of breakfast, main and dessert to make, the kcal and "
    "protein target every single portion must hit, the store, likes, dislikes and pantry, the titles "
    "already saved for this session and for the week's other session (with its leftovers to use up), "
    "recent titles not to repeat, favourites, and the rules to follow. Pass `date` to plan a particular "
    "Tuesday or Friday; leave it out for the next one. If `existing` comes back non-null, tell the user "
    "that saving will replace what is already there.")

SAVE_SESSION_DESCRIPTION = (
    "Post one finished batch-prep session (a Tuesday or a Friday) to Galleon. Replaces that session's "
    "recipes, timeline, leftovers and shopping list; the week's other session and its shopping ticks are "
    "left alone. The app re-adds your per-ingredient kcal and protein and refuses the whole session if "
    "any recipe is off target or any field is too long — nothing is saved in that case. Validation "
    "errors list every problem, one per line: fix them all and call save_session again. Call "
    "get_brief first.")

# No outputSchema on either tool on purpose: structuredContent is still returned, and declaring a
# schema would only add a surface a client could fail us on.
TOOLS = [
    {"name": "get_brief", "title": "The household's prep brief",
     "description": GET_BRIEF_DESCRIPTION, "inputSchema": menu.GET_BRIEF_SCHEMA},
    {"name": "save_session", "title": "Save a prep session",
     "description": SAVE_SESSION_DESCRIPTION, "inputSchema": menu.SAVE_SESSION_SCHEMA},
]
TOOL_NAMES = tuple(t["name"] for t in TOOLS)


def jsonrpc_result(rid, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "result": result})


def jsonrpc_error(rid, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}},
                        status_code=status)


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None when it is over the cap — declared or measured while streaming."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            return None  # stop reading; don't keep the rest
        chunks.append(chunk)
    return b"".join(chunks)


async def mcp_post(request: Request) -> Response:
    # Order matters: size, then bearer, then parse, then dispatch. Size first so an anonymous request
    # can't make a worker buffer megabytes; bearer before parse so a token-less client always gets the
    # sign-in handshake rather than a parse error.
    raw = await _read_body(request)
    if raw is None:
        return jsonrpc_error(None, -32600, "Request body too large.", status=413)
    user, refusal = await oauth.require_owner_bearer(request)
    if refusal is not None:
        return refusal
    asked_version = request.headers.get("mcp-protocol-version")
    if asked_version:
        log.info("MCP request with MCP-Protocol-Version: %s", asked_version)
    try:
        msg = json.loads(raw)
    except ValueError:
        return jsonrpc_error(None, -32700, "Parse error.")
    if isinstance(msg, list):
        return jsonrpc_error(None, -32600, "This server does not support batched requests.")
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return jsonrpc_error(None, -32600, "Invalid request.")
    rid = msg.get("id")
    if rid is None:
        # A notification: nothing to answer, whatever the method. notifications/initialized lands here.
        return Response(status_code=202)
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return jsonrpc_result(rid, {
            "protocolVersion": asked if asked in SUPPORTED_PROTOCOL_VERSIONS else LATEST,
            "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return jsonrpc_result(rid, {})
    if method == "tools/list":
        return jsonrpc_result(rid, {"tools": TOOLS})  # two tools, no pagination, no nextCursor
    return jsonrpc_error(rid, -32601, f"Method not found: {method}.")


async def mcp_not_allowed(request: Request) -> Response:
    return JSONResponse({"error": "method_not_allowed"}, status_code=405, headers={"Allow": "POST"})


routes = [
    Route("/mcp", mcp_post, methods=["POST"]),
    Route("/mcp", mcp_not_allowed, methods=["GET", "DELETE"]),
]
```

- [ ] **Step 4: Mount it**

In `app/main.py`, change the import and the route list:

```python
from . import api, mcp, oauth
```

```python
    app = Starlette(routes=[*api.routes, *oauth.routes, *mcp.routes,
                            Mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")],
                    lifespan=lifespan)
```

- [ ] **Step 5: Run the MCP tests to verify they pass**

Run: `python3 -m unittest tests.test_mcp -v`
Expected: 9 tests, `OK`.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 148 tests` … `OK` (139 + 9).

- [ ] **Step 7: Commit**

```bash
git add app/mcp.py app/main.py tests/test_mcp.py
git commit -m "Answer Claude's MCP handshake and list the two menu tools" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 10: `tools/call` — `get_brief` and `save_session` end to end

**Files:**
- Modify: `app/mcp.py` (append the tool machinery; add the `tools/call` branch to `mcp_post`)
- Modify: `tests/test_mcp.py` (helpers + 6 tests)

**Interfaces:**
- Consumes: `mcp.TOOL_NAMES`, `mcp.jsonrpc_result`, `mcp.jsonrpc_error` (Task 9); `menu.MenuError`, `menu.brief(conn, date) -> dict` (Task 3); `menu.normalize_session(args) -> tuple[dict, list[dict]]`, `menu.validate_session(normalized, settings) -> list[str]`, `menu.problem_report(problems) -> str`, `menu.session_data(normalized, settings) -> dict`, `menu.save_result(week, session, session_data, shopping) -> tuple[str, dict]` (Task 4); `store.get_settings(conn)`, `store.save_session(conn, week, session, session_data, shopping, user_id)` (Tasks 2 and 4); `app.db.connect`, `tx`.
- Produces: `mcp.tool_error(text) -> dict`, `mcp.tool_ok(text, structured) -> dict`, `mcp.call_tool(name, args, user) -> dict`, `mcp.HANDLERS`.

**Context:** Tool handlers run through `run_in_threadpool` with their **own** `connect()`, closed in a `finally`, exactly like `api.endpoint`. A validation failure is **not** a JSON-RPC error: it is HTTP 200, a JSON-RPC *result*, with `isError: true` and the problem list as its one text block — that is how the MCP spec says a tool reports a failure it wants the model to fix. An unexpected exception is logged with `log.exception` and returned as the tool error `Something went wrong saving that session. Try again.` — never a stack trace. That one sentence is used for both tools (it is the spec's wording); `get_brief` has no write path, so in practice only `save_session` can reach it.

Claude's tool-call timeout is 240 s and a tool result may be ~150,000 characters; the brief is a few kilobytes, so neither is close.

- [ ] **Step 1: Write the failing tests**

Add these two helpers to `class McpTest` in `tests/test_mcp.py`, after `rpc` (the same shapes `tests/test_menu.py` uses — repeated on purpose, test files never import from each other):

```python
    def recipe(self, title: str, portions: int, kcal: float, protein: float) -> dict:
        return {"title": title, "blurb": "One short line.", "portions": portions,
                "portionNote": f"{portions} containers, one a day.",
                "ingredients": [{"item": "Everything", "amount": "1 batch (1000 g)",
                                 "kcal": kcal * portions, "protein": protein * portions}],
                "steps": ["Cook it.", "Portion it out by weight."], "storage": "Fridge for 3 days."}

    def payload(self, **overrides) -> dict:
        args = {"week": "2026-10-05", "session": "tue",
                "recipes": {"breakfast": self.recipe("Vanilla blueberry overnight oats", 3, 500, 52),
                            "main": self.recipe("Beef burritos", 9, 500, 51),
                            "dessert": self.recipe("Chocolate overnight oats", 3, 500, 49)},
                "timeline": ["Start the oats.", "Brown the beef.", "Roll the burritos.", "Label everything."],
                "shopping": [{"item": "Amazon Grocery 93/7 Ground Beef, 1 lb", "buy": "2 lb", "aisle": "Meat",
                              "stock": False, "search": "Amazon Grocery 93/7 ground beef 1 lb"},
                             {"item": "Mission Carb Balance Tortillas, 8 ct", "buy": "1 pack", "aisle": "Bakery",
                              "stock": False, "search": "Mission Carb Balance flour tortillas"}],
                "leftovers": ["About 170 g Greek yogurt"]}
        args.update(overrides)
        return args
```

Then these 6 tests:

```python
    # ---- tools/call ----
    def test_get_brief_returns_text_and_structured_content(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": {"date": "2026-09-29"}}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertNotIn("isError", result)
        self.assertEqual(len(result["content"]), 1)
        self.assertEqual(result["content"][0]["type"], "text")
        self.assertEqual(json.loads(result["content"][0]["text"]), result["structuredContent"])
        brief = result["structuredContent"]
        self.assertEqual(brief["week"], "2026-09-28")
        self.assertEqual(brief["session"], "tue")
        self.assertEqual(brief["date"], "2026-09-29")
        self.assertEqual(brief["portions"], {"breakfast": 3, "main": 9, "dessert": 3})
        self.assertEqual(brief["targets"]["kcal"], 500)
        self.assertIn("Every single portion", brief["rules"])
        self.assertEqual(brief["existing"]["titles"]["main"], "Chipotle chicken burrito bowls")
        self.assertEqual(brief["otherSession"]["session"], "fri")
        # no arguments at all is legal: the brief resolves the next prep day itself
        result = self.rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "get_brief"}}).json()["result"]
        self.assertNotIn("isError", result)
        self.assertIn(result["structuredContent"]["session"], ("tue", "fri"))

    def test_get_brief_on_a_wednesday_is_a_tool_error(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": {"date": "2026-09-30"}}})
        self.assertEqual(r.status_code, 200)
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertEqual(result["content"][0]["text"],
                         "2026-09-30 is a Wednesday. Prep sessions are Tuesdays and Fridays — "
                         "pick one of those.")

    def test_save_session_writes_the_plan(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": self.payload()}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertNotIn("isError", result)
        self.assertIn("Saved Tuesday 6 Oct (week of 5 Oct)", result["content"][0]["text"])
        self.assertIn("Open http://testserver/#meals", result["content"][0]["text"])
        structured = result["structuredContent"]
        self.assertEqual(structured["week"], "2026-10-05")
        self.assertEqual(structured["session"], "tue")
        self.assertEqual(structured["date"], "2026-10-06")
        self.assertEqual(structured["shoppingCount"], 2)
        self.assertEqual(structured["url"], "http://testserver/#meals")
        self.assertEqual(structured["recipes"]["main"], {"title": "Beef burritos", "portions": 9,
                                                        "kcalPerPortion": 500, "proteinPerPortion": 51})
        # …and the app reads it straight back
        plan = self.c.get("/api/plans/2026-10-05").json()["plan"]
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["breakfast"]["title"],
                         "Vanilla blueberry overnight oats")
        self.assertEqual(plan["sessions"]["tue"]["covers"], "Wed, Thu, Fri")
        self.assertEqual(plan["sessions"]["tue"]["leftovers"], ["About 170 g Greek yogurt"])
        self.assertEqual([i["id"] for i in plan["shopping"]], ["t01", "t02"])
        self.assertEqual(plan["source"], "Claude")

    def test_an_off_target_session_saves_nothing(self):
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "save_session", "arguments": self.payload()}})
        bad = self.payload(recipes={"breakfast": self.recipe("Oats", 3, 500, 52),
                                    "main": self.recipe("Burritos", 9, 612, 51),
                                    "dessert": self.recipe("Pots", 3, 500, 44)})
        r = self.rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": bad}})
        self.assertEqual(r.status_code, 200, r.text)
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertNotIn("structuredContent", result)
        self.assertEqual(result["content"][0]["text"],
                         "Nothing was saved. Fix these and call save_session again:\n"
                         "main: 612 kcal per portion, target 500 ±35.\n"
                         "dessert: 44 g protein per portion, need at least 47.")
        # the session that was already there is untouched
        plan = self.c.get("/api/plans/2026-10-05").json()["plan"]
        self.assertEqual(plan["sessions"]["tue"]["recipes"]["main"]["title"], "Beef burritos")

    def test_a_wrong_argument_shape_is_a_tool_error(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                      "params": {"name": "save_session", "arguments": {"week": "2026-10-05"}}})
        result = r.json()["result"]
        self.assertIs(result["isError"], True)
        self.assertEqual(result["content"][0]["text"],
                         "save_session needs an object with week, session, recipes, timeline, "
                         "shopping and leftovers.")

    def test_unknown_tool_and_bad_arguments(self):
        r = self.rpc({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                      "params": {"name": "delete_everything", "arguments": {}}})
        self.assertEqual(r.json()["error"], {"code": -32602, "message": "Unknown tool: delete_everything."})
        r = self.rpc({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                      "params": {"name": "get_brief", "arguments": "2026-09-29"}})
        self.assertEqual(r.json()["error"], {"code": -32602, "message": "arguments must be an object."})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_mcp -v`
Expected: the 6 new tests FAIL — five with a `-32601 Method not found: tools/call.` error instead of a result, and `test_unknown_tool_and_bad_arguments` with `AssertionError` comparing that same `-32601` against `-32602`.

- [ ] **Step 3: Write the tool machinery**

Append to `app/mcp.py`, and extend its imports to:

```python
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from . import menu, oauth, store
from .db import connect, tx
```

```python
# ---------- the two tools ----------
def tool_error(text: str) -> dict:
    """A tool failure: HTTP 200, a JSON-RPC result, isError true. Claude reads the text and retries."""
    return {"content": [{"type": "text", "text": text}], "isError": True}


def tool_ok(text: str, structured: dict) -> dict:
    return {"content": [{"type": "text", "text": text}], "structuredContent": structured}


def _get_brief(conn, args: dict, user) -> dict:
    given = args.get("date")
    if given is not None and not isinstance(given, str):
        raise menu.MenuError('date must be a string like "2026-09-29".')
    data = menu.brief(conn, given)
    # One text block holding the JSON, plus the same object as structuredContent: clients that
    # ignore structuredContent read the text.
    return tool_ok(json.dumps(data, indent=2), data)


def _save_session(conn, args: dict, user) -> dict:
    normalized, shopping = menu.normalize_session(args)
    settings = store.get_settings(conn)
    problems = menu.validate_session(normalized, settings)
    if problems:
        return tool_error(menu.problem_report(problems))  # nothing is written when anything is wrong
    week, session = normalized["week"], normalized["session"]
    data = menu.session_data(normalized, settings)
    with tx(conn):
        store.save_session(conn, week, session, data, shopping, user["id"])
    log.info("saved the %s session of week %s from the Claude connector", session, week)
    text, structured = menu.save_result(week, session, data, shopping)
    return tool_ok(text, structured)


HANDLERS = {"get_brief": _get_brief, "save_session": _save_session}


def call_tool(name: str, args: dict, user) -> dict:
    """Run one tool on its own connection, in a worker thread, exactly like api.endpoint. Never raises."""
    conn = connect()
    try:
        return HANDLERS[name](conn, args, user)
    except menu.MenuError as e:
        return tool_error(str(e))
    except Exception:  # noqa: BLE001 - a stack trace must never reach the chat
        log.exception("MCP tool %s failed", name)
        return tool_error("Something went wrong saving that session. Try again.")
    finally:
        conn.close()
```

- [ ] **Step 4: Dispatch it**

In `mcp_post`, insert this branch immediately before the final `return jsonrpc_error(rid, -32601, …)`:

```python
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOL_NAMES:
            return jsonrpc_error(rid, -32602, f"Unknown tool: {name}.")
        args = params.get("arguments", {})
        if not isinstance(args, dict):
            return jsonrpc_error(rid, -32602, "arguments must be an object.")
        return jsonrpc_result(rid, await run_in_threadpool(call_tool, name, args, user))
```

- [ ] **Step 5: Run the MCP tests to verify they pass**

Run: `python3 -m unittest tests.test_mcp -v`
Expected: 15 tests, `OK`.

- [ ] **Step 6: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 154 tests` … `OK` (148 + 6).

- [ ] **Step 7: Commit**

```bash
git add app/mcp.py tests/test_mcp.py
git commit -m "Let Claude read the brief and post a finished prep session" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 11: The backend gate

**Files:** none — this task only verifies. If something fails, fix it in the file it belongs to and commit that fix.

**Interfaces:**
- Consumes: everything Tasks 1–10 produced.
- Produces: a verified branch. Nothing new.

**Context:** The React app is *not* part of this plan and still calls the deleted `/api/plans/{week}/generate`, but it is unchanged TypeScript, so `tsc -b && vite build` (the Dockerfile's node stage) still succeeds. That is why a Docker build is a fair gate here. Do **not** "fix" the frontend — a separate plan does that.

- [ ] **Step 1: The whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 154 tests` … `OK`. No warnings about `HRS_PUBLIC_URL`, no `ResourceWarning`, no stray thread output.

- [ ] **Step 2: A genuinely fresh database**

Run:

```bash
rm -rf /tmp/hrs-gate && HRS_DB_PATH=/tmp/hrs-gate/house.db python3 -m app.cli import-seed seed/
HRS_DB_PATH=/tmp/hrs-gate/house.db python3 -m app.cli create-user --username owen --name Owen \
  --role owner --password owner-pass
python3 - <<'PY'
import sqlite3
c = sqlite3.connect("/tmp/hrs-gate/house.db")
print("version", c.execute("PRAGMA user_version").fetchone()[0])
print("tables", sorted(r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'oauth%' OR name = 'ai_jobs'")))
print("fk_check", c.execute("PRAGMA foreign_key_check").fetchall())
PY
```

Expected exactly:

```
Tasks: 41
Settings: imported
Meal plans: 1
Created owner 'owen'.
version 4
tables ['oauth_clients', 'oauth_codes', 'oauth_tokens']
fk_check []
```

- [ ] **Step 3: Final grep sweep**

Run:

```bash
grep -rn "ANTHROPIC\|anthropic\|ai_jobs\|app\.ai\|/api/jobs\|request\.form(" app/ tests/ migrations/004_oauth.sql
grep -rn "python-multipart" requirements.txt
cat requirements.txt
```

Expected: the first grep prints **nothing at all**; the second prints nothing; `requirements.txt` is exactly `starlette==1.0.0`, `uvicorn[standard]==0.46.0`, `httpx==0.28.1`.

- [ ] **Step 4: Build the image**

Run: `docker build -t hrs-connector-check .`
Expected: the node stage runs `tsc -b && vite build` and succeeds; the python stage installs the three dependencies; last line `naming to docker.io/library/hrs-connector-check done` (or `Successfully tagged hrs-connector-check:latest` on an older Docker).

- [ ] **Step 5: Run it and smoke-test the connector surface**

```bash
mkdir -p /tmp/hrs-check-data && chmod 777 /tmp/hrs-check-data   # the image runs as uid 10001
docker run -d --name hrs-check -p 127.0.0.1:8001:8000 \
  -e HRS_PUBLIC_URL=http://localhost:8001 -e HRS_COOKIE_SECURE=0 \
  -v /tmp/hrs-check-data:/data hrs-connector-check
for i in $(seq 1 40); do curl -sf http://localhost:8001/healthz >/dev/null && break; done
docker logs hrs-check 2>&1 | tail -3
echo "--- AS metadata"
curl -s http://localhost:8001/.well-known/oauth-authorization-server
echo; echo "--- protected resource"
curl -s http://localhost:8001/.well-known/oauth-protected-resource/mcp
echo; echo "--- unauthenticated POST /mcp"
curl -si -X POST http://localhost:8001/mcp -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize"}' | grep -Ei "^HTTP|^www-authenticate|^cache-control|^\{"
echo "--- GET /mcp"
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8001/mcp
echo "--- the consent page's script"
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8001/oauth.js
```

Expected output, exactly (the log line's timestamp aside):

```
--- AS metadata
{"issuer":"http://localhost:8001","authorization_endpoint":"http://localhost:8001/oauth/authorize","token_endpoint":"http://localhost:8001/oauth/token","registration_endpoint":"http://localhost:8001/oauth/register","response_types_supported":["code"],"grant_types_supported":["authorization_code","refresh_token"],"code_challenge_methods_supported":["S256"],"token_endpoint_auth_methods_supported":["none"],"scopes_supported":["menus"]}
--- protected resource
{"resource":"http://localhost:8001/mcp","authorization_servers":["http://localhost:8001"],"scopes_supported":["menus"],"bearer_methods_supported":["header"],"resource_name":"Galleon"}
--- unauthenticated POST /mcp
HTTP/1.1 401 Unauthorized
www-authenticate: Bearer resource_metadata="http://localhost:8001/.well-known/oauth-protected-resource", scope="menus"
cache-control: no-store
{"error":"unauthorized"}
--- GET /mcp
405
--- the consent page's script
200
```

and `docker logs` must contain `database ready at schema version 4` and **no** `HRS_PUBLIC_URL is not set` warning. The `/oauth.js` 200 proves Vite copied `frontend/public/oauth.js` into `static/`.

- [ ] **Step 6: Clean up**

```bash
docker stop hrs-check && docker rm hrs-check
docker rmi hrs-connector-check
rm -rf /tmp/hrs-check-data /tmp/hrs-gate /tmp/hrs-seed-check
```

Expected: the container stops and is removed; nothing is left in `/tmp`. **Never** commit anything from `data/`.

- [ ] **Step 7: Commit**

Only if this gate needed a fix:

```bash
git add -A
git commit -m "Fix <what the gate caught>" -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

If the gate was clean there is nothing to commit — say so, and report that the backend half of the connector is done and the frontend/docs plan is next. `git status` must be clean and `git log --oneline -11` must show this plan's eleven commits (Task 11's included only if it had a fix).

---

## What this plan deliberately leaves undone

- Everything in spec §9 (`GenPanel.tsx`, `useGeneration.ts`, `MealsScreen.tsx`, `ShoppingScreen.tsx`, `ClaudeCard.tsx`, `api/types.ts`) and the documentation half of §10 (`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `.env.example`) — the frontend plan.
- §11's frontend gate (`npm run typecheck && npm test && npm run build`, then `grep -R "<script" static/index.html`) — same plan.
- §11's manual verification (MCP Inspector against a local `docker compose up`, then adding the real connector at claude.ai) and §12's droplet deploy — done by hand, by Owen, after both plans land.
- §13 records the decisions this plan implements; §14's non-goals stay non-goals. The Cloudflare IP rule in §12 is explicitly out of scope.











