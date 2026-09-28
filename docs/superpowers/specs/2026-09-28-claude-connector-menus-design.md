# Claude connector menus — design spec

Date: 2026-09-28. Status: approved by the homeowner (Owen) in full; ready for an implementation plan. Branch: `claude-connector`.

## 1. Summary

Today the app writes menus itself: `POST /api/plans/{week}/generate` starts a background thread in `app/ai.py`, that thread calls the Anthropic Messages API with a long prompt, and the Meals tab polls `/api/jobs/{id}` for five minutes. It costs money per menu, Owen can't steer it once it starts, and the JSON it returns is trusted almost unexamined.

This replaces all of it with a **remote MCP connector**. Owen plans a prep session conversationally in the Claude app ("vanilla-blueberry overnight oats with Fage 0, Premier Protein vanilla, coconut milk, old-fashioned oats, sugar-free maple syrup, vanilla essence; chocolate version with Ascent chocolate whey and dark chocolate for dessert; burritos with 97/3 beef, Mission Carb Balance tortillas, 2% cottage cheese, fat-free cheddar, peppers, onions, jalapeños"). Claude reads the household's brief from the app with `get_brief`, works out whole-batch weights, per-ingredient macros, steps, timeline, shopping list and leftovers **in the chat**, then posts the finished session with `save_session`. The app re-adds the macros against the household targets and **refuses** anything off target with a precise message, so Claude can fix it and post again. Nothing is generated server-side and no Anthropic API key is involved.

The app becomes its own OAuth 2.1 authorization server so Claude can sign Owen in once and keep a refresh token.

## 2. Decisions (do not re-open)

| # | Decision | Why |
|---|---|---|
| 1 | **Claude does the arithmetic in the chat; the app verifies.** | A product catalogue + portion solver in the app was considered and rejected as too much build for now (it can be layered on later). The app recomputes per-portion kcal and protein from Claude's per-ingredient numbers and refuses anything off target, so the guarantee survives without the catalogue. |
| 2 | **OAuth 2.1**, not a secret URL and not a pasted token. | Anthropic's connector docs (<https://claude.com/docs/connectors/building/authentication>) say custom connectors on a personal plan support OAuth 2.0 (DCR or CIMD) or no auth; static header auth is beta/org-only and tokens in URLs are prohibited. The app already has a login to reuse. |
| 3 | **One session at a time.** | `save_session` posts one prep session (`tue` or `fri`) for one week. The app merges it and leaves the week's other session — and its shopping ticks — alone. `both` is retired as a value for new shopping items; leftovers become per-session. |
| 4 | **Hand-rolled in Starlette**, no `mcp` SDK. | Keeps the deliberate three-dependency footprint (`starlette`, `uvicorn`, `httpx`). The SDK does not provide login, consent or token issuance, which is the bulk of this work. Transport is Streamable HTTP in its stateless JSON form: one JSON-RPC request in, one JSON response out, no SSE, no session ids. |
| 5 | **No fallback.** In-app generation is deleted outright. | `POST /api/plans/{week}/generate`, `/api/jobs/*`, the `ai_jobs` table, `ANTHROPIC_API_KEY`/`ANTHROPIC_MODEL`, `GenPanel.tsx`, `useGeneration.ts`, the `Job`/`JobStatus`/`JobStarted` types and the start-up orphan-job sweep all go. |
| 6 | **Two tools only, both owner-only:** `get_brief`, `save_session`. | |
| 7 | **`save_session` rejects rather than saving something flagged.** | A saved-but-wrong menu is worse than a retry: Claude can fix and re-post in seconds. |
| 8 | **The OAuth consent page is a small server-rendered HTML page**, not the SPA, with the SMS login inline. | The SPA boots from `/api/me` and owns the hash router; threading a consent step through it would be far more code than one HTML template. |

## 3. Facts about Claude's connector client (from Anthropic's docs — treat as requirements)

- Claude connects from Anthropic's cloud (egress `160.79.104.0/21`) on behalf of claude.ai web, Desktop, mobile, Cowork and Claude Code. Owen adds it at claude.ai → Settings → Connectors → Add custom connector → URL.
- Transport: Streamable HTTP over POST. Tool call timeout **240 s**; tool result max **~150,000 characters**.
- Sign-in starts **only** from a `401` whose `WWW-Authenticate` header carries `Bearer resource_metadata="<url>"`. A `WWW-Authenticate` on a 200 is ignored. Claude also probes `/.well-known/oauth-protected-resource/<mcp-path>` and falls back to `/.well-known/oauth-protected-resource`.
- Protected resource metadata's `resource` must equal the URL Owen typed, **including the path**. Claude uses only the **first** entry of `authorization_servers`.
- Authorization server metadata lives at `/.well-known/oauth-authorization-server` (RFC 8414) and must advertise `code_challenge_methods_supported: ["S256"]`.
- Claude registers by Dynamic Client Registration (RFC 7591, `POST` JSON to `registration_endpoint`) as a **public** client, so `token_endpoint_auth_methods_supported` must include `"none"`.
- Redirect URI for the hosted apps is exactly `https://claude.ai/api/mcp/auth_callback`. Claude Code uses a loopback redirect `http://localhost/callback` or `http://127.0.0.1/callback` on an ephemeral port (match with the port ignored).
- Every authorization request carries PKCE `code_challenge` with `code_challenge_method=S256`.
- `/token` must accept `application/x-www-form-urlencoded` for both the code exchange and the refresh, and return RFC 6749 error codes (`invalid_grant` when a refresh token is no longer valid). Refresh tokens must rotate for public clients, with the new one returned in the same response. Claude refreshes reactively on a 401 and proactively up to 5 minutes before expiry.
- Claude waits 10 s for discovery, registration and token calls; 30 s for a refresh.
- Scope control: put a `scope` parameter in the `WWW-Authenticate` header on the 401, otherwise Claude takes `scopes_supported` from the protected resource metadata.
- Tool calls that write are approved by the user in the Claude UI (with "Allow always"). Nothing for us to build.

## 4. Architecture

One Starlette process, one SPA, as today. Two new backend modules mounted alongside `/api`:

- **`app/oauth.py`** — the app is its own OAuth 2.1 authorization server *and* resource server. Routes: `GET /.well-known/oauth-protected-resource`, `GET /.well-known/oauth-protected-resource/mcp`, `GET /.well-known/oauth-authorization-server`, `POST /oauth/register`, `GET /oauth/authorize`, `POST /oauth/authorize`, `POST /oauth/token`. Deliberately outside `/api` so the `X-HRS` CSRF rule does not apply to a browser form post and a browser navigation.
- **`app/mcp.py`** — `POST /mcp`, JSON-RPC 2.0. Bearer-authenticated (§7).
- **`app/ai.py` becomes `app/menu.py`** — keeps `AISLES`, `SLOTS`, `add_days`; gains `RULES`, `today_la()`, `resolve_prep_date()`, `brief()`, `normalize_session()`, `validate_session()`. Everything Anthropic-shaped (`API_URL`, `build_prompt`, `extract_json`, `normalize_plan`, `call_model`, `_apply_delta`, `_api_error_message`, `_Cancelled`, `recent_context`, `start_job`, `run_job`, `GenerationError`) is deleted. `recent_context`'s query logic is reused inside `brief()`.

`app/main.py` mounts them in order: `[*api.routes, *oauth.routes, *mcp.routes, Mount("/", StaticFiles(...))]`.

**Caching and CSP.** `SecurityHeaders` in `app/main.py` currently sets `Cache-Control: no-store` only for `/api/`. Widen it:

```python
NO_STORE_PREFIXES = ("/api/", "/oauth/", "/mcp", "/.well-known/")
no_store = scope["path"].startswith(NO_STORE_PREFIXES)
```

and serve a second, tighter CSP for `/oauth/` paths, because the consent form's Allow button 302s to claude.ai and `form-action` is checked against the submission target:

```python
OAUTH_CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
             "img-src 'self'; connect-src 'self'; "
             "form-action 'self' https://claude.ai http://localhost:* http://127.0.0.1:*; "
             "frame-ancestors 'none'; base-uri 'none'")
```

The app-wide `CSP` constant is unchanged for every other path.

**The one-time connect flow.** Owen adds `https://galleon.casa/mcp` in Claude. Claude POSTs to `/mcp` with no token → 401 + `resource_metadata` → reads both metadata documents → `POST /oauth/register` → opens Owen's browser at `/oauth/authorize?...`. That page checks the `hrs_session` cookie:

- valid **owner** session → consent page: "Allow Claude to read and write Galleon menus", **Allow** / **Deny**;
- no or invalid cookie → inline phone → SMS code form, which calls the existing `POST /api/login/sms/start` and `POST /api/login/sms/check` by `fetch` with `X-HRS: 1` (they set the cookie), then reloads the page to show consent. Under it, "Owner? Sign in with a password" reveals a username + password form calling the existing `POST /api/login`;
- valid **staff** session → "Only an owner can connect Claude."

Allow → authorization code → 302 to the redirect URI with `code` and `state` → Claude exchanges it at `/oauth/token`.

The page is served under `OAUTH_CSP` with `script-src 'self'`, so its script must be an external file. Put it at **`frontend/public/oauth.js`**, which Vite copies verbatim into `static/` (the same route `icon.svg` and `manifest.webmanifest` already take) and the existing static mount serves at **`/oauth.js`**. The page's CSS is an inline `<style>` block, which `style-src 'unsafe-inline'` allows.

**Every menu after that.** In any Claude chat Owen describes the session; Claude calls `get_brief`, drafts in the chat, calls `save_session`; the app validates and merges; Owen opens Meals.

## 5. Tool contract

### 5.1 `get_brief`

Input schema (JSON Schema draft 2020-12):

```json
{
  "type": "object",
  "properties": {
    "date": {
      "type": "string",
      "pattern": "^\\d{4}-\\d{2}-\\d{2}$",
      "description": "The prep day to plan, a Tuesday or a Friday, as YYYY-MM-DD. Omit for the next one."
    }
  },
  "required": [],
  "additionalProperties": false
}
```

Date resolution. `menu.today_la()` returns `datetime.now(ZoneInfo("America/Los_Angeles")).date()` — Owen is in Los Angeles and the container runs UTC, so a UTC date would be a day ahead all evening. `ZoneInfo("America/Los_Angeles")` was verified to work in `python:3.12-slim` (`docker run --rm python:3.12-slim python -c "from zoneinfo import ZoneInfo; ZoneInfo('America/Los_Angeles')"` prints the zone), so **no `tzdata` package is needed** and `requirements.txt` stays at three entries. `resolve_prep_date(given: str | None) -> str`:

- `None` → the next Tuesday or Friday **on or after** `today_la()`; today counts, so calling on a Tuesday plans that Tuesday.
- given → must parse and be a Tuesday or Friday, else a tool error: `2026-09-30 is a Wednesday. Prep sessions are Tuesdays and Fridays — pick one of those.`

Result. One text content block containing the JSON below, plus the same object as `structuredContent`:

```json
{
  "week": "2026-09-28",
  "session": "tue",
  "date": "2026-09-29",
  "covers": "Wed, Thu, Fri",
  "portions": { "breakfast": 3, "main": 9, "dessert": 3 },
  "targets": { "kcal": 500, "protein": 50, "tolerance": { "kcalPercent": 7, "proteinBelow": 3 } },
  "store": "Amazon Fresh",
  "likes": "Mexican, Thai, anything with lime",
  "dislikes": "olives, blue cheese",
  "pantry": "olive oil, salt, pepper, cumin, oats, protein powder",
  "existing": { "titles": { "breakfast": "Peach oats", "main": "Chicken bowls", "dessert": "Brownie pots" } },
  "otherSession": {
    "session": "fri",
    "date": "2026-10-02",
    "titles": { "breakfast": "Berry oats", "main": "Beef chilli", "dessert": "Cheesecake pots" },
    "leftovers": ["About 170 g Greek yogurt", "About 80 g Parmesan"]
  },
  "recentTitles": ["Peach oats", "Chicken bowls", "..."],
  "favourites": ["Beef burritos"],
  "rules": "<the RULES constant, verbatim>"
}
```

- `week` = Monday of `date`; `session` = `tue` when `date` is a Tuesday else `fri`.
- `covers` and `portions` come from `store.get_settings(conn)[session]`.
- `existing` is `null` when this session is not saved; otherwise `{"titles": {...}}` with one entry per saved slot. The tool description tells Claude to warn Owen it will be replaced.
- `otherSession` is `null` when the week's other session is not saved.
- `recentTitles`: every recipe title from the six most recent saved weeks **before** `week`, both sessions, de-duplicated, order newest week first (the same `ORDER BY week DESC` walk `ai.recent_context` used).
- `favourites`: every title with `fav: true` across all saved plans, de-duplicated.
- `rules` is `menu.RULES` verbatim.

### 5.2 `RULES`

`app/menu.py`:

```python
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
```

Dropped from the old prompt on purpose (Owen now decides these in the chat): "Breakfast is always an overnight oats variation", the main and dessert genre lists, "Make Tuesday and Friday clearly different", the one-week framing, the JSON-shape description (now the tool schema) and the `web_search` tool mention.

### 5.3 `save_session`

Input schema (draft 2020-12; `additionalProperties: false` everywhere; every listed property required):

```json
{
  "type": "object",
  "properties": {
    "week": { "type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$", "description": "Monday of the week, from get_brief." },
    "session": { "type": "string", "enum": ["tue", "fri"] },
    "recipes": {
      "type": "object",
      "properties": {
        "breakfast": { "$ref": "#/$defs/recipe" },
        "main": { "$ref": "#/$defs/recipe" },
        "dessert": { "$ref": "#/$defs/recipe" }
      },
      "required": ["breakfast", "main", "dessert"],
      "additionalProperties": false
    },
    "timeline": {
      "type": "array", "items": { "type": "string" },
      "description": "4 to 6 lines ordering the session's work."
    },
    "shopping": { "type": "array", "items": { "$ref": "#/$defs/item" } },
    "leftovers": {
      "type": "array", "items": { "type": "string" },
      "description": "Realistic amounts left over after this session."
    }
  },
  "required": ["week", "session", "recipes", "timeline", "shopping", "leftovers"],
  "additionalProperties": false,
  "$defs": {
    "recipe": {
      "type": "object",
      "properties": {
        "title": { "type": "string" },
        "blurb": { "type": "string", "description": "One short line." },
        "portions": { "type": "integer", "minimum": 1 },
        "portionNote": { "type": "string", "description": "e.g. \"9 containers: 3 a day for Wed, Thu, Fri\"." },
        "ingredients": {
          "type": "array",
          "items": {
            "type": "object",
            "properties": {
              "item": { "type": "string" },
              "amount": { "type": "string", "description": "WHOLE BATCH amount, US units with grams in brackets." },
              "kcal": { "type": "number", "minimum": 0, "description": "kcal in that whole-batch amount." },
              "protein": { "type": "number", "minimum": 0, "description": "grams of protein in that whole-batch amount." }
            },
            "required": ["item", "amount", "kcal", "protein"],
            "additionalProperties": false
          }
        },
        "steps": { "type": "array", "items": { "type": "string" } },
        "storage": { "type": "string" }
      },
      "required": ["title", "blurb", "portions", "portionNote", "ingredients", "steps", "storage"],
      "additionalProperties": false
    },
    "item": {
      "type": "object",
      "properties": {
        "item": { "type": "string", "description": "Worded the way Amazon Fresh sells it, with a real pack size." },
        "buy": { "type": "string", "description": "How many of that pack to order, e.g. \"2 bags\"." },
        "aisle": { "type": "string", "enum": ["Produce", "Meat", "Dairy & eggs", "Frozen", "Bakery", "Pantry", "Baking", "Spices"] },
        "stock": { "type": "boolean", "description": "true only for long-life items." },
        "search": { "type": "string", "description": "Short Amazon Fresh search phrase, no quantity." }
      },
      "required": ["item", "buy", "aisle", "stock", "search"],
      "additionalProperties": false
    }
  }
}
```

There is no `for` on an item (the session is the whole call), no `fav` on a recipe, and no `date`/`covers` — those come from settings.

### 5.4 Tool descriptions (exact strings for `tools/list`)

`get_brief`:

> Call this first, before planning anything. Returns the household's brief for one batch-prep session: the date and week, how many portions of breakfast, main and dessert to make, the kcal and protein target every single portion must hit, the store, likes, dislikes and pantry, the titles already saved for this session and for the week's other session (with its leftovers to use up), recent titles not to repeat, favourites, and the rules to follow. Pass `date` to plan a particular Tuesday or Friday; leave it out for the next one. If `existing` comes back non-null, tell the user that saving will replace what is already there.

`save_session`:

> Post one finished batch-prep session (a Tuesday or a Friday) to Galleon. Replaces that session's recipes, timeline, leftovers and shopping list; the week's other session and its shopping ticks are left alone. The app re-adds your per-ingredient kcal and protein and refuses the whole session if any recipe is off target or any field is too long — nothing is saved in that case. Validation errors list every problem, one per line: fix them all and call save_session again. Call get_brief first.

### 5.5 Validation

`normalize_session(args) -> tuple[dict, list[dict]]` coerces and trims but never truncates: strings are `str(...).strip()`, numbers go through `float()` (`0.0` on failure), `portions` through `int()`, booleans through `bool()`. An `aisle` not in `AISLES` is silently coerced to `"Pantry"` (not an error). Unknown keys are ignored — `additionalProperties: false` tells Claude not to send them, and rejecting them would only cause a pointless retry. If the top-level value is not an object, or `recipes`/`timeline`/`shopping`/`leftovers` are the wrong JSON type, `normalize_session` raises with the single line `save_session needs an object with week, session, recipes, timeline, shopping and leftovers.`

`validate_session(normalized, settings) -> list[str]` then collects **every** problem. Exact lines:

```
week: "2026-13-01" is not a date in YYYY-MM-DD form.
week: 2026-09-30 is a Wednesday; a week is identified by its Monday (2026-09-28).
session: must be "tue" or "fri".
recipes: breakfast is missing.
breakfast: title is empty.
main: portions must be 1 or more.
main: needs at least one ingredient.
main: needs at least one step.
main: 612 kcal per portion, target 500 ±35.
dessert: 44 g protein per portion, need at least 47.
main: title is too long (120 characters max).
main: blurb is too long (300 characters max).
main: portionNote is too long (300 characters max).
main: storage is too long (500 characters max).
main: too many ingredients (40 max).
main: too many steps (30 max).
main ingredient 12: item is too long (120 characters max).
main ingredient 12: amount is too long (120 characters max).
main ingredient 12: kcal must be a number of 0 or more.
main ingredient 12: protein must be a number of 0 or more.
main step 4: too long (500 characters max).
timeline: too many lines (12 max).
timeline line 3: too long (300 characters max).
shopping: too many items (60 max).
shopping item 7: item is too long (160 characters max).
shopping item 7: buy is too long (60 characters max).
shopping item 7: search is too long (160 characters max).
leftovers: too many lines (20 max).
leftovers line 2: too long (160 characters max).
```

Macro rule, identical to `frontend/src/lib/macros.ts: macroStatus`:

```python
kcal_pp    = sum(i["kcal"] for i in ings) / portions
protein_pp = sum(i["protein"] for i in ings) / portions
kcal_ok    = abs(round(kcal_pp) - t_kcal) <= t_kcal * 0.07
protein_ok = round(protein_pp) >= t_protein - 3
```

Both sides are rounded to whole numbers for the comparison *and* for the message, so the error text and the pills the Meals tab draws can never disagree. `±35` in the message is `round(t_kcal * 0.07)`; `47` is `t_protein - 3`.

`portions` is **not** checked against the brief's portion counts — Owen may want more or fewer than the setting says, and the brief already tells Claude the intended number.

When the list is non-empty the tool returns a **tool error** (HTTP 200, JSON-RPC result, `isError: true`) whose single text block is:

```
Nothing was saved. Fix these and call save_session again:
main: 612 kcal per portion, target 500 ±35.
dessert: 44 g protein per portion, need at least 47.
```

### 5.6 Saving

Shopping ids are assigned in order: `t01`, `t02`, … for `tue` and `f01`, `f02`, … for `fri` (two digits is enough at a 60-item cap).

`store.save_session(conn, week, session, session_data, shopping, user_id)`, called inside one `tx(conn)`:

1. `SELECT data FROM meal_plans WHERE week = ?`. If absent, insert `(week, '{"sessions": {}}', 'Claude', '', now_iso(), user_id)`.
2. `data["sessions"][session] = session_data`, where `session_data = {"date": add_days(week, 1 if session == "tue" else 4), "covers": settings[session]["covers"], "recipes": {slot: {..., "fav": False}}, "timeline": [...], "leftovers": [...]}`.
3. `UPDATE meal_plans SET data = ?, created_at = ? WHERE week = ?`. An existing row keeps its original `source`, `note` and `created_by` — only `data` and `created_at` move.
4. `DELETE FROM shopping_items WHERE week = ? AND for_session = ?` (this session only), then insert the new rows with `for_session = session`, `got = 0`, `sort_order = n`.

Replacing a session resets its three `fav` flags to `false`; the recipes are new, so the old stars do not carry.

Success result: `content: [{"type": "text", "text": summary}]` plus

```json
{
  "week": "2026-09-28",
  "session": "tue",
  "date": "2026-09-29",
  "recipes": {
    "breakfast": { "title": "Vanilla blueberry overnight oats", "portions": 3, "kcalPerPortion": 498, "proteinPerPortion": 52 },
    "main": { "title": "Beef burritos", "portions": 9, "kcalPerPortion": 505, "proteinPerPortion": 51 },
    "dessert": { "title": "Chocolate overnight oats", "portions": 3, "kcalPerPortion": 501, "proteinPerPortion": 49 }
  },
  "shoppingCount": 14,
  "url": "https://galleon.casa/#meals"
}
```

`url` is `f"{config.public_url}/#meals"` — `#meals` is the hash the Meals tab actually uses (`TABS` in `frontend/src/state/AppState.tsx`). The summary text is one paragraph:

```
Saved Tuesday 29 Sep (week of 28 Sep): Vanilla blueberry overnight oats (3 × 498 kcal / 52 g), Beef burritos (9 × 505 kcal / 51 g), Chocolate overnight oats (3 × 501 kcal / 49 g). 14 shopping items. Open https://galleon.casa/#meals
```

Dates are formatted `%A %-d %b` and `%-d %b` (use `str(d.day)` rather than `%-d`, which is not portable).

No `outputSchema` is declared for either tool. `structuredContent` is still returned — clients that ignore it read the text block — and declaring a schema would only add a validation surface a client could fail us on.

### 5.7 `store.py` changes

- `get_plan` no longer returns a top-level `leftovers`. Each session object in `plan["sessions"]` carries its own; `get_plan` does `sess.setdefault("leftovers", [])` for every session so a row written before migration 004 still reads cleanly.
- **`save_plan` is kept**, because `app/cli.py: cmd_import_seed` uses it for `seed/plans/*.json` (which has a top-level `leftovers`). It changes to write per-session leftovers: for each session `k`, `sessions[k]["leftovers"] = plan["sessions"][k].get("leftovers") or (plan.get("leftovers", []) if k == last_present_session else [])`, where `last_present_session` is `"fri"` if `"fri"` is present else `"tue"` — the same rule migration 004 applies, so importing the seed and migrating an old database give the same shape. It keeps accepting `for: "both"` on seed items.
- `set_recipe_fav` is unchanged.

## 6. Migration `migrations/004_oauth.sql`

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

SQLite's JSON functions have been built in by default since **3.38.0** (2022-02). `python:3.12-slim` ships 3.46.1 and a current macOS dev box 3.50.4; the five statements above were run against both and produce identical output, including the "already per-session" and "no sessions at all" edge cases.

`shopping_items.for_session`'s CHECK still allows `'both'` so pre-004 rows stay legal. New code never writes it: `save_session` writes only `'tue'` or `'fri'`, and `save_plan` (seed import only) is the last writer that can produce `'both'`.

## 7. OAuth and token storage

### 7.1 Secrets

Tokens and codes are `secrets.token_urlsafe(32)` (256 bits, well over the 128-bit floor) and are stored only as SHA-256 hex, using the existing helper `app.auth._token_hash`, which `app/oauth.py` imports (rename it to `token_hash` and update the four call sites in `auth.py`, so it is no longer private).

- Authorization codes: 10 minutes, single use — deleted on exchange.
- Access tokens: 3600 s.
- Refresh tokens: 30 days.
- Rotation on refresh: the presented refresh row is marked `revoked_at`, and a new access + refresh pair is issued with the **same** `family`.
- Reuse detection: presenting a refresh token whose row exists but has `revoked_at` set → mark every row in that `family` revoked and answer `invalid_grant`. A hash that is not in the table at all → `invalid_grant`, nothing to revoke.
- Sweep, run at the top of every `/oauth/token` call: `DELETE FROM oauth_codes WHERE expires_at < now`; `DELETE FROM oauth_tokens WHERE expires_at < now`; `DELETE FROM oauth_tokens WHERE revoked_at IS NOT NULL AND revoked_at < now - 7 days`. Revoked rows are kept for a week so reuse detection still works.

### 7.2 Metadata documents

`GET /.well-known/oauth-protected-resource` and `GET /.well-known/oauth-protected-resource/mcp` return the same document:

```json
{
  "resource": "https://galleon.casa/mcp",
  "authorization_servers": ["https://galleon.casa"],
  "scopes_supported": ["menus"],
  "bearer_methods_supported": ["header"],
  "resource_name": "Galleon"
}
```

`GET /.well-known/oauth-authorization-server`:

```json
{
  "issuer": "https://galleon.casa",
  "authorization_endpoint": "https://galleon.casa/oauth/authorize",
  "token_endpoint": "https://galleon.casa/oauth/token",
  "registration_endpoint": "https://galleon.casa/oauth/register",
  "response_types_supported": ["code"],
  "grant_types_supported": ["authorization_code", "refresh_token"],
  "code_challenge_methods_supported": ["S256"],
  "token_endpoint_auth_methods_supported": ["none"],
  "scopes_supported": ["menus"]
}
```

Every URL is built from `config.public_url`. Both documents are public (no cookie, no bearer).

### 7.3 `POST /oauth/register`

Request: JSON. Only `client_name` (string, trimmed, max 120 chars, default `""`) and `redirect_uris` (non-empty array of strings) are read; every other RFC 7591 field is accepted and ignored.

Allowlist — a redirect URI is accepted if it is exactly `https://claude.ai/api/mcp/auth_callback`, or its scheme is `http` and its hostname is `localhost` or `127.0.0.1` (any port, any path, no query, no fragment). Anything else → **400** `{"error": "invalid_redirect_uri", "error_description": "..."}`. A missing or empty `redirect_uris` → **400** `invalid_client_metadata`.

Before inserting: if `SELECT COUNT(*) FROM oauth_clients` exceeds 200, `DELETE FROM oauth_clients WHERE created_at < <30 days ago> AND client_id NOT IN (SELECT client_id FROM oauth_tokens)`.

`client_id` is `secrets.token_urlsafe(24)` and is **not** hashed — it is an identifier, not a credential.

Response **201**:

```json
{
  "client_id": "8xK…",
  "client_id_issued_at": 1790000000,
  "client_name": "Claude",
  "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
  "token_endpoint_auth_method": "none",
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"]
}
```

### 7.4 `GET /oauth/authorize`

Query parameters: `response_type` (must be `code`), `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method` (must be `S256`), `state`, optional `scope`, optional `resource`.

Failure handling splits in two, per RFC 6749 §4.1.2.1:

- Unknown `client_id`, or a `redirect_uri` that is not in that client's registered list → render an HTML error page (**400**), **never** redirect. Text: "Galleon can't complete this connection: the app asking isn't registered here. Remove the connector in Claude and add it again."
- Otherwise, any other invalid parameter → **302** to `redirect_uri` with `error=...` and the `state` echoed: `unsupported_response_type` when `response_type != "code"`, `invalid_request` for a missing/empty `code_challenge` or a `code_challenge_method` other than `S256`, `invalid_scope` when `scope` names anything but `menus`.

`scope` defaults to `menus` when absent. `resource` is recorded on the code row for the audit trail but not enforced — the server has exactly one protected resource.

The `hrs_session` cookie is `SameSite=Lax` (`app/api.py: _session_response`), so it travels on Claude's top-level redirect into this page and a signed-in owner lands straight on the consent state.

Page states, all rendered from one template with `OAUTH_CSP`, an inline `<style>`, `<script src="/oauth.js" defer>` and the request parameters echoed into hidden inputs:

| Cookie | Page |
|---|---|
| valid session, `role == "owner"` | **Consent.** "Claude wants to read and write Galleon menus", a line naming the client (`client_name` or "A Claude app"), the account name, and a form posting to `/oauth/authorize` with **Allow** (primary) and **Deny**. |
| no cookie / expired / unknown | **Login.** Phone field → "Text me a code" → 6-digit code field → "Sign in", calling `POST /api/login/sms/start` and `POST /api/login/sms/check` by `fetch` with `X-HRS: 1`; on success `location.reload()`. Under it, a "Owner? Sign in with a password" disclosure with username + password calling `POST /api/login`. Errors render into a `role="alert"` block from the server's `error` field. |
| valid session, `role == "staff"` | **Refused.** "Only an owner can connect Claude." plus a "Sign out and use a different account" button posting `POST /api/logout`. No form. |

All user-supplied values (`client_name`, `state`, `redirect_uri`, …) are HTML-escaped with `html.escape(..., quote=True)` before interpolation.

### 7.5 `POST /oauth/authorize`

Form body: `decision` (`allow` or `deny`) plus `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method`, `state`, `scope`, `resource` echoed from the hidden inputs.

**Form parsing (here and in §7.6):** never call `request.form()` — Starlette 1.0 asserts that `python-multipart` is installed before parsing *any* form body, urlencoded included, and it is not in `requirements.txt` (the app reads raw bodies everywhere else). Parse with the standard library instead: `urllib.parse.parse_qs((await request.body()).decode(), keep_blank_values=True)`, taking the first value of each key. A shared helper `oauth.form_body(request) -> dict[str, str]` does this and rejects a body over 64 KB with 400.

Guards, in order:

1. `Sec-Fetch-Site`, when the header is present, must be `same-origin` or `none`; anything else → **403** HTML "That request didn't come from this page." An **absent** header is allowed (old browsers send none). This is the CSRF defence in place of `X-HRS`, which cannot travel on a plain form post.
2. A valid owner session cookie is required → **403** HTML "Please sign in as an owner first." for no session, and **403** HTML "Only an owner can connect Claude." for staff.
3. `client_id` must exist and `redirect_uri` must be registered for it, else the HTML error page from §7.4 (never a redirect).

Then:

- `decision=allow` → insert an `oauth_codes` row (`code_hash`, client, user, `redirect_uri`, `code_challenge`, `scope`, `resource`, `expires_at = now + 600`) and **302** to `redirect_uri?code=<code>&state=<state>`.
- anything else (including `deny`) → **302** to `redirect_uri?error=access_denied&state=<state>`.

`state` is appended only when non-empty, and both parameters are `urllib.parse.quote`d. A `redirect_uri` that already has a query string gets `&` instead of `?`.

### 7.6 `POST /oauth/token`

Body must be `application/x-www-form-urlencoded`; a JSON body (or any other content type) → **400** `{"error": "invalid_request", "error_description": "The token endpoint takes application/x-www-form-urlencoded."}`.

Every error is **400** JSON `{"error": <code>, "error_description": <sentence>}`. An unknown `client_id` is `invalid_client` at **400**, not 401: the client authenticates with `none`, so no HTTP authentication scheme was attempted, and RFC 6749 §5.2 requires a `WWW-Authenticate` header on a 401 — which Claude would mistake for a protected-resource challenge and restart discovery.

`grant_type=authorization_code`, with `code`, `client_id`, `redirect_uri`, `code_verifier`:

1. Missing any of the four → `invalid_request`.
2. `client_id` unknown → `invalid_client`.
3. Code hash not found, `expires_at` passed, `client_id` different, or `redirect_uri` different → `invalid_grant`.
4. PKCE: `base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()` compared to the stored `code_challenge` with `hmac.compare_digest`; mismatch → delete the code row and answer `invalid_grant`.
5. Success: delete the code row, mint `family = secrets.token_urlsafe(16)`, insert one `access` row (`now + 3600`) and one `refresh` row (`now + 2592000`), return **200**:

```json
{ "access_token": "…", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "…", "scope": "menus" }
```

`grant_type=refresh_token`, with `refresh_token`, `client_id`:

1. Missing either → `invalid_request`; unknown `client_id` → `invalid_client`.
2. Row not found, wrong `kind`, wrong client, or expired → `invalid_grant`.
3. `revoked_at` already set → revoke the whole `family` and answer `invalid_grant`.
4. Otherwise: set `revoked_at` on the presented row, issue a new access + refresh pair in the same `family`, and return the same 200 body. Access tokens from the previous rotation are left to expire on their own (an hour at most).

Any other `grant_type` → `unsupported_grant_type`. Form fields beyond the ones named above (`resource`, `scope`, `client_secret`, …) are accepted and ignored — MCP clients send `resource` per RFC 8707 and the server has only one resource. All work happens inside one `tx(conn)`.

### 7.7 Bearer check on `/mcp`

`oauth.owner_for_bearer(conn, header) -> sqlite3.Row | None`:

1. `Authorization` must be `Bearer <token>` (scheme compared case-insensitively).
2. SHA-256 lookup in `oauth_tokens`; require `kind = 'access'`, `expires_at > now`, `revoked_at IS NULL`.
3. Join `users`: the row must exist, be `active = 1` and have `role = 'owner'`.
4. Update `last_used_at` only when it is NULL or older than 60 s, so a chatty client does not write on every call.

On failure, **401** with

```
WWW-Authenticate: Bearer resource_metadata="https://galleon.casa/.well-known/oauth-protected-resource", scope="menus"
```

and body `{"error": "unauthorized"}` when no `Authorization` header was sent. When a token *was* presented but is bad, the header gains `error="invalid_token"` first —

```
WWW-Authenticate: Bearer error="invalid_token", resource_metadata="https://galleon.casa/.well-known/oauth-protected-resource", scope="menus"
```

— and the body is `{"error": "invalid_token"}`. A staff-owned token is treated as `invalid_token`; there is no 403, because a connector with no owner behind it has nothing it can ever do.

### 7.8 Connections API (owner-only, under `/api`, the normal `endpoint` decorator)

`GET /api/oauth/connections` → `{"connections": [...], "mcpUrl": "https://galleon.casa/mcp"}`. One entry per `family` that still has an unrevoked, unexpired `refresh` row belonging to the caller:

```json
{ "family": "Qk9…", "clientName": "Claude", "connectedAt": "2026-09-28T17:04:11.221000Z", "lastUsedAt": "2026-09-28T18:22:02.004000Z" }
```

`connectedAt` is the oldest `created_at` in the family (the original grant), `lastUsedAt` is the newest non-null `last_used_at` across the family or `null`, `clientName` comes from `oauth_clients.client_name` (falling back to `"Claude"` when blank). Ordered newest `connectedAt` first.

`mcpUrl` rides along here rather than in `/api/state`, so the connector URL is never sent to staff and no new route is needed.

`DELETE /api/oauth/connections/{family}` → sets `revoked_at = now_iso()` on every row in that family belonging to the caller; `{"ok": true}`. A `family` not matching `^[A-Za-z0-9_-]{8,64}$`, or with no rows for this user → **404** "That connection doesn't exist."

### 7.9 Config

`app/config.py`: add `public_url: str`, read as `os.environ.get("HRS_PUBLIC_URL", "http://localhost:8000").rstrip("/")`. When `HRS_PUBLIC_URL` is unset, `lifespan` logs `log.warning("HRS_PUBLIC_URL is not set; the Claude connector will advertise %s", cfg.public_url)` — local development keeps working and a misconfigured droplet is loud in the logs. Remove `anthropic_api_key` and `anthropic_model`.

## 8. `POST /mcp`

JSON-RPC 2.0, one request per POST, one JSON response per POST. `Content-Type: application/json` on every response. The `Accept` header is not required (Claude sends `application/json, text/event-stream`; we always answer JSON). `MCP-Protocol-Version`, when present, is logged and otherwise ignored.

Order of checks: body size → bearer → JSON parse → dispatch.

- Body over **256 KB** (declared `Content-Length` or measured while streaming) → **413** with `{"jsonrpc": "2.0", "id": null, "error": {"code": -32600, "message": "Request body too large."}}`.
- No valid owner bearer token → the 401 handshake of §7.7.
- Unparseable JSON → **200** with error `-32700` "Parse error."
- A JSON array (batching) → **200** with error `-32600` "This server does not support batched requests."
- Not an object, or `jsonrpc != "2.0"` → `-32600` "Invalid request."
- No `id` (a notification) → **202** with an empty body, whatever the method. `notifications/initialized` therefore needs no special case.

Methods:

| Method | Result |
|---|---|
| `initialize` | `{"protocolVersion": <negotiated>, "capabilities": {"tools": {}}, "serverInfo": {"name": "Galleon", "title": "Galleon", "version": "1.0.0"}, "instructions": "Galleon is a household app for one home in Los Angeles. To plan a Tuesday or Friday batch-prep session, call get_brief first, work the recipes and macros out with the user in the chat, then post the session with save_session."}` |
| `ping` | `{}` |
| `tools/list` | `{"tools": [get_brief, save_session]}` — no pagination, no `nextCursor`. Each entry is `{"name", "title", "description", "inputSchema"}` from §5. |
| `tools/call` | §5. `params.name` unknown → error `-32602` `Unknown tool: <name>.`; `params.arguments` not an object → `-32602` "arguments must be an object." |
| anything else | error `-32601` `Method not found: <method>.` |

Version negotiation: `SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")`, `LATEST = SUPPORTED_PROTOCOL_VERSIONS[0]`. If the client's `params.protocolVersion` is in the tuple, echo it; otherwise answer `LATEST`. Before implementing, check <https://modelcontextprotocol.io> for a newer revision and prepend it to the tuple — the echo rule then handles both old and new clients with no other change.

`GET /mcp` and `DELETE /mcp` → **405** with `Allow: POST` and body `{"error": "method_not_allowed"}`.

Tool handlers run through `run_in_threadpool` with their own `connect()`, closed in a `finally`, exactly like `api.endpoint`. An unexpected exception inside a handler is logged with `log.exception` and returned as a tool error `Something went wrong saving that session. Try again.` — never as a stack trace.

## 9. Frontend

**Deleted:** `frontend/src/features/meals/GenPanel.tsx`, `frontend/src/features/meals/useGeneration.ts`, the `.gen-status` and `.gen-titles` rules in `frontend/src/styles/base.css`, and the `Job` / `JobStatus` / `JobStarted` types in `api/types.ts`. There are no generate/job functions in `api/client.ts` (the hook called `api()` directly), so nothing changes there. The `"regen"` example in the `UiState.confirm` doc comment in `state/AppState.tsx` is swapped for another live key.

**`MealsScreen.tsx`:** drop the `GenPanel`/`useGeneration` imports, the hook call and its "lives here, not in GenPanel" comment, and the trailing `{isOwner && loaded ? <GenPanel …/> : null}`. Empty state splits by role: owner "No menu for this week yet — plan it in Claude and it'll appear here."; staff "No menu for this week yet. Owen will add one before the visit." The existing missing-session state ("This session is missing from the menu.") is unchanged. `SLOT_LABEL` becomes `{breakfast: "Breakfast", main: "Main", dessert: "Dessert"}` — the old labels ("Breakfast · overnight oats", "Main · 3 a day") are no longer true now that Owen picks the format per session.

**`api/types.ts`:** `PlanSession` gains `leftovers: string[]`; `Plan.leftovers` is removed. `ShoppingItem.for` keeps `"both" | "tue" | "fri"` for pre-004 rows. Add:

```ts
export interface Connection {
  family: string;
  clientName: string;
  connectedAt: string;
  lastUsedAt: string | null;
}
export interface ConnectionsResponse { connections: Connection[]; mcpUrl: string }
```

**`ShoppingScreen.tsx`:** the single "Expected leftovers" card becomes up to two cards, `After Tuesday's cook` and `After Friday's cook`, each rendered only when `plan.sessions[key]?.leftovers?.length` is non-zero, reading from `plan.sessions.tue` / `plan.sessions.fri` rather than `plan.leftovers`. They are **not** filtered by the Everything / Deliver by Tue / Friday-only chips: that filter picks which grocery order you are placing, while leftovers describe what the cooking leaves behind, so hiding one would just lose information. The trailing line becomes "The next session's menu is planned to use these up first." Its empty state becomes "No menu for this week yet — plan it in Claude and the shopping list will appear here." with no link to Meals, since nothing there creates a menu any more.

**`frontend/src/features/setup/ClaudeCard.tsx`** (new, owner-only, rendered in `SetupScreen` between `PeopleCard` and `TasksCard`):

- Heading "Claude", then: "Add Galleon as a connector in Claude, then plan a prep session by chatting. In Claude: Settings → Connectors → Add custom connector. Paste this URL, then sign in as an owner when Claude asks."
- The URL in a `.mono` row with a **Copy** button using the same pattern as `ShoppingScreen.copy()` — `await navigator.clipboard.writeText(url)` then `toast("Connector URL copied")`, and on failure a read-only `<textarea>` with the text to select by hand. Local state, not `ui.copyText`.
- **Connections** list: `clientName`, "connected {fmtStamp(connectedAt)}", "last used {fmtStamp(lastUsedAt)}" or "not used yet", and a **Disconnect** button (`DELETE /api/oauth/connections/{family}`, no confirm step — reconnecting takes one click in Claude). Empty state: "Not connected yet."
- Data comes from one `GET /api/oauth/connections` on mount (and after a disconnect). It is not added to the polling loop.

## 10. Backend cleanup checklist

- `git mv app/ai.py app/menu.py`; rewrite per §5.
- `app/api.py`: drop `from . import ai`; delete `generate`, `_job_id`, `get_job`, `cancel_job` and their three `Route`s; drop the generate assertion from any test helper; add the two `/api/oauth/connections` handlers and routes.
- `app/main.py`: delete the `ai_jobs` sweep in `lifespan` (and the now-unused `now_iso` import); add the `HRS_PUBLIC_URL` warning; mount `oauth.routes` and `mcp.routes`; widen `no_store`; add `OAUTH_CSP`.
- `app/config.py`: `public_url` in, `anthropic_api_key` and `anthropic_model` out.
- `app/auth.py`: rename `_token_hash` → `token_hash`.
- `requirements.txt`: unchanged (`starlette`, `uvicorn`, `httpx`).
- `.env.example`: drop `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`; add `HRS_PUBLIC_URL=https://galleon.casa` with the comment "The address Claude reaches this app on. Used for the OAuth metadata and the connector URL; no trailing slash."
- `README.md`:
  - permission-matrix row "Generate a week's menu with Claude | ✓ |" → "Claude connector (OAuth consent, connections list) | ✓ |";
  - intro "written by Claude" → "planned with Claude in the Claude app";
  - "Run it locally": drop `export ANTHROPIC_API_KEY=…`, add `export HRS_PUBLIC_URL=http://localhost:8000`; the `.env` line lists `HRS_PUBLIC_URL, TWILIO_*, tunnel token`;
  - layout tree: replace `ai.py  menu prompt, Claude API streaming call, background job` with `menu.py  the brief, the rules, session validation`, and add `oauth.py  OAuth 2.1 server (register, consent, token)` and `mcp.py  the MCP endpoint Claude talks to`; the `tests/` line loses "generation with a mocked Claude API" and gains "OAuth dance, MCP tools";
  - new short **"Planning a menu"** section describing the connect-once flow and the per-session chat.
- `ARCHITECTURE.md`:
  - the "AI menus" row → "The Claude app talks to the server as an MCP connector over OAuth; the app validates the macros and stores the session. No API key, no per-menu cost.";
  - new row "Machine access" — OAuth 2.1 with DCR, PKCE S256, rotating refresh tokens, owner-only bearer;
  - data model: `ai_jobs` out, the three `oauth_*` tables in, and a note that leftovers live per session on `meal_plans`;
  - new decisions "Hand-rolled OAuth + MCP rather than the SDK" and "Claude does the arithmetic, the app verifies";
  - "Before going live": `ANTHROPIC_API_KEY` out, `HRS_PUBLIC_URL` in. "If it grows" is unchanged.
- `AGENTS.md`, four new lines: menus come from the Claude connector (`app/mcp.py` + `app/oauth.py`), never a server-side API call, and there is no `ANTHROPIC_*` config; `/mcp` is bearer-authenticated and owner-only while `/oauth/*` and `/.well-known/*` are public, and all of them send `Cache-Control: no-store`; the consent page's script lives in `frontend/public/oauth.js` because the CSP forbids inline scripts; a week's leftovers live inside each session, not at the top of the plan.

## 11. Tests

Python, `python3 -m unittest discover -s tests -v`.

**Deleted from `tests/test_api.py`:** `test_generate_menu`, `test_generate_bad_reply`, `test_normalize_keeps_the_amazon_fresh_search_phrase`, `test_the_prompt_asks_for_amazon_fresh_wording`, the whole `StreamParsingTest` class (currently lines 846–1023, after the stray `if __name__ == "__main__"` block, which becomes the file's real ending), and the `POST /api/plans/…/generate` assertion inside `test_staff_limits`. `ANTHROPIC_API_KEY` leaves `ENV_KEYS` and `setUp`; `HRS_PUBLIC_URL` joins both and is set to `http://testserver` (the host `starlette.testclient` uses).

**`tests/test_menu.py`**

- `resolve_prep_date` with `app.menu.today_la` patched: Mon 2026-09-28 → `2026-09-29` (`tue`, week `2026-09-28`); Wed 2026-09-30 → `2026-10-02` (`fri`, same week); Sat 2026-10-03 → `2026-10-06` (`tue`, week `2026-10-05`); Tue 2026-09-29 → `2026-09-29` (today counts). An explicit `2026-09-30` → the "is a Wednesday" error.
- `brief()` on a seeded database: portions and covers from settings, `existing` non-null for a saved session, `otherSession` carrying the other day's titles and leftovers, `recentTitles` excluding the brief's own week and capped at six earlier weeks, `favourites` picking up a `fav: true` recipe, `rules` identical to `RULES`.
- `validate_session` passing on a good session built from the seed week, with an empty problem list.
- `validate_session` failures, each asserted **by message text**: kcal 22% high → `main: 612 kcal per portion, target 500 ±35.`; protein 6 g short → `dessert: 44 g protein per portion, need at least 47.`; a Wednesday `week`; a missing `dessert`; an empty title; zero ingredients; zero steps; `portions: 0`; a 121-character title; 41 ingredients; 13 timeline lines; 61 shopping items.
- Several problems at once produce several lines, in field order, under the one "Nothing was saved." heading.
- `normalize_session` coerces a bogus `aisle` to `"Pantry"` without adding a problem, trims whitespace, and assigns `t01…`/`f01…` ids by session.
- `store.save_session` merge: save `fri`, tick one of its shopping items, then save `tue` — Friday's recipes, timeline, leftovers, shopping rows **and** its `got` tick all survive, Tuesday's rows are new and untick, and `meal_plans.data.sessions` has both keys. Saving `tue` twice replaces only Tuesday and resets its `fav` flags.

**`tests/test_oauth.py`**

- Both protected-resource paths and the AS metadata document: assert every field of §7.2, and that `resource` ends in `/mcp` and equals `HRS_PUBLIC_URL + "/mcp"`.
- `POST /oauth/register`: 201 and the exact body fields; a registered loopback URI with a port; `https://evil.example/cb` → 400 `invalid_redirect_uri`; missing `redirect_uris` → 400 `invalid_client_metadata`.
- The full dance with a real verifier: `verifier = secrets.token_urlsafe(48)`, `challenge = b64url(sha256(verifier))`; register → `GET /oauth/authorize` as a signed-in owner returns 200 HTML containing "Allow" → `POST /oauth/authorize` with `decision=allow` returns 302 whose `Location` carries `code` and the echoed `state` → `POST /oauth/token` returns 200 with `access_token`, `refresh_token`, `expires_in: 3600`, `scope: "menus"`.
- `GET /oauth/authorize` with no cookie → 200 HTML containing the phone field and `src="/oauth.js"`, and **not** the word "Allow" as a button; as staff → 200 HTML containing "Only an owner can connect Claude." `POST /oauth/authorize` as staff → 403; with no session → 403; with `Sec-Fetch-Site: cross-site` → 403.
- Unknown `client_id` on `GET /oauth/authorize` → 400 HTML, **no** `Location` header. A `redirect_uri` not registered for a known client → same. `response_type=token` with a valid client → 302 carrying `error=unsupported_response_type` and the state.
- Token errors: wrong `code_verifier` → 400 `invalid_grant` and the code is then gone (a retry with the right verifier also fails); a reused code → `invalid_grant`; a different `client_id` → `invalid_grant`; a different `redirect_uri` → `invalid_grant`; a JSON body → `invalid_request`; `grant_type=password` → `unsupported_grant_type`; an unregistered `client_id` → 400 `invalid_client`.
- Refresh rotation: refresh once, get a new pair, and the old refresh token then fails with `invalid_grant`; presenting that revoked token also revokes the new one, so the whole family is dead (a third call with the *new* refresh token is `invalid_grant` too).
- An expired access token (insert with `expires_at` in the past) → the 401 handshake.
- `GET /api/oauth/connections` as owner lists one connection with `mcpUrl`; as staff → 403. `DELETE /api/oauth/connections/{family}` → 200 and the access token immediately stops working; an unknown family → 404.

**`tests/test_mcp.py`**

- `POST /mcp` with no `Authorization` → 401, body `{"error": "unauthorized"}`, and `WWW-Authenticate` **exactly** `Bearer resource_metadata="http://testserver/.well-known/oauth-protected-resource", scope="menus"`. With a garbage token → the same plus `error="invalid_token"` first and body `{"error": "invalid_token"}`. With a **staff** user's token → `invalid_token`.
- `initialize` echoes a known `protocolVersion`, answers `LATEST` for `"1999-01-01"`, and returns the `serverInfo` and `instructions` of §8. `notifications/initialized` → 202 with an empty body. `ping` → `{}`.
- `tools/list` returns exactly two tools whose names, titles and `inputSchema` keys match §5, and whose descriptions contain "Call this first" and "call save_session again".
- `tools/call` round trip: `get_brief` with no arguments returns one text block whose JSON parses and equals `structuredContent`; `get_brief` with a Wednesday returns `isError: true`; `save_session` with a good session returns `isError` absent, the `structuredContent` of §5.6 and a summary containing "Saved"; the plan is then readable through `GET /api/plans/{week}`.
- `save_session` with an off-target main returns `isError: true`, the message lines, and **nothing saved** (`GET /api/plans/{week}` still returns the old plan).
- `GET /mcp` and `DELETE /mcp` → 405 with `Allow: POST`. A malformed body → `-32700`. A JSON array → `-32600`. `{"jsonrpc":"2.0","id":1,"method":"nope"}` → `-32601`. A 300 KB body → 413.

**`tests/test_migration.py` additions**

- `test_004_moves_leftovers_onto_the_session_that_made_them`: apply 001–003, insert three `meal_plans` rows (both sessions + week leftovers; tue only + leftovers; fri only, no leftovers), `migrate()`, then assert Friday got the list in the first row, Tuesday got it in the second, every session has a `leftovers` key, no row has a top-level `$.leftovers`, and `PRAGMA foreign_key_check` is empty.
- `test_004_drops_ai_jobs_and_adds_the_oauth_tables`: `ai_jobs` gone from `sqlite_master`, the three `oauth_*` tables and two indexes present, and a pre-004 `shopping_items` row with `for_session = 'both'` still readable.

**Frontend:** `cd frontend && npm run typecheck && npm test && npm run build`, then `grep -R "<script" static/index.html` to prove the build still emits no inline script (AGENTS.md rule). No new Vitest file is needed — the changed code is all screens, which TypeScript and the build cover; `shopping.test.ts` and `macros.test.ts` still pass unchanged.

**Manual verification:** run MCP Inspector against a local `docker compose up` with `HRS_PUBLIC_URL=http://localhost:8000` and walk the whole dance, then add the connector at claude.ai against `https://galleon.casa` and plan a real session end to end.

## 12. Deploy

On the droplet (`/opt/house-run-sheet`):

1. Edit `.env`: add `HRS_PUBLIC_URL=https://galleon.casa`, remove `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL`.
2. `git pull`
3. `docker compose build`
4. `docker compose --profile tunnel up -d`

Migration 004 runs at start-up from `lifespan`. Then, in Claude: Settings → Connectors → Add custom connector → `https://galleon.casa/mcp` → sign in as Owen → Allow. Check **Setup → Claude** shows the connection.

Optional hardening, explicitly **not** in scope: a Cloudflare rule restricting `/mcp`, `/oauth/token` and `/oauth/register` to `160.79.104.0/21`.

## 13. Decisions taken while writing this spec

Beyond the eight in §2, these open details were settled here, each the simplest thing consistent with the codebase:

- **Brief dates use `America/Los_Angeles`** via `zoneinfo`, verified present in `python:3.12-slim`, so `requirements.txt` stays at three dependencies.
- **`store.save_plan` is kept**, because `python -m app.cli import-seed` still needs it; it learns the per-session leftovers shape and uses the same fri-then-tue rule as migration 004.
- **`invalid_client` is a 400, not a 401**, because a public client sends no credentials and a 401 would need a `WWW-Authenticate` header Claude would misread as a resource challenge.
- **`mcpUrl` rides on `GET /api/oauth/connections`** rather than widening `/api/state`, so the connector URL is never sent to staff.
- **The consent page's script lives at `frontend/public/oauth.js`** (served at `/oauth.js`), the existing route for hand-written static assets; its CSS is inline, which the CSP already allows.
- **`/oauth/` gets its own CSP** with `form-action 'self' https://claude.ai http://localhost:* http://127.0.0.1:*`, so the Allow button's redirect can't be blocked.
- **Shopping leftovers ignore the order filter** — both sessions' blocks show whenever they are non-empty.
- **No `outputSchema` on either tool**, but `structuredContent` is still returned.

## 14. Non-goals

- Any server-side model call, streaming, job table or progress UI. If Claude is unreachable, there is no menu — that is the trade.
- A product catalogue, pack-size database or portion solver in the app. Claude does the arithmetic; the app only checks it (Decision 1, revisitable later).
- Multi-session or whole-week posting in one tool call, and `both` as a value for new shopping items.
- Editing recipes, timelines or the shopping list in the app. Ticking `got` and starring a favourite stay the only writes.
- Non-owner connector access, per-tool scopes, or more than the single `menus` scope.
- Token introspection (RFC 7662), revocation (RFC 7009), `client_secret`-authenticated clients, CIMD registration, or any grant beyond `authorization_code` and `refresh_token`.
- SSE / resumable streams / MCP sessions, resources, prompts, sampling, elicitation, completions or notifications from the server.
- Rate limiting on `/oauth/*` and `/mcp` beyond the 200-client registration cap; the Cloudflare IP rule in §12 is the intended answer if abuse ever shows up.
- A second household, a second authorization server, or connecting anything other than Claude.
