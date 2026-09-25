# Agent notes

- Python 3.11+, Starlette + stdlib `sqlite3` + `httpx`. No ORM. Keep dependencies minimal.
- Run tests: `python3 -m unittest discover -s tests -v`. Add a test for every new route, including staff being refused where relevant.
- Schema changes: add `migrations/NNN_description.sql` (next number). Never edit an applied migration. `migrate()` runs each script with foreign keys off and then checks `PRAGMA foreign_key_check`.
- Every API handler uses the `endpoint(role=...)` decorator in `app/api.py`; owner-only routes must pass `role="owner"` (the refusal reads "Only an owner can do that."). Roles are exactly `owner` and `staff`.
- Endpoints that take raw bytes instead of JSON (photo upload) use `endpoint(raw_body=True)`.
- Visit photos are files under `HRS_PHOTOS_DIR` (default `data/photos`) with random names (`secrets.token_hex(16)`), served by `GET /photos/{filename}` — registered before the static mount and outside `/api/` on purpose, so the `no-store` header doesn't stop the browser caching them. The handler looks the filename up in `visit_photos` before touching the disk; keep it that way.
- `user_dict` (everyone's phone and door code) is owner-only output; `me_dict` gives a person only their own. Don't widen either.
- Never log a door code or a sign-in code, and never log a full phone number — redact it the way `app/sms.py: _redact` does.
- Front end is `static/app.js` (vanilla JS, no build). All user text goes through `esc()` before `innerHTML`.
- Non-GET requests must send `X-HRS: 1` (CSRF guard), including the raw-body photo upload.
- Visit days are Tuesday and Friday; weeks are keyed by their Monday (`YYYY-MM-DD`).
- Never commit `.env` or anything in `data/`.
