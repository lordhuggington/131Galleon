# House Run Sheet v2 — design spec

Date: 2026-09-24. Status: approved by the homeowner (Owen) after a brainstorming session; ready for an implementation plan.

## 1. Summary

House Run Sheet is a small self-hosted app (Starlette + SQLite backend, single-file vanilla JS frontend) that a homeowner uses to run a home with a housekeeper: a checklist for each Tuesday/Friday visit, one-off jobs, Claude-written batch meal prep, and a shopping list. See `ARCHITECTURE.md` and `README.md` for the current system.

v2 is one combined build (the owner explicitly chose to do everything in one go rather than phases) covering five things:

1. **Frontend rewrite** in React 19.2+ / TypeScript / Vite, replacing `static/app.js`, keeping the same JSON API style.
2. **Visual redesign** — "Retro Surf": a fun, cartoony, by-the-beach look (the house is at 131 Galleon St, Apt 2, Marina del Rey, CA 90292) with a new **Home screen** of big action tiles and a **bottom navigation bar** replacing today's top tabs.
3. **Flexible people model** — anyone (builders, pool service, dog walker…) can be added without code changes: two base types (owner / staff), a free-text label per person, and a per-person "can see Meals" toggle.
4. **SMS sign-in via Twilio Verify** — people sign in with their phone number and a texted code; nobody has to remember a password. Owners keep a password as an emergency fallback.
5. **Door codes and photos** — each person can see their own 4-digit Schlage lock code on the Home screen (entered manually by the owner); staff can attach "work completed" / "needs attention" photos to a visit.

Everything that works today (task due logic, polling, optimistic ticks, menu generation job, shopping list, settings, people admin, CSRF header, sessions, lockouts) keeps working with the same behavior unless this spec says otherwise.

## 2. Decisions already made (do not re-open)

| Topic | Decision |
|---|---|
| Frontend stack | React 19.2+, TypeScript (strict), Vite. No state library; context + reducer + React 19 hooks. |
| Fidelity | Port + improvements. Same features and behavior; new look, new Home screen, new nav. |
| Theme | **Retro Surf** (see §4). Light-only; the dark-mode palette is dropped. |
| Home layout | **Action tiles** (see §5.1), not the "postcard feed" variant. |
| Navigation | Bottom tab bar on all screen sizes, replacing top tabs. |
| Build/deploy | Multi-stage Dockerfile (node build stage → python stage). Built files are not committed. |
| Auth | Twilio **Verify** (not hand-rolled OTP). SMS for everyone; password fallback for owners only. |
| People model | Two base types `owner` / `staff`, free-text `label`, per-user `can_see_meals`. Not a permissions table, not a fixed role list. |
| Photos | Simple attachments per visit with kind + caption. No follow-up/resolution tracking. Client-side resize to JPEG; raw-body upload (no multipart parser dependency). |
| Door codes | Manual: owner types each person's code; app displays it. No lock integration (HomeKit is a future idea). |
| Tests | Vitest for pure frontend logic; Python `unittest` for every new/changed route including refused-permission cases (AGENTS.md rule). |
| Dependencies | Backend stays at `starlette`, `uvicorn`, `httpx` — **no new Python dependencies**. Frontend dev deps kept minimal (see §7.1). |

## 3. Roles and permissions

### 3.1 Base types

`users.role` becomes `'owner' | 'staff'` (was `'homeowner' | 'housekeeper'`). Migration maps `homeowner → owner` and `housekeeper → staff` with `label = 'Housekeeper'`.

Each user row gains:

| Column | Type | Meaning |
|---|---|---|
| `label` | TEXT NOT NULL DEFAULT '' | Shown in the UI next to the name, e.g. "Housekeeper", "Builder", "Pool service". Owners: label defaults to "Owner" in the UI when blank. Max 40 chars. |
| `phone` | TEXT NULL UNIQUE | E.164, e.g. `+13105551234`. Used for SMS sign-in. |
| `door_code` | TEXT NULL | 4–8 digits. The Schlage code the owner programmed for this person. |
| `can_see_meals` | INTEGER NOT NULL DEFAULT 1 | Staff only: whether the Meals tab and `/api/plans/*` are available. Ignored (always true) for owners. |
| `password_hash` | TEXT **NULL** (was NOT NULL) | Owners always have one. Staff normally have none. |

`username` stays as the internal identifier and the owner's password login name. When adding a person through the UI it is optional; if blank the server derives one from the display name (lower-case, `[a-z0-9.]`, suffixed `-2`, `-3`… until unique).

### 3.2 What each type can do

| | Owner | Staff |
|---|---|---|
| Home screen, own door code | ✓ | ✓ |
| Visit: tick tasks/extras, leave notes, add/delete own photos | ✓ | ✓ |
| Meals tab and `/api/plans/{week}` (recipes, prep order) | ✓ | only if `can_see_meals` |
| Add one-off jobs (extras) to a visit | ✓ | |
| Delete anyone's photo | ✓ | |
| Shopping list (`plan.shopping`, `plan.got`) | ✓ | |
| Setup: tasks, meal settings, people, door codes | ✓ | |
| Generate a menu with Claude, job status/cancel | ✓ | |
| See other people's door codes / phone numbers | ✓ | |
| Password sign-in | ✓ | only while they have no phone on file (see §6.4) |

All of this is enforced server-side. `endpoint(role="homeowner")` becomes `endpoint(role="owner")` everywhere it is used today; the 403 message becomes "Only an owner can do that."

## 4. Visual design — Retro Surf

The look is a vintage Southern-California surf poster: warm cream backgrounds, sunset gradients, teal water, orange accents, groovy display type, and simple flat cartoon illustrations (sun, waves, camper van, surfboard, gulls). Fun but grown-up.

### 4.1 Tokens (`frontend/src/styles/tokens.css`)

```css
:root {
  color-scheme: light;
  --bg: #FFF3E0;          /* page background, warm cream */
  --surface: #FDF1DC;     /* cards */
  --surface-2: #F6E4C6;   /* card headers, subtle fills */
  --line: #E7C9A1;        /* borders */
  --ink: #5A3E36;         /* body text, dark cocoa */
  --muted: #8C6B4F;       /* secondary text */
  --teal: #1C7C8C;        /* primary actions, banners, checked state */
  --teal-deep: #155E6B;
  --teal-soft: #D5EBEE;
  --orange: #E76F2C;      /* accent, active nav, progress bars */
  --orange-soft: #FBDCC8;
  --rust: #B14A2E;        /* headings inside cards, danger */
  --rust-soft: #F8DCD0;
  --sun: #FFE8B0;
  --sand: #E9C89B;
  --sky: #9AD4D6;
  --sunset: linear-gradient(180deg, #FFCE7B 0%, #FF9E64 55%, #F2708A 100%);
  --display: "Shrikhand", "Cooper Black", Georgia, serif;
  --body: "Quicksand", "Avenir Next", "Segoe UI", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  --r: 14px;              /* card radius */
  --r-sm: 10px;
}
```

Semantic mapping from today's CSS: `--accent → --teal`, `--accent-ink → #FFF3E0`, `--accent-soft → --teal-soft`, `--oat → --orange` / `--oat-soft → --orange-soft`, `--warn → --rust` / `--warn-soft → --rust-soft`.

### 4.2 Type

- Display (`h1`, screen titles, "Aloha, Maria"): **Shrikhand** 400. Use sparingly — one per screen.
- Card headings (`h2`, `h3`): **Quicksand** 700, color `--rust`.
- Body: **Quicksand** 500/600, 15px, line-height 1.5.
- Numbers, door codes, pack sizes, counts: **IBM Plex Mono** 500.
- Google Fonts link in `index.html`: `Shrikhand`, `Quicksand:wght@500;600;700`, `IBM+Plex+Mono:wght@500` (CSP already allows fonts.googleapis.com / fonts.gstatic.com).

### 4.3 Components (visual rules)

- **Cards:** `--surface` fill, 2px `--line` border, `--r` radius, 16px padding. No drop shadows except the bottom nav.
- **Primary button:** `--teal` fill, cream text, 999px radius, 44px min height. Secondary: cream fill, 2px `--line` border. Danger: `--rust` outline.
- **Checkbox tick (`.check`):** 30px rounded square; checked = `--teal` fill with cream tick. Done rows fade text to `--muted`.
- **Pills:** mono 11.5px, 999px radius; variants `default` (surface-2), `ok` (teal-soft/teal), `warn` (rust-soft/rust), `oat` (orange-soft/orange).
- **Progress bar:** `--surface-2` track, `--orange` fill.
- **Bottom nav:** fixed to viewport bottom, `--surface` fill, 2px `--line` top border, safe-area padding, max 5 items, each an emoji icon (20px) over a 10px 700-weight label; active item colored `--orange` with `aria-current="page"`. Content column gets bottom padding so nothing hides under it.
- **Illustrations:** inline SVG React components in `frontend/src/components/illustrations/` — `SunsetBand` (full-width scene: gradient sky, sun, two teal wave layers, sand, camper van left, surfboard right, two gulls), `WaveDivider` (thin two-tone wave), `SunIcon` (app icon). Start from the approved mockup SVG in Appendix A. No raster assets.
- **Emoji** are fine for nav and tile icons (🌊 Home, ✅ Visit, 🍳 Meals, 🛒 Shopping, ⚙️ Setup; 🔑 door code, 📷 photo, 📝 note).
- **Motion:** `transition: width .3s` on progress, 150ms on nav color; honor `prefers-reduced-motion`.
- **Toast:** bottom-centered above the nav, `--ink` fill, cream text.
- Keep today's accessibility: `role="checkbox"` + `aria-checked` on tick buttons, `aria-label`s, `:focus-visible` outlines (`--teal`), `role="alert"` on login errors, `role="tablist"` is replaced by a `<nav aria-label="Main">` with `aria-current`.
- PWA: `manifest.webmanifest` `theme_color` `#1C7C8C`, `background_color` `#FFF3E0`; `<meta name="theme-color" content="#1C7C8C">`; new `icon.svg` = sun over two waves in the palette.

## 5. Screens

Content column: max-width 900px, centered, 16px side padding, bottom padding for the nav. Mobile-first; everything works at 360px wide.

### 5.1 Home (new; default screen after sign-in, hash `#home`)

Top to bottom:

1. **Sunset band** (`SunsetBand`, ~110px tall on phones) with overlaid text: display-font "Aloha, {first name}" and a small line "{Weekday}, {Mon} {D} · 131 Galleon St, Apt 2 · Marina del Rey". First name = `displayName` up to the first space.
2. **Door-code banner** — teal rounded bar: 🔑 "Door code" and the code in mono with 5px letter-spacing. Tapping toggles between the code and `••••`; the choice is remembered in `localStorage` (`hrs.hideCode`). Default: shown. If the person has no `doorCode`, the banner is not rendered.
3. **Tiles** — 2-column grid of cards with a 24px emoji, a rust 700 title and a one-line muted subtitle, each a `<button>`:
   - **Staff:** "{Weekday}'s visit" (e.g. "Friday's visit"; "Today's visit" when `nextVisit(today)` is today) — subtitle "{n} tasks ready" where n = tasks due on that date per `isDue` plus that date's extras, minus those already done, plus " · {p} photos" when that date has photos; → Visit tab at `nextVisit(today)`. "This week's meals" — "Menu & prep order" (or "No menu yet" when `plans[week]` is null); → Meals (only if `canSeeMeals`). "Add a photo" — "Work done / needs fixing"; → Visit tab at `nextVisit(today)` and opens the photo picker. "Leave a note" — "Running low? Broken?"; → Visit tab at `nextVisit(today)` with the note field focused.
   - **Owner:** "{Weekday}'s visit", "This week's meals", "Shopping list" — subtitle "{n} items left to get" where n = shopping items with `got` false (or "No menu yet") → Shopping; "Add a photo".
   - With three tiles (staff without Meals) the grid is still 2 columns; the last tile spans both.
   - The tile week is `mondayOf(today)`; Home fetches `/api/plans/{week}` for it on refresh (owner always; staff when `canSeeMeals`).
4. Nothing else. No feed. Account/sign-out live on the Setup tab for owners and in a small "Account" link under the tiles for staff (opens the Account panel, §5.6).

### 5.2 Visit (`#visit`)

Same structure and behavior as today (`renderVisit` in the old `static/app.js`): date header with ‹ › stepping between Tue/Fri visits, "Today / Next visit / Past visit / Coming up" pill, "meal prep covers …" line, done count + progress bar, "Jump to next visit", "Added for this visit" group (extras; owner can add and remove), regular tasks grouped by area in `AREA_ORDER`, per-task frequency pills and "last {date}" pills, "Open this visit's recipes" link on tasks with `link === "meals"`, and the notes textarea (debounced 900ms save + save on blur).

New, between the task groups and the note card: **Photos** card.

- Header "Photos" + count. Grid of thumbnails (CSS `aspect-ratio: 4/3`, `object-fit: cover`, `loading="lazy"`), each with a pill "Work completed" (teal) or "Needs attention" (rust) and the caption below; tapping opens the full image in a lightweight overlay (a `<dialog>` with the image, caption, who/when, and Close). Uploader or owner sees a "Delete" link (inline confirm: Delete / Keep).
- "Add photo" primary button → `<input type="file" accept="image/*">` (do not set `capture`; let the OS offer camera or library). After picking: preview, a two-chip choice **Work completed** / **Needs attention** (default Work completed), optional caption (max 300), "Upload" button with progress text. The image is resized in the browser (§7.5) before upload. Multiple files can be selected; they upload sequentially.
- Empty state: "No photos for this visit yet."

### 5.3 Meals (`#meals`)

As today (`renderMeals`): week nav, Tue/Fri session chips, prep order timeline, three recipe cards with macros pills, favourite star (owner), and the owner-only "Create / Replace this week's menu" panel with the job status poller and Stop button. Only rendered/reachable when the user is an owner or `canSeeMeals`.

### 5.4 Shopping (`#shopping`, owner)

As today (`renderShopping`): week nav, Everything / Deliver by Tue / Friday-only chips, "Copy list" (with the textarea fallback when the clipboard is blocked), aisle groups, pantry basics, expected leftovers.

### 5.5 Setup (`#setup`, owner)

As today (`renderSetup`) with a reworked **People** section:

- **Add person** form: Name (required), Label (text, default "Housekeeper", `<datalist>` with Housekeeper, Builder, Pool service, Gardener, Cleaner, Family), Type (Staff / Owner, default Staff), Phone (tel input, optional, hint "for text-message sign-in"), Door code (numeric, optional, 4–8 digits), "Can see Meals" checkbox (default on, shown for Staff only), Password (shown only when Type = Owner; required then, 10+ chars). Username is not asked for; the server derives it.
- **Person row:** name, label pill, type pill (Owner teal / Staff orange), phone (mono) or "no phone yet — can't sign in by text", door code (mono, masked as `••••` with a tap-to-show), "Meals ✓/✗". Actions: **Edit** (turns the row into inline inputs for name, label, phone, door code, Meals toggle; Save / Cancel), **Reset password** (owners only; inline password input + Set / Cancel), **Remove access / Restore access**. Your own row shows "You" and has no Remove.
- Explainer text updated: "Everyone signs in with a code texted to their phone. Owners also have a password as a backup. Staff see Home and Visit; turn on Meals for people who cook. Shopping and Setup are owner-only."
- The rest of Setup (Regular tasks editor, Meal plan settings) is unchanged in behavior.
- An **Account** card at the bottom of Setup: your phone, "Change password" (owners), "Sign out".

### 5.6 Account panel (staff)

Opened from the Home screen "Account" link: shows your name, label, phone ("Ask Owen to change this"), and a Sign out button. No password section for staff.

### 5.7 Login (public)

Sunset band, "House Run Sheet" in the display font, then:

- **Step 1:** "Your phone number" (`type="tel"`, `autocomplete="tel"`, US formatting hint), button "Text me a code". Calls `POST /api/login/sms/start`.
- **Step 2:** "Enter the 6-digit code we texted to {phone}" (`inputmode="numeric"`, `autocomplete="one-time-code"`, `maxlength=6`), button "Sign in", links "Send it again" (re-calls start; disabled for 30s after each send) and "Use a different number". Calls `POST /api/login/sms/check`; on success the app loads state and goes to Home.
- Under the form: "Owner? Sign in with password" toggles a username + password form (today's `POST /api/login`).
- If `GET /api/login/options` returns `{"sms": false}`, show the password form directly with the note "Text-message sign-in isn't set up on this server yet."
- Errors render in a `role="alert"` block with the server's message. The address is **not** shown on the login screen (it is public).

## 6. Sign-in with Twilio Verify

### 6.1 Configuration (`app/config.py`, `.env.example`)

```
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_VERIFY_SERVICE_SID=      # a Verify service created in the Twilio console
HRS_PHOTOS_DIR=/data/photos     # see §8
```

`config.sms_enabled` is true only when all three Twilio values are non-empty.

### 6.2 `app/sms.py`

Two functions, both taking an optional `transport` for tests (same pattern as `app/ai.py`'s `call_model`):

- `start_verification(phone: str, transport=None) -> None` — `POST https://verify.twilio.com/v2/Services/{VERIFY_SID}/Verifications` with form fields `To={phone}`, `Channel=sms`, HTTP basic auth `(ACCOUNT_SID, AUTH_TOKEN)`, 10s timeout. 201 → ok. 429 or Twilio error code 60203 → `SmsError("Too many codes sent to that number. Wait 10 minutes.")`. 400/60200 → `SmsError("That doesn't look like a valid mobile number.")`. Anything else / network error → `SmsError("Couldn't send the text message. Try again in a minute.")` (log the detail at WARNING without the phone number's last 7 digits).
- `check_verification(phone: str, code: str, transport=None) -> bool` — `POST .../VerificationCheck` with `To`, `Code`. Returns `status == "approved"`. 404/20404 (expired or already used) → return False. Other errors → `SmsError("Couldn't check the code. Try again.")`.

### 6.3 Phone normalization (`app/auth.py: normalize_phone`)

Input from users is messy. Rules: strip everything except digits and a leading `+`. 10 digits → `+1` + digits. 11 digits starting with `1` → `+` + digits. Leading `+` with 8–15 digits → as is. Anything else → `ApiError(400, "Enter a mobile number like (310) 555-1234.")`. Store and compare only the normalized form.

### 6.4 Endpoints

| Method/path | Auth | Body → result |
|---|---|---|
| `GET /api/login/options` | public | `{"sms": bool}` |
| `POST /api/login/sms/start` | public | `{phone}` → `{"ok": true}`. Normalizes the phone. If `sms_enabled` is false → 503 `{"error": "Text-message sign-in isn't set up.", "smsUnavailable": true}`. If the send throttle blocks the phone → 429. If an **active** user with that phone exists, call `start_verification`; otherwise do nothing. **Always** return `{"ok": true}` in both cases so phone numbers can't be enumerated. A `SmsError` from the send is therefore swallowed, not surfaced: `app/sms.py` has already logged it with the number redacted, and the response stays `{"ok": true}` — a distinct answer here (even a generic 502) would confirm membership, since the send only ever runs for a number that is on file. |
| `POST /api/login/sms/check` | public | `{phone, code}` → `{"me": …}` + session cookie (identical to today's password login response). Normalize phone; validate `code` is 4–10 digits (else 400); if `throttle.blocked(phone)` → 429 (same message as today). Find the active user with that phone. If there is none, do **not** call Twilio: `throttle.fail(phone)` and 401. Otherwise call `check_verification`; False → `throttle.fail(phone)` and 401. The 401 message is always `"That code isn't right or has expired."`. On success `throttle.reset(phone)` and create the session. |
| `POST /api/login` (password) | public | Unchanged body. Now only succeeds when the user is active **and** has a `password_hash` **and** (`role == 'owner'` **or** `phone IS NULL`). The "staff with no phone yet" allowance exists so nobody is locked out by the upgrade; once an owner sets a staff member's phone, their `password_hash` is set to NULL. Failure message stays "Wrong username or password." |
| `PUT /api/me/password` | signed in | Unchanged for owners. Staff → 400 "Staff sign in by text message and don't have a password." |
| `POST /api/logout`, `GET /api/me` | | Unchanged. |

**Send throttle:** a second `LoginThrottle(limit=3, window=600)` instance keyed by phone, checked in `sms/start`, `.fail()` on every send, never reset by success (it is a rate limit, not a lockout).

**Throttle on check:** a **separate** `LoginThrottle(limit=5, window=900)` instance, `sms_check_throttle`, keyed by phone — not the `throttle` used by `/api/login`. Usernames have no charset restriction, so sharing one instance would let anyone lock a phone out of text-message sign-in by posting failed password attempts with that number as the username.

### 6.5 CLI (`app/cli.py`)

- `create-user --username U --name N --role owner|staff [--label L] [--phone P] [--door-code C] [--password PW]` — password required (prompted if absent) for owners; for staff `--password` is accepted for the transitional case but not prompted for.
- `set-password --username U` — owners, and staff with no phone on file (the break-glass when text-message sign-in is down); staff with a phone → error pointing at `set-phone --clear`.
- New `set-phone --username U --phone P` — normalizes, updates, clears `password_hash` for staff, signs the user out everywhere. `set-phone --username U --clear` is the reverse: it removes the number, signs them out everywhere, and leaves any password alone, so `set-password` works for them again. `--phone` and `--clear` are mutually exclusive and one is required.
- `--role` choices become `owner|staff`; the tests' `setUp` and README examples change accordingly.

## 7. Frontend

### 7.1 Project layout and tooling

```
frontend/
  package.json            react ^19.2, react-dom ^19.2; dev: typescript, vite, @vitejs/plugin-react,
                          @types/react, @types/react-dom, vitest
  package-lock.json       committed; Docker uses `npm ci`
  tsconfig.json           strict, "jsx": "react-jsx", "moduleResolution": "bundler", noUncheckedIndexedAccess
  vite.config.ts          plugins: react(); build.outDir "../static", build.emptyOutDir true;
                          server.proxy for "/api", "/photos", "/healthz" → http://localhost:8000
  index.html              Vite entry: meta viewport/theme-color, manifest, icon, Google Fonts link, <div id="root">
  public/                 icon.svg, manifest.webmanifest (copied verbatim into static/)
  src/
    main.tsx              createRoot(...).render(<StrictMode><App/></StrictMode>)
    App.tsx               boot (GET /api/me), login vs shell, <BottomNav/>, screens inside <Activity>
    api/client.ts         api(method, path, body?) with X-HRS header, ApiError {status, message, body},
                          401 handling (dispatch signed-out), uploadPhoto(date, blob, kind, caption)
    api/types.ts          Me, User, Task, Visit, Extra, Photo, Settings, Plan, Recipe, ShoppingItem, Job
    lib/dates.ts          pad, iso, parse, addDays, dow, diffDays, today, mondayOf, isVisitDay, nextVisit,
                          stepVisit, sessionOf, fmt, fmtLong, fmtShort, fmtDay — ported 1:1
    lib/schedule.ts       AREA_ORDER, FREQ_LABEL, FREQ_GAP, DAY_LABEL, doneDatesByTask, lastDoneBefore,
                          isDue, areaRank, sortTasks, groupByArea — ported 1:1 (pure; takes visits/tasks as args)
    lib/macros.ts         macros(recipe), macroStatus(recipe, settings) → {kcal, protein, ok}
    lib/shopping.ts       AISLES, filterItems, groupByAisle, shoppingText
    lib/phone.ts          prettyPhone("+13105551234") → "(310) 555-1234"; digitsOnly
    lib/image.ts          resizeToJpeg(file, maxEdge=1600, quality=0.85): Promise<Blob>
    state/AppState.tsx    context + useReducer: {me, booted, tasks, visits, settings, plans, planLoaded, users,
                          ui: {tab, date, week, session, shopFilter, confirm, gen, copyText}}; actions
    state/usePolling.ts   20s interval while document.visibilityState === "visible", refresh on
                          visibilitychange and after every mutation; useEffectEvent for the refresh callback
    state/useToast.tsx    toast(msg) context, 2.6s auto-hide
    components/           Button, Card, Pill, Check, Chips, Field, InlineConfirm, Toast, BottomNav,
                          Dialog, illustrations/{SunsetBand,WaveDivider,SunIcon}.tsx
    features/auth/        LoginScreen.tsx, AccountPanel.tsx
    features/home/        HomeScreen.tsx, DoorCodeBanner.tsx, Tile.tsx
    features/visit/       VisitScreen.tsx, TaskRow.tsx, ExtraRow.tsx, VisitNote.tsx, PhotosCard.tsx, PhotoUploader.tsx
    features/meals/       MealsScreen.tsx, WeekNav.tsx, RecipeCard.tsx, GenPanel.tsx
    features/shopping/    ShoppingScreen.tsx
    features/setup/       SetupScreen.tsx, PeopleCard.tsx, PersonRow.tsx, AddPersonForm.tsx, TasksCard.tsx,
                          TaskRowEditor.tsx, MealSettingsCard.tsx, AccountCard.tsx
    styles/tokens.css, styles/base.css   (base.css = today's app.css restyled to the tokens; class names may stay)
    lib/*.test.ts         Vitest (node environment, no jsdom)
```

Scripts: `npm run dev` (Vite on :5173 proxying to uvicorn on :8000), `npm run build` (`tsc -b && vite build` → `../static`), `npm test` (`vitest run`), `npm run typecheck`.

`static/` is fully gitignored (`static/`) and produced only by the build. The old files are removed from git: `git rm static/app.js static/app.css static/index.html`, and `static/icon.svg` / `static/manifest.webmanifest` are moved to `frontend/public/` (then redrawn/edited per §4.3). `app/main.py` creates `STATIC_DIR` if it is missing so the Python app and its tests start without a build (GET `/` then 404s, which is fine in development because the Vite dev server serves the page).

### 7.2 State and data flow

- One `AppState` provider. Server data is replaced wholesale on each refresh **only if its JSON signature changed** (same `lastSig` trick as today) so polling doesn't clobber in-flight optimistic UI or cause pointless re-renders.
- **Optimistic mutations** (tick task, tick extra, remove extra, favourite, shopping got, delete task, user active): reducer applies the change locally, `api()` is called, on failure a toast shows the error and a refresh resyncs — exactly today's `mutate()`. Use `useOptimistic` where it makes the row simpler (tick rows, got rows); otherwise the reducer is enough.
- **Forms** (login steps, password change, add extra, add task, add person, edit person, settings): `useActionState` with pending state disabling the submit button; success clears the form and toasts.
- **Inputs never lose drafts**: React controlled inputs replace the old `data-keep`/focus-restore hack. The visit note keeps a local draft and saves debounced (900ms) and on blur; an incoming poll must not overwrite the draft while the textarea is focused.
- **Tabs:** `ui.tab` mirrors `location.hash` (`#home` default). Each screen renders inside `<Activity mode={tab === key ? "visible" : "hidden"}>` so week/session/filter/scroll state survives switching tabs; screens the user cannot access are not mounted at all.
- **Refresh scope** as today: `/api/state` always; `/api/plans/{week}` when on Meals/Shopping (or Home for the owner's Shopping tile / staff's meals tile); `/api/users` when on Setup as owner.
- **Menu generation**: same job loop (POST generate → poll `/api/jobs/{id}` every 2s → done/cancelled/error), living in a `useGeneration` hook inside `GenPanel`.
- Any 401 from the API (other than the login endpoints) signs the user out locally, stops polling and shows the login screen.

### 7.3 Boot

`GET /api/me` → if 200: load state (and the current week's plan if needed for Home), start polling, render the shell at the hash tab (default Home). If 401: `GET /api/login/options`, render Login.

### 7.4 Security constraints (unchanged from today)

- Every non-GET request sends `X-HRS: 1`.
- CSP stays `script-src 'self'`: the Vite build must emit no inline scripts (default modern build; do not add `@vitejs/plugin-legacy`). `img-src` gains `blob:` for local previews: `img-src 'self' data: blob:`.
- React escapes text by default; **never** use `dangerouslySetInnerHTML`.
- Fonts continue to load from Google Fonts (already allowed by CSP).

### 7.5 Client-side image resize (`lib/image.ts`)

`resizeToJpeg(file)`: `createImageBitmap(file, { imageOrientation: "from-image" })` (falls back to an `<img>` + `URL.createObjectURL` if unavailable), draw onto a canvas scaled so the longest edge ≤ 1600px, `canvas.toBlob(cb, "image/jpeg", 0.85)`. HEIC from iPhones decodes in Safari, so the server only ever receives JPEG. If decoding fails (e.g. HEIC in a non-Safari browser), show "That photo format isn't supported here — take a screenshot of it or use your phone." and skip the file.

## 8. Photos

### 8.1 Storage

- Directory from `HRS_PHOTOS_DIR` (default `data/photos`; container `/data/photos`, on the existing `./data` volume). `lifespan` creates it.
- Filenames: `secrets.token_hex(16) + ".jpg"`. Never derived from user input.
- Table:

```sql
CREATE TABLE visit_photos (
    id         INTEGER PRIMARY KEY,
    visit_date TEXT    NOT NULL,
    kind       TEXT    NOT NULL CHECK (kind IN ('done', 'fix')),
    caption    TEXT    NOT NULL DEFAULT '',
    filename   TEXT    NOT NULL UNIQUE,
    bytes      INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id)
);
CREATE INDEX ix_photos_date ON visit_photos (visit_date);
```

### 8.2 Endpoints

| Method/path | Auth | Behavior |
|---|---|---|
| `POST /api/visits/{date}/photos?kind=done\|fix&caption=…` | signed in | Body is the **raw JPEG bytes** (`Content-Type: image/jpeg`); no multipart. The `endpoint` decorator gains a `raw_body=True` option that skips JSON parsing and passes `bytes` as `body`. Validate: `kind` ∈ {done, fix}; caption ≤ 300 chars (URL-decoded query param); size ≤ 8 MB → else 413 "That photo is too large."; first bytes `FF D8 FF` → else 415 "Only JPEG photos are accepted." Write file, insert row, return `{"photo": {...}}`. |
| `GET /photos/{filename}` | signed in (cookie) | Registered **before** the static mount, outside `/api/` so the `no-store` header doesn't apply. Validate filename `^[0-9a-f]{32}\.jpg$`; 404 if not in the table; `FileResponse` with `Cache-Control: private, max-age=604800, immutable`. 401 JSON when not signed in. |
| `DELETE /api/visits/{date}/photos/{photo_id}` | signed in | Owner, or the uploader, else 403 "You can only delete your own photos." Deletes the row then the file (ignore a missing file). |

`store.list_visits` adds `photos` to each visit: `[{id, kind, caption, url: "/photos/<filename>", createdAt, by: {id, displayName}}]` ordered by `created_at`. (`visits[date]` rows now come into existence for a date that only has photos, same as for completions.)

### 8.3 Backup

`deploy/backup.sh` additionally runs `rsync -a --delete data/photos/ backups/photos/` before the optional off-site rsync (which already syncs the whole `backups/` folder). README's restore note mentions copying `backups/photos/` back to `data/photos/`.

## 9. Door codes

- Owner sets a person's code via `PATCH /api/users/{id}` `{"doorCode": "4821"}` (or `""` to clear); validated `^\d{4,8}$`.
- `me_dict` (used by `/api/me`, `/api/state`, login responses) includes `doorCode` (the caller's own only), plus `label`, `phone`, `canSeeMeals`.
- `user_dict` (owner-only `/api/users`) includes `label`, `phone`, `doorCode`, `canSeeMeals`, `active`, `hasPassword`.
- Door codes are never written to logs. Staff cannot reach any endpoint that returns another person's code (`/api/users*` is owner-only; test this).

## 10. API summary of changes

New: `GET /api/login/options`, `POST /api/login/sms/start`, `POST /api/login/sms/check`, `POST /api/visits/{date}/photos`, `DELETE /api/visits/{date}/photos/{photo_id}`, `GET /photos/{filename}`.

Changed:
- `POST /api/login` — owner/no-phone rule (§6.4). `PUT /api/me/password` — staff refused.
- `GET /api/plans/{week}` — 403 "Meals aren't turned on for you." for staff with `can_see_meals = 0`. `include_shopping` still owner-only.
- `GET /api/users` / `POST /api/users` / `PATCH /api/users/{id}` — new fields `label`, `phone`, `doorCode`, `canSeeMeals`; `role` values `owner|staff`; `username` optional on create (derived); `password` required on create only for owners; `phone` must be unique (409 "That phone number is already used by {name}."); setting a staff member's phone clears their `password_hash` and signs them out everywhere; owners cannot remove their own owner access (as today); `PATCH` with `password` is refused for staff (400).
- `me` objects everywhere gain `label`, `phone`, `doorCode`, `canSeeMeals`.
- `/api/state` `visits[date].photos` added.
- All `role="homeowner"` guards → `role="owner"`.

Unchanged: tasks, visits (ticks/notes/extras), settings, plans (fav/got/generate/jobs), logout, healthz.

## 11. Database migration `migrations/002_people_and_photos.sql`

SQLite can't alter a CHECK constraint, so `users` is rebuilt with the documented 12-step procedure. To make that safe, `app/db.py: migrate()` is changed to run each migration script with foreign keys **off**: `PRAGMA foreign_keys = OFF` before `executescript`, then `PRAGMA foreign_key_check` (raise if any rows are returned), then `PRAGMA foreign_keys = ON`. (`PRAGMA foreign_keys` is a no-op inside a transaction, which is why it must be issued outside the script's `BEGIN … COMMIT`.)

```sql
CREATE TABLE users_new (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('owner', 'staff')),
    label         TEXT    NOT NULL DEFAULT '',
    phone         TEXT    UNIQUE,
    door_code     TEXT,
    can_see_meals INTEGER NOT NULL DEFAULT 1,
    password_hash TEXT,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL
);
INSERT INTO users_new (id, username, display_name, role, label, password_hash, active, created_at)
    SELECT id, username, display_name,
           CASE role WHEN 'homeowner' THEN 'owner' ELSE 'staff' END,
           CASE role WHEN 'homeowner' THEN '' ELSE 'Housekeeper' END,
           password_hash, active, created_at
    FROM users;
DROP TABLE users;
ALTER TABLE users_new RENAME TO users;
-- The other tables' "REFERENCES users (id)" clauses are stored as text, so after the drop + rename
-- they resolve to the rebuilt table. Foreign keys are off while this script runs (see above), so the
-- DROP does not cascade-delete sessions or null out done_by/created_by columns.

CREATE TABLE visit_photos ( … as in §8.1 … );
CREATE INDEX ix_photos_date ON visit_photos (visit_date);
```

`migrate()` after the change:

```python
for f in files:
    ...
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {num};\nCOMMIT;")
        bad = conn.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"migration {f.name} left {len(bad)} broken foreign key rows")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
```

A test applies `001` only, inserts a homeowner and a housekeeper, runs `migrate()`, and asserts the roles/labels mapped, the sessions row survived, and `PRAGMA foreign_key_check` is empty.

## 12. Build and deploy

**Dockerfile**

```dockerfile
FROM node:22-alpine AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build            # writes /static (outDir ../static relative to /build)

FROM python:3.12-slim
… (as today) …
COPY app ./app
COPY migrations ./migrations
COPY seed ./seed
COPY --from=frontend /static ./static
… USER hrs, EXPOSE, HEALTHCHECK, CMD as today …
```

- `.dockerignore` adds `frontend/node_modules`, `static`, `.superpowers`, `docs`.
- `docker-compose.yml` unchanged (the `./data` volume now also holds `photos/`).
- `app/main.py`: CSP `img-src 'self' data: blob:`; `/photos/{filename}` route before the static mount; `lifespan` creates the photos dir; `STATIC_DIR` created if missing.
- README "Run it locally" gains the two-terminal dev flow (`uvicorn … --port 8000` and `cd frontend && npm install && npm run dev`) and the production build (`cd frontend && npm run build`).

## 13. Testing

**Python (`tests/test_api.py`, plus `tests/test_migration.py`, `tests/test_photos.py`, `tests/test_sms.py` if splitting helps):**

- Existing tests updated for `owner|staff` and the new CLI flags; all still pass.
- SMS: start with unknown phone → 200 and Twilio not called; start with known phone → Twilio called with `To=+1…`; send throttle → 429 on the 4th start within 10 min; unconfigured → 503 with `smsUnavailable`; check with approved status → 200 + cookie + `me.doorCode`; wrong code → 401, five wrong → 429; Twilio 404 on check → 401. Twilio is faked with `httpx.MockTransport` injected via `mock.patch("app.sms.transport_for_tests")` or by patching `start_verification`/`check_verification` — the planner picks one consistent approach.
- Password login: staff with phone set → 401; staff with no phone → 200 (transitional); owner → 200; staff `PUT /api/me/password` → 400.
- People: create staff without password → 200 and `hasPassword` false; create owner without password → 400; duplicate phone → 409; `doorCode` validation (`"12"` → 400, `"4821"` → ok); setting a staff phone clears password and kills sessions; username derived and unique (`"Maria"`, `"Maria"` → `maria`, `maria-2`); staff `GET /api/users` → 403; staff `/api/me` shows only their own `doorCode`.
- Meals gating: staff with `canSeeMeals=false` → `GET /api/plans/{week}` 403; owner always 200.
- Photos: upload ok (row + file exist, `bytes` recorded); non-JPEG → 415; > 8 MB → 413; bad kind → 400; anonymous `GET /photos/x` → 401; wrong filename pattern → 404; staff deleting another person's photo → 403; owner deleting → 200 and file gone; `visits[date].photos` present in `/api/state`.
- Migration test as in §11.

**Frontend (Vitest):** `dates.test.ts` (iso/parse round-trip, addDays across month ends, mondayOf, nextVisit/stepVisit/sessionOf for each weekday), `schedule.test.ts` (every branch of `isDue`: inactive task, day pinning, `visit` freq, already-done-today, pinned weekly, gap rules for weekly/fortnightly/monthly, first-time), `macros.test.ts`, `shopping.test.ts` (filters, grouping order, `shoppingText` output), `phone.test.ts`.

**Manual smoke (documented in the plan, done by the implementer with the Vite dev server + uvicorn):** sign in by password as owner, add a staff person with a phone, verify the Home tiles, tick a task, upload a photo, switch tabs and confirm state survives, sign out.

## 14. Documentation updates

- `README.md`: roles table → Owner / Staff (with the Meals toggle), stack line (React 19 + TypeScript + Vite frontend), local dev flow, Twilio Verify setup steps (create a Verify service, copy the three values into `.env`), photos + backup note, updated CLI commands, layout tree.
- `ARCHITECTURE.md`: table rows for auth (SMS), permissions (label + toggle), live updates (unchanged), photos, front end (React/Vite build); data model additions; decisions (raw-body upload to avoid a multipart dependency, foreign keys off during migrations, why Verify not hand-rolled OTP); "If it grows" — remove items now done, add **HomeKit / Schlage integration** ("Owen wants to have a crack at this later").
- `AGENTS.md`: frontend lives in `frontend/` (React + TS + Vite; `npm test`, `npm run build`); `static/` is a build output; role names `owner|staff` and `endpoint(role="owner")`; raw-body endpoints use `raw_body=True`; photos dir; keep no inline scripts (CSP); no `dangerouslySetInnerHTML`.
- `.env.example`: Twilio vars and `HRS_PHOTOS_DIR`.

## 15. Out of scope (recorded for later)

- HomeKit / Schlage lock integration (owner's future project).
- "Needs attention" follow-up tracking / resolution.
- Multiple households, notifications, recipe library, grocery ordering (still listed in ARCHITECTURE.md's future section).
- Dark mode.
- React Compiler (can be added later via `babel-plugin-react-compiler`; not needed now).

## 16. Risks and mitigations

- **Lock-out during upgrade:** staff keep password login until a phone is set (§6.4); owners always have a password. The README's "Updating" section tells the owner to add phones right after deploying.
- **Twilio misconfiguration:** the `sms_enabled` gate + `smsUnavailable` response keep the password path usable for owners and for staff with no phone on file; staff who have a phone cannot sign in until Twilio is back, because `login()` refuses a password from them. The break-glass is on the server: `set-phone --username U --clear` then `set-password --username U` (see README "Text-message sign-in"). Errors from Twilio surface as readable messages.
- **CSP and Vite:** verify the built `index.html` has no inline `<script>`; a test in the plan should grep the build output.
- **SQLite table rebuild:** the migration test (§11) runs on every test run; `foreign_key_check` guards data integrity.
- **Photo disk usage:** ~300 KB per photo after client resize; a household won't notice. No quota in v2.

## Appendix A — approved mockup SVG (Retro Surf)

This is the sunset scene the owner approved in the brainstorm. `SunsetBand` should reproduce it (as JSX, `viewBox="0 0 320 190"`, `preserveAspectRatio="xMidYMid slice"`, `width="100%"`); on the Home screen it may be cropped to ~110px tall on phones via the container height. Gradient ids must be unique per instance (use React's `useId()`).

```svg
<svg viewBox="0 0 320 190" preserveAspectRatio="xMidYMid slice" width="100%">
  <defs>
    <linearGradient id="sunset" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#FFCE7B"/><stop offset=".55" stop-color="#FF9E64"/><stop offset="1" stop-color="#F2708A"/>
    </linearGradient>
  </defs>
  <!-- sky -->
  <rect width="320" height="190" fill="url(#sunset)"/>
  <!-- sun -->
  <circle cx="160" cy="108" r="40" fill="#FFE8B0"/>
  <!-- water: two wave layers -->
  <path d="M0 128 Q80 116 160 128 T320 128 V190 H0 Z" fill="#1C7C8C"/>
  <path d="M0 142 Q80 132 160 142 T320 142 V190 H0 Z" fill="#155E6B"/>
  <!-- sand -->
  <path d="M0 166 Q160 156 320 166 V190 H0 Z" fill="#E9C89B"/>
  <!-- surfboard, planted in the sand, right -->
  <g transform="translate(226,120) rotate(14)">
    <ellipse cx="0" cy="0" rx="9" ry="32" fill="#F4E1C6" stroke="#8C4A2F" stroke-width="2.5"/>
    <line x1="0" y1="-28" x2="0" y2="28" stroke="#E76F2C" stroke-width="3"/>
  </g>
  <!-- camper van, left -->
  <g transform="translate(30,128)">
    <rect x="0" y="6" width="58" height="26" rx="8" fill="#FDF1DC" stroke="#5A3E36" stroke-width="2.5"/>
    <path d="M0 18 h58" stroke="#E76F2C" stroke-width="6"/>
    <rect x="8" y="10" width="12" height="8" rx="2" fill="#9AD4D6"/>
    <rect x="24" y="10" width="12" height="8" rx="2" fill="#9AD4D6"/>
    <circle cx="14" cy="34" r="6" fill="#5A3E36"/><circle cx="46" cy="34" r="6" fill="#5A3E36"/>
  </g>
  <!-- gulls -->
  <path d="M96 52 q6 -6 12 0 M120 64 q6 -6 12 0" fill="none" stroke="#FFF3E0" stroke-width="2.5" stroke-linecap="round"/>
</svg>
```

Text overlaid on the band (absolutely positioned, top-left, 16px inset): the display-font greeting in `#FFF3E0` with `text-shadow: 2px 2px 0 rgba(90,62,54,.35)`, and a 700-weight 12px date/address line in `#FFF3E0`.

Approved Home mockup (staff view), for reference — tiles are `.card`s with `min-height: 92px`, a 24px emoji, a 14px 700 `--rust` title, an 11–12px subtitle:

```
┌──────────────────────────────┐
│  [sunset band]               │
│  Aloha, Maria                │
│  Tue, Sep 24                 │
├──────────────────────────────┤
│ 🔑 Door code          4821   │  ← teal bar, mono code, tap to hide
├──────────────┬───────────────┤
│ ✅           │ 🍳            │
│ Friday's     │ This week's   │
│ visit        │ meals         │
│ 8 tasks ready│ Menu & prep   │
├──────────────┼───────────────┤
│ 📷           │ 📝            │
│ Add a photo  │ Leave a note  │
│ Work done /  │ Running low?  │
│ needs fixing │ Broken?       │
├──────────────┴───────────────┤
│  🌊 Home   ✅ Visit   🍳 Meals │  ← bottom nav, active = orange
└──────────────────────────────┘
```

`SunIcon` / `icon.svg` (app icon, 64×64): `#FFF3E0` rounded square background (radius 14), sun `circle cx=32 cy=30 r=14 fill=#E76F2C`, two waves below in `#1C7C8C` and `#155E6B` using the same quadratic wave shape scaled to 64 wide.
