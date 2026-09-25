# Agent notes

- Python 3.11+, Starlette + stdlib `sqlite3` + `httpx`. No ORM. Keep dependencies minimal.
- Run tests: `python -m unittest discover -s tests -v`. Add a test for every new route, including the housekeeper being refused where relevant.
- Schema changes: add `migrations/NNN_description.sql` (next number). Never edit an applied migration.
- Every API handler uses the `endpoint(role=...)` decorator in `app/api.py`; homeowner-only routes must pass `role="homeowner"`.
- Front end is `static/app.js` (vanilla JS, no build). All user text goes through `esc()` before `innerHTML`.
- Non-GET requests must send `X-HRS: 1` (CSRF guard).
- Visit days are Tuesday and Friday; weeks are keyed by their Monday (`YYYY-MM-DD`).
- Never commit `.env` or anything in `data/`.
