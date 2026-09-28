# Claude Connector — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take in-app menu generation out of the React SPA, move a week's leftovers onto the session that produced them, add a **Setup → Claude** card that hands Owen the connector URL and lists (and revokes) the OAuth connections, and bring `README.md`, `ARCHITECTURE.md`, `AGENTS.md` and `.env.example` in line.

**Architecture:** Five small, self-contained tasks over the existing Vite app. Task 1 deletes `GenPanel.tsx` / `useGeneration.ts` and everything that referenced them; Task 2 follows the backend's per-session `leftovers` change through `api/types.ts` and `ShoppingScreen.tsx`; Task 3 adds one new component, `features/setup/ClaudeCard.tsx`, that fetches `GET /api/oauth/connections` once on mount (never in the 20 s polling loop); Task 4 is documentation only; Task 5 is the build gate. No new dependency, no new Vitest file, no Python file touched.

**Tech Stack:** React 19.2 + TypeScript (strict, `noUncheckedIndexedAccess`) + Vite 8 + Vitest 5, already installed in `frontend/`. Node v22.18.0, npm 10.9.3.

**Spec:** `docs/superpowers/specs/2026-09-28-claude-connector-menus-design.md` — this plan implements **§9 (Frontend)**, the documentation items of **§10** (`README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `.env.example`) and the frontend gate in **§11**. Read §9 and §10 before starting; §5 and §7.8 explain the shapes the new card consumes.

## Global Constraints

- **Branch:** `claude-connector` (already checked out). Commit after every task. Never commit `static/` (build output, gitignored), `frontend/node_modules`, `.env` or `data/`.
- **The backend plan for this spec has already been executed.** Treat these server shapes as given and do not change any Python file, or anything under `app/`, `migrations/`, `tests/` or `seed/`:
  - `GET /api/plans/{week}` returns each session with its own `leftovers: string[]` inside `plan.sessions.tue` / `plan.sessions.fri`, and **no** top-level `plan.leftovers`.
  - `GET /api/oauth/connections` (owner-only) → `{"connections": [{"family", "clientName", "connectedAt", "lastUsedAt": string | null}], "mcpUrl": "https://…/mcp"}`.
  - `DELETE /api/oauth/connections/{family}` (owner-only) → `{"ok": true}`.
  - `POST /api/plans/{week}/generate` and `/api/jobs/*` **no longer exist**.
  If a response looks wrong, say so in the task summary — do not patch the backend.
- **Project rules, copied from `AGENTS.md` (they apply to every task):**
  - Front end lives in `frontend/` (React 19 + TypeScript + Vite). `cd frontend && npm test` (Vitest, node env, pure logic in `src/lib` only) and `npm run typecheck`; `npm run build` writes `static/`.
  - `static/` is a build output: gitignored, never edited by hand, rebuilt by `npm run build` and by the Dockerfile's node stage.
  - CSP is `script-src 'self'`: no inline `<script>` in the built page (check with a grep after building), and never use `dangerouslySetInnerHTML`.
  - Frontend dependencies stay minimal: react, react-dom, and dev-only typescript, vite, @vitejs/plugin-react, @types/react, @types/react-dom, vitest. Adding anything else needs a reason in the PR.
  - Non-GET requests must send `X-HRS: 1` (CSRF guard), including the raw-body photo upload. **In practice: always go through `api()` in `frontend/src/api/client.ts`, which sets the header unconditionally — never call `fetch` directly.**
  - Visit days are Tuesday and Friday; weeks are keyed by their Monday (`YYYY-MM-DD`).
  - Never commit `.env` or anything in `data/`.
- **Roles are `owner` and `staff`.** Shopping and Setup are owner-only; `useIsOwner()` in `state/AppState.tsx` is how a component asks.
- **Every user-facing string in this plan is exact.** They come from spec §9 — do not reword, re-punctuate or "fix" the em dashes, the `·` separators or the `→` arrows.
- **Every task ends green and committed:** `cd frontend && npm run typecheck && npm test` must pass before the commit. Task 5 additionally runs `npm run build` and the inline-script grep.
- **Commit messages** are imperative, plain English, no `feat:`/`fix:` prefixes, and end with the trailer `Co-Authored-By: Claude Code <noreply@anthropic.com>` (see `git log --oneline -20`).
- **Docs (Task 4) quote the line to replace.** If a quoted line is already gone because the backend plan got there first, check the replacement text is present and move on — never add it twice.

---

## File structure

Nothing new outside `frontend/src/features/setup/`. Every other change is an edit to a file that already exists.

```
frontend/src/
  api/types.ts                      -Job -JobStatus -JobStarted; PlanSession.leftovers; -Plan.leftovers;
                                    +Connection +ConnectionsResponse
  state/AppState.tsx                UiState.confirm doc comment only
  styles/base.css                   -.gen-status -.gen-titles; +.mcp-url +.conn
  features/meals/GenPanel.tsx       DELETED
  features/meals/useGeneration.ts   DELETED
  features/meals/MealsScreen.tsx    rewritten: no generation, role-split empty state, plain SLOT_LABEL
  features/shopping/ShoppingScreen.tsx  two per-session leftovers cards
  features/setup/ClaudeCard.tsx     NEW: connector URL + copy, connections list + disconnect
  features/setup/SetupScreen.tsx    renders <ClaudeCard/> between PeopleCard and TasksCard
```

Repo-root files Task 4 touches: `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `.env.example`.

| Task | Deliverable | Depends on |
|---|---|---|
| 1 | Generation gone from the SPA | — |
| 2 | Per-session leftovers on Shopping | 1 (both edit `api/types.ts`) |
| 3 | `ClaudeCard` on Setup | 1 (frees the space in `api/types.ts`) |
| 4 | Docs and `.env.example` | 3 (the card is what the docs describe) |
| 5 | Frontend gate | 1, 2, 3, 4 |

---

### Task 1: Remove generation from the SPA

**Files:**
- Delete: `frontend/src/features/meals/GenPanel.tsx`, `frontend/src/features/meals/useGeneration.ts`
- Modify: `frontend/src/features/meals/MealsScreen.tsx` (rewritten in full below)
- Modify: `frontend/src/api/types.ts:143-152` (`JobStatus`, `Job`) and `frontend/src/api/types.ts:171` (`JobStarted`)
- Modify: `frontend/src/styles/base.css:137-138` (`.gen-status`, `.gen-titles`)
- Modify: `frontend/src/state/AppState.tsx:26` (the `UiState.confirm` doc comment)
- Test: no new test file — the gate is the grep below plus `npm run typecheck && npm test`

**Interfaces:**
- Consumes: nothing from earlier tasks. From the existing code: `useApp()`, `useIsOwner()`, `useSettings()` from `state/AppState`, `Card` from `components/Card`, `Chips` from `components/Chips`, `addDays`/`fmtLong` from `lib/dates`, `RecipeCard` from `./RecipeCard`, `WeekNav` from `./WeekNav`.
- Produces: `api/types.ts` with **no** `Job`, `JobStatus` or `JobStarted` export — Task 3 adds `Connection` and `ConnectionsResponse` in the space they leave, between `interface Plan` and `interface MeResponse`. `MealsScreen` keeps exporting `export function MealsScreen()` with no props, as `App.tsx` calls it.

- [ ] **Step 1: Write the failing check**

The gate for this task is that no trace of in-app generation survives anywhere under `frontend/src`. Save this command; Step 5 reruns it. Run it from `frontend/`:

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend
grep -rnE 'GenPanel|useGeneration|GenState|Job|/api/jobs|/generate|gen-status|gen-titles|"regen"' src
```

- [ ] **Step 2: Run it to verify it fails**

Run the command above.
Expected: FAIL — about 26 matching lines across `src/features/meals/GenPanel.tsx`, `src/features/meals/useGeneration.ts`, `src/features/meals/MealsScreen.tsx`, `src/state/AppState.tsx`, `src/styles/base.css` and `src/api/types.ts`.

- [ ] **Step 3: Delete the two generation files**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git rm -q frontend/src/features/meals/GenPanel.tsx frontend/src/features/meals/useGeneration.ts
```

- [ ] **Step 4: Edit the four survivors**

**4a. `frontend/src/api/types.ts`** — delete these ten lines (they sit between `interface Plan` and `interface MeResponse`):

```ts
export type JobStatus = "running" | "cancelling" | "done" | "cancelled" | "error";

export interface Job {
  id: number;
  week: string;
  status: JobStatus;
  progressChars: number;
  titles: string[];
  error: string | null;
}
```

and delete this line from the response block at the end of the file:

```ts
export interface JobStarted { jobId: number }
```

Leave one blank line between `interface Plan`'s closing `}` and `export interface MeResponse { me: Me }`. Nothing else in the file changes in this task.

`frontend/src/api/client.ts` needs **no** change: the hook called `api()` directly, so there was never a `generate` or `job` function there. Do not add one, and do not touch `LOGIN_PATHS`.

**4b. `frontend/src/styles/base.css`** — delete the last two lines of the `/* meals */` block:

```css
.gen-status { font-size: 14px }
.gen-titles { margin: 6px 0 0; padding-left: 18px; color: var(--muted); font-size: 13px }
```

The block now ends with the `.star[aria-pressed="true"]` rule, followed by the blank line and `/* shopping */`.

**4c. `frontend/src/state/AppState.tsx`** — `"regen"` was the only key `GenPanel` used and it is gone. The three live confirmation keys left in the app are `del:<taskId>` (`features/setup/TaskRowEditor.tsx`), `pw:<userId>` (`features/setup/PersonRow.tsx`) and `photo:<photoId>` (`features/visit/PhotosCard.tsx`) — all three are already in the comment, so `"regen"` is simply dropped. Replace this line:

```tsx
  /** Inline confirmation key, e.g. "del:t07", "pw:3", "photo:12", "regen". */
```

with:

```tsx
  /** Inline confirmation key, e.g. "del:t07", "pw:3", "photo:12". */
```

**4d. `frontend/src/features/meals/MealsScreen.tsx`** — replace the whole file with this. The `GenPanel`/`useGeneration` imports, the hook call, its "Lives here, not in GenPanel" comment and the trailing `{isOwner && loaded ? <GenPanel …/> : null}` are gone; `SLOT_LABEL` loses the old "· overnight oats" / "· 3 a day" suffixes because Owen now picks the format per session; the empty state splits by role. `isOwner` is still used — by the empty state and by `RecipeCard`'s `canFavourite`.

```tsx
import type { SessionKey, Slot } from "../../api/types";
import { Card } from "../../components/Card";
import { Chips } from "../../components/Chips";
import { addDays, fmtLong } from "../../lib/dates";
import { useApp, useIsOwner, useSettings } from "../../state/AppState";
import { RecipeCard } from "./RecipeCard";
import { WeekNav } from "./WeekNav";

const SLOT_LABEL: Record<Slot, string> = {
  breakfast: "Breakfast",
  main: "Main",
  dessert: "Dessert",
};
const SLOTS: Slot[] = ["breakfast", "main", "dessert"];
const SESSIONS: SessionKey[] = ["tue", "fri"];

export function MealsScreen() {
  const { state, dispatch } = useApp();
  const settings = useSettings();
  const isOwner = useIsOwner();
  const week = state.ui.week;
  const session = state.ui.session;
  const plan = state.plans[week] ?? null;
  const loaded = state.planLoaded[week] === true;
  const sessionPlan = plan?.sessions[session];

  return (
    <div className="section">
      <WeekNav />

      {!loaded ? (
        <div className="empty">Loading menu…</div>
      ) : !plan ? (
        <Card className="empty">
          {isOwner
            ? "No menu for this week yet — plan it in Claude and it'll appear here."
            : "No menu for this week yet. Owen will add one before the visit."}
        </Card>
      ) : (
        <>
          <Chips<SessionKey>
            label="Prep session"
            value={session}
            onChange={(next) => dispatch({ type: "session", session: next })}
            options={SESSIONS.map((key) => ({
              value: key,
              label: `${key === "tue" ? "Tuesday prep" : "Friday prep"} · covers ${
                plan.sessions[key]?.covers ?? settings[key].covers
              }`,
            }))}
          />
          {sessionPlan ? (
            <>
              <Card>
                <div className="label">
                  Prep order for {fmtLong(sessionPlan.date || addDays(week, session === "tue" ? 1 : 4))}
                </div>
                <ol className="timeline">
                  {sessionPlan.timeline.map((step, index) => (
                    <li key={`${index}-${step.slice(0, 16)}`}>{step}</li>
                  ))}
                </ol>
              </Card>
              {SLOTS.map((slot) => {
                const recipe = sessionPlan.recipes[slot];
                return recipe ? (
                  <RecipeCard
                    key={slot}
                    week={week}
                    session={session}
                    slot={slot}
                    label={SLOT_LABEL[slot]}
                    recipe={recipe}
                    canFavourite={isOwner}
                  />
                ) : null;
              })}
            </>
          ) : (
            <div className="empty">This session is missing from the menu.</div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run the grep to verify it passes**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend
grep -rnE 'GenPanel|useGeneration|GenState|Job|/api/jobs|/generate|gen-status|gen-titles|"regen"' src; echo "exit=$?"
```
Expected: no output and `exit=1` (grep found nothing). If anything is still listed, delete or rewrite it before moving on.

- [ ] **Step 6: Run typecheck and tests**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck && npm test
```
Expected: `tsc -b` prints nothing and exits 0; Vitest prints `Test Files  6 passed (6)` and `Tests  75 passed (75)`.

- [ ] **Step 7: Commit**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git add -A frontend/src
git commit -m "Take menu generation out of the app" \
  -m "Menus are written in the Claude app now, so GenPanel, useGeneration, the Job types and the generation CSS all go. The Meals empty state tells an owner to plan the week in Claude and a staff member that Owen will add one, and the slot labels lose the old oats/3-a-day wording now that Owen picks the format per session." \
  -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 2: Per-session leftovers on the Shopping list

**Files:**
- Modify: `frontend/src/api/types.ts` (`PlanSession` gains `leftovers`, `Plan` loses it)
- Modify: `frontend/src/features/shopping/ShoppingScreen.tsx:162-172` (the single "Expected leftovers" card becomes up to two per-session cards)
- Test: no new test file; `src/lib/shopping.test.ts` builds `ShoppingItem` values only and is unchanged

**Interfaces:**
- Consumes: `api/types.ts` as Task 1 left it (no `Job`/`JobStatus`/`JobStarted`).
- Produces:
  ```ts
  export interface PlanSession {
    date: string;
    covers: string;
    timeline: string[];
    recipes: Partial<Record<Slot, Recipe>>;
    leftovers: string[];
  }
  ```
  and `interface Plan` **without** a `leftovers` field. `ShoppingItem.for` keeps `"both" | "tue" | "fri"` (pre-004 rows still carry `"both"`), and `ShoppingItem.search` is untouched.

- [ ] **Step 1: Find every reader of the old shape**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend
grep -rn "leftovers" src
```
Expected, before the change: exactly five lines — `src/api/types.ts` (the `Plan.leftovers` field) and four in `src/features/shopping/ShoppingScreen.tsx` (lines 162, 164 and 166). **No test file and no fixture builds a `Plan`**, so nothing under `src/lib/*.test.ts` needs updating; if this grep shows a `.test.ts` file, update that fixture too before continuing.

- [ ] **Step 2: Move the field in `frontend/src/api/types.ts`**

Replace:

```ts
export interface PlanSession {
  date: string;
  covers: string;
  timeline: string[];
  recipes: Partial<Record<Slot, Recipe>>;
}
```

with:

```ts
export interface PlanSession {
  date: string;
  covers: string;
  timeline: string[];
  recipes: Partial<Record<Slot, Recipe>>;
  /** What this session's cook is expected to leave behind, for the next session to use up. */
  leftovers: string[];
}
```

and delete this line from `interface Plan` (it sits between `sessions` and the `/** Owner-only. */` comment):

```ts
  leftovers: string[];
```

- [ ] **Step 3: Run typecheck to verify it fails**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck
```
Expected: FAIL — `src/features/shopping/ShoppingScreen.tsx` errors with `Property 'leftovers' does not exist on type 'Plan'.` (twice: lines 162 and 166).

- [ ] **Step 4: Render the two per-session cards**

In `frontend/src/features/shopping/ShoppingScreen.tsx`, widen the first import line from

```tsx
import type { ShoppingItem } from "../../api/types";
```

to

```tsx
import type { SessionKey, ShoppingItem } from "../../api/types";
```

and add this constant just above `export function ShoppingScreen() {`:

```tsx
const SESSIONS: SessionKey[] = ["tue", "fri"];
const SESSION_LABEL: Record<SessionKey, string> = { tue: "After Tuesday's cook", fri: "After Friday's cook" };
```

Then replace the whole trailing leftovers block:

```tsx
      {plan.leftovers.length > 0 ? (
        <Card>
          <div className="label">Expected leftovers</div>
          <ul className="steps mt8">
            {plan.leftovers.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
          <p className="small muted mt8">Next week's menu is planned to use these up first.</p>
        </Card>
      ) : null}
```

with:

```tsx
      {/* Not filtered by the Everything / Deliver by Tue / Friday-only chips: that filter picks which
          grocery order you are placing, while leftovers describe what the cooking leaves behind. */}
      {SESSIONS.map((key) => {
        const leftovers = plan.sessions[key]?.leftovers ?? [];
        return leftovers.length > 0 ? (
          <Card key={key}>
            <div className="label">{SESSION_LABEL[key]}</div>
            <ul className="steps mt8">
              {leftovers.map((item, index) => (
                <li key={index}>{item}</li>
              ))}
            </ul>
            <p className="small muted mt8">The next session's menu is planned to use these up first.</p>
          </Card>
        ) : null;
      })}
```

`plan.sessions[key]` is `PlanSession | undefined` under `noUncheckedIndexedAccess`, so the `?.` and `?? []` are both load-bearing: a week with only a Friday session renders one card, a week with neither renders none.

- [ ] **Step 5: Run typecheck and tests to verify they pass**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck && npm test
```
Expected: `tsc -b` prints nothing and exits 0; Vitest prints `Test Files  6 passed (6)` and `Tests  75 passed (75)`.

- [ ] **Step 6: Commit**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git add frontend/src/api/types.ts frontend/src/features/shopping/ShoppingScreen.tsx
git commit -m "Show leftovers under the session that made them" \
  -m "Leftovers now hang off each prep session rather than the week, so the Shopping tab draws up to two cards, After Tuesday's cook and After Friday's cook, each only when it has something in it. They ignore the order filter: that filter picks which grocery order you are placing, and hiding a session's leftovers with it would only lose information." \
  -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 3: `ClaudeCard` on the Setup tab

**Files:**
- Create: `frontend/src/features/setup/ClaudeCard.tsx`
- Modify: `frontend/src/api/types.ts` (add `Connection` and `ConnectionsResponse`)
- Modify: `frontend/src/features/setup/SetupScreen.tsx` (render it between `PeopleCard` and `TasksCard`)
- Modify: `frontend/src/styles/base.css` (two new rules in the `/* setup */` block)
- Test: no new test file — `src/lib` holds the unit-tested logic and this is a screen; the gate is typecheck + build

**Interfaces:**
- Consumes:
  - `api/types.ts` as Task 1 left it, plus `OkResponse` (`{ ok: true }`), already exported there.
  - `api<T>(method, path, body?)` from `api/client.ts` — it sets `X-HRS: 1` on every request and routes a 401 through the app's sign-out handler.
  - `useIsOwner(): boolean` and `useToast(): { toast: (message: string) => void }`.
  - `fmtStamp(timestamp: string): string` from `lib/dates.ts` — formats a full ISO datetime as `Fri, Sep 25, 2:05 PM`.
  - `Card` (`components/Card.tsx`), `Button` (`components/Button.tsx`, `variant?: "primary" | "secondary" | "danger" | "ghost"`).
  - Existing CSS classes: `h19`, `small`, `muted`, `mt6`, `mt12`, `mono`, `spacer`, `field`, `err`, `group`, `group-h`, `label`, `t`.
- Produces: `export function ClaudeCard()` (no props) in `features/setup/ClaudeCard.tsx`; the two exported types
  ```ts
  export interface Connection {
    family: string;
    clientName: string;
    connectedAt: string;
    lastUsedAt: string | null;
  }
  export interface ConnectionsResponse { connections: Connection[]; mcpUrl: string }
  ```
  and the CSS classes `.mcp-url` and `.conn`.

- [ ] **Step 1: Add the two types to `frontend/src/api/types.ts`**

Put them in the gap Task 1 left, between the closing `}` of `interface Plan` and `export interface MeResponse { me: Me }`:

```ts
/** One OAuth grant chain (a "family") behind the Claude connector — spec §7.8. */
export interface Connection {
  family: string;
  clientName: string;
  connectedAt: string;
  lastUsedAt: string | null;
}

/** GET /api/oauth/connections. `mcpUrl` rides along here so staff never see it. */
export interface ConnectionsResponse { connections: Connection[]; mcpUrl: string }
```

- [ ] **Step 2: Write `frontend/src/features/setup/ClaudeCard.tsx`**

The copy pattern mirrors `ShoppingScreen.copy()` exactly — `await navigator.clipboard.writeText(...)`, a toast on success, and on failure a read-only `<textarea>` that is focused and selected for the user — except that the fallback text is local state, because `ui.copyText` belongs to the shopping list and is cleared whenever the week, filter or tab changes.

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Connection, ConnectionsResponse, OkResponse } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { fmtStamp } from "../../lib/dates";
import { useIsOwner } from "../../state/AppState";
import { useToast } from "../../state/useToast";

/**
 * Setup → Claude: the connector URL to paste into the Claude app, and the OAuth grants
 * that came back. Owner-only, and deliberately outside the 20 s polling loop — a
 * connection is made about once and then left alone.
 */
export function ClaudeCard() {
  const isOwner = useIsOwner();
  const { toast } = useToast();
  const [mcpUrl, setMcpUrl] = useState("");
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [error, setError] = useState("");
  // Local state, not ui.copyText: that one belongs to the shopping list and is cleared
  // on every week, filter and tab change.
  const [copyText, setCopyText] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);
  // Activity tears this screen's effects down when the tab is hidden; a late response
  // must not write into a card that is no longer on screen.
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const data = await api<ConnectionsResponse>("GET", "/api/oauth/connections");
      if (!alive.current) return;
      setMcpUrl(data.mcpUrl);
      setConnections(data.connections);
      setError("");
    } catch (e) {
      if (!alive.current) return;
      setConnections([]);
      setError(e instanceof Error ? e.message : "Couldn't load the Claude connections.");
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    if (isOwner) void load();
    return () => {
      alive.current = false;
    };
  }, [isOwner, load]);

  useEffect(() => {
    if (copyText !== null) {
      box.current?.focus();
      box.current?.select();
    }
  }, [copyText]);

  if (!isOwner) return null;

  async function copy() {
    if (!mcpUrl) return;
    try {
      await navigator.clipboard.writeText(mcpUrl);
      setCopyText(null);
      toast("Connector URL copied");
    } catch {
      // Clipboard blocked (no permission, or not a secure context): show the text to select.
      setCopyText(mcpUrl);
    }
  }

  // No confirm step: reconnecting is one click in Claude.
  async function disconnect(family: string) {
    try {
      await api<OkResponse>("DELETE", `/api/oauth/connections/${encodeURIComponent(family)}`);
      setError("");
      toast("Disconnected");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't disconnect that.");
    }
    await load();
  }

  return (
    <Card>
      <h3 className="h19">Claude</h3>
      <p className="small muted mt6">
        Add Galleon as a connector in Claude, then plan a prep session by chatting. In Claude: Settings → Connectors →
        Add custom connector. Paste this URL, then sign in as an owner when Claude asks.
      </p>

      <div className="mcp-url">
        <span className="mono">{mcpUrl || "Loading…"}</span>
        <span className="spacer" />
        <Button onClick={() => void copy()} disabled={!mcpUrl}>
          Copy
        </Button>
      </div>

      {copyText !== null ? (
        <label className="field mt12" htmlFor="mcpUrlBox">
          <span>Copying was blocked. Select all and copy this instead.</span>
          <textarea id="mcpUrlBox" ref={box} className="mono" readOnly value={copyText} />
        </label>
      ) : null}

      {error ? (
        <div className="err mt12" role="alert">
          {error}
        </div>
      ) : null}

      <div className="group mt12">
        <div className="group-h">
          <span className="label">Connections</span>
          <span className="mono small muted">{connections?.length ?? 0}</span>
        </div>
        {connections === null ? (
          <div className="conn small muted">Loading…</div>
        ) : connections.length === 0 ? (
          <div className="conn small muted">Not connected yet.</div>
        ) : (
          connections.map((connection) => (
            <div className="conn" key={connection.family}>
              <div>
                <div className="t">{connection.clientName}</div>
                <div className="small muted">
                  connected {fmtStamp(connection.connectedAt)} ·{" "}
                  {connection.lastUsedAt === null
                    ? "not used yet"
                    : `last used ${fmtStamp(connection.lastUsedAt)}`}
                </div>
              </div>
              <Button variant="danger" onClick={() => void disconnect(connection.family)}>
                Disconnect
              </Button>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
```

- [ ] **Step 3: Render it in `frontend/src/features/setup/SetupScreen.tsx`**

Replace the whole file:

```tsx
import { useApp } from "../../state/AppState";
import { AccountCard } from "./AccountCard";
import { ClaudeCard } from "./ClaudeCard";
import { MealSettingsCard } from "./MealSettingsCard";
import { PeopleCard } from "./PeopleCard";
import { TasksCard } from "./TasksCard";

export function SetupScreen() {
  const { state } = useApp();

  return (
    <div className="section">
      <PeopleCard />
      <ClaudeCard />
      <TasksCard />
      {/* Not before /api/state has answered: the card would adopt the defaults and save them back. */}
      {state.loadedState ? <MealSettingsCard /> : null}
      <AccountCard />
    </div>
  );
}
```

- [ ] **Step 4: Add the two CSS rules to `frontend/src/styles/base.css`**

Insert them in the `/* setup */` block, immediately after the `.uedit` rule and before `.grid2`. Find this line:

```css
.uedit { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; padding: 12px 16px; border-top: 2px solid var(--line) }
```

and add these four lines directly under it:

```css
/* Setup → Claude: the connector URL row and the OAuth connections under it */
.mcp-url { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 12px; padding: 10px 12px; border: 2px solid var(--line); border-radius: var(--r-sm); background: var(--surface-2) }
.mcp-url .mono { word-break: break-all; font-size: 13px }
.conn { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: center; padding: 12px 16px; border-top: 2px solid var(--line) }
.conn .t { font-weight: 600 }
.group-h + .conn { border-top: 0 }
```

Every custom property used (`--line`, `--r-sm`, `--surface-2`) is already defined in `styles/tokens.css`. No hard-coded colour. `.conn .t` is needed because the existing `.t` rules are scoped (`.task .t`, `.shop-item .t`), so a bare `.t` carries no style.

- [ ] **Step 5: Run typecheck and tests to verify they pass**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck && npm test
```
Expected: `tsc -b` prints nothing and exits 0; Vitest prints `Test Files  6 passed (6)` and `Tests  75 passed (75)`.

- [ ] **Step 6: Check the card is reachable and CSP-clean**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend
grep -n "ClaudeCard" src/features/setup/SetupScreen.tsx
grep -rn "dangerouslySetInnerHTML" src; echo "exit=$?"
```
Expected: the first grep prints two lines (the import and `<ClaudeCard />`), and the second prints nothing with `exit=1`.

- [ ] **Step 7: Commit**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git add frontend/src/api/types.ts frontend/src/features/setup/ClaudeCard.tsx frontend/src/features/setup/SetupScreen.tsx frontend/src/styles/base.css
git commit -m "Add a Claude card to Setup for the connector" \
  -m "Setup now shows the /mcp URL to paste into the Claude app, with a Copy button that falls back to a selectable box when the clipboard is blocked, and the OAuth connections underneath: who connected, when, when it was last used, and a Disconnect button. Owner-only, one fetch when the tab is shown, and out of the polling loop." \
  -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 4: Documentation and `.env.example`

**Files:**
- Modify: `README.md` (six edits plus one new section)
- Modify: `ARCHITECTURE.md` (six edits)
- Modify: `AGENTS.md` (four new lines)
- Modify: `.env.example` (two edits)

**Interfaces:**
- Consumes: the finished behaviour from Tasks 1–3 — **Setup → Claude** exists, there is no "Create menu" button, and the Meals empty state points at the Claude app. The backend plan has already renamed `app/ai.py` to `app/menu.py` and added `app/oauth.py` and `app/mcp.py`, and `HRS_PUBLIC_URL` is the new config value.
- Produces: nothing other code reads. No `ANTHROPIC_API_KEY` or `ANTHROPIC_MODEL` mention survives anywhere in the four files.

Every edit below quotes the exact line to find. If a line is already gone (the backend plan got there first), confirm the replacement text is present and skip that edit — never add it twice.

- [ ] **Step 1: Write the failing check**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
grep -nE "ANTHROPIC|ai\.py|ai_jobs|api/jobs|generation|menu job|written by Claude|Generate a week|004_whatever" README.md ARCHITECTURE.md AGENTS.md .env.example
```

- [ ] **Step 2: Run it to verify it fails**

Run the command above.
Expected: FAIL — 14 matching lines: `.env.example` 3, 4, 6; `ARCHITECTURE.md` 20, 33, 38, 39, 48; `README.md` 3, 15, 35, 67, 167, 177. `AGENTS.md` has no match today.

- [ ] **Step 3: Edit `.env.example`**

**3a.** Delete the Anthropic block entirely — these four lines plus the blank line under them:

```
# Claude API key for weekly menu generation (console.anthropic.com -> API keys)
ANTHROPIC_API_KEY=
# Model used for menus. Needs a Claude 5 model (adaptive thinking + web search); claude-opus-5-5 is the default.
ANTHROPIC_MODEL=claude-opus-5-5
```

The file now opens with the header comment, a blank line, then the `# Text-message sign-in (Twilio Verify).` block.

**3b.** Add the public URL to the app-settings block. Find:

```
# SQLite file (inside the container this is on the ./data volume)
HRS_DB_PATH=/data/house.db
```

and put these two lines immediately above it:

```
# The address Claude reaches this app on. Used for the OAuth metadata and the connector URL; no trailing slash.
HRS_PUBLIC_URL=https://galleon.casa
```

- [ ] **Step 4: Edit `README.md`**

**4a.** Intro. Replace:

```
A small self-hosted app for running a home with a housekeeper: a checklist for each Tuesday and Friday visit, one-off jobs for particular days, high-protein batch meal prep written by Claude, and a shopping list to order from.
```

with:

```
A small self-hosted app for running a home with a housekeeper: a checklist for each Tuesday and Friday visit, one-off jobs for particular days, high-protein batch meal prep planned with Claude in the Claude app, and a shopping list to order from.
```

**4b.** Permission matrix. Replace:

```
| Generate a week's menu with Claude | ✓ | |
```

with:

```
| Claude connector (OAuth consent, connections list) | ✓ | |
```

**4c.** New section. Insert it between the `Stack: Python 3.11+, …` paragraph and the `## Run it locally` heading, with a blank line either side:

```markdown
## Planning a menu

Menus are not written by the server. The app is a **remote MCP connector** for the Claude app, and each prep
session is planned by chatting.

Once, to connect: in Claude, **Settings → Connectors → Add custom connector**, paste the URL from
**Setup → Claude** (`https://your-host/mcp`), and sign in as an owner on the consent page Claude opens. The
grant then shows up under **Setup → Claude** with when it was last used and a **Disconnect** button.

After that, in any Claude chat: say which Tuesday or Friday you are cooking and roughly what you fancy. Claude
calls `get_brief` for that day's portion counts, kcal and protein targets, store, likes, dislikes, pantry, the
other session's leftovers and the titles not to repeat; works the whole-batch amounts and per-ingredient macros
out with you in the chat; then posts the finished session with `save_session`. The app re-adds the numbers and
refuses anything off target with a list of what to fix, so Claude corrects it and posts again. Open **Meals**
and it is there, with its shopping list on **Shopping**.

One session per call: saving Tuesday leaves Friday's recipes, leftovers and ticked shopping items alone. There
is no "Create menu" button in the app any more, and no Anthropic API key.
```

**4d.** "Run it locally". Replace:

```
export ANTHROPIC_API_KEY=sk-ant-...                     # only needed for "Create menu"
```

with:

```
export HRS_PUBLIC_URL=http://localhost:8000             # what the Claude connector advertises
```

**4e.** Deploy step. Replace:

```
cp .env.example .env && nano .env                        # ANTHROPIC_API_KEY, TWILIO_*, tunnel token
```

with:

```
cp .env.example .env && nano .env                        # HRS_PUBLIC_URL, TWILIO_*, tunnel token
```

**4f.** Layout tree. Replace this single line:

```
  ai.py        menu prompt, Claude API streaming call, background job
```

with these three (descriptions line up at column 16, like every neighbouring line):

```
  menu.py      the brief, the rules, session validation
  oauth.py     OAuth 2.1 server (register, consent, token)
  mcp.py       the MCP endpoint Claude talks to
```

**4g.** Layout tree, tests line. Replace:

```
tests/         unittest suite (API, roles, generation with a mocked Claude API)
```

with:

```
tests/         unittest suite (API, roles, OAuth dance, MCP tools)
```

- [ ] **Step 5: Edit `ARCHITECTURE.md`**

**5a.** The "AI menus" table row. Replace:

```
| AI menus | In-browser `sample()` on the viewer's Claude plan | Server calls the Claude Messages API with your `ANTHROPIC_API_KEY` (billed to your Anthropic account per menu). Runs as a background job; the page polls `/api/jobs/{id}` and can cancel |
```

with these two rows (the second is the new "Machine access" row):

```
| AI menus | In-browser `sample()` on the viewer's Claude plan | The Claude app talks to the server as an MCP connector over OAuth; the app validates the macros and stores the session. No API key, no per-menu cost. |
| Machine access | n/a | The app is its own OAuth 2.1 authorization server: Dynamic Client Registration, PKCE `S256`, one-hour access tokens and 30-day refresh tokens that rotate on every use with reuse detection. `POST /mcp` accepts a bearer token only for an active **owner** |
```

**5b.** Data model, meal plans. Replace:

```
- `meal_plans`: one row per week (keyed by Monday); recipes are stored as JSON. `shopping_items` is a real table so ticking items off is a single-row update.
```

with:

```
- `meal_plans`: one row per week (keyed by Monday); recipes are stored as JSON, one object per prep session, and each session carries its own `leftovers` list. `shopping_items` is a real table so ticking items off is a single-row update.
```

**5c.** Data model, the job table. Replace:

```
- `ai_jobs`: menu generation status and progress.
```

with:

```
- `oauth_clients`, `oauth_codes`, `oauth_tokens`: the Claude connector's registered clients, its 10-minute authorization codes, and hashed access and refresh tokens grouped by `family` (one grant chain per connection).
```

**5d.** Two new decisions. Find the last bullet of "Decisions worth knowing":

```
- **No inline scripts, ever.** The CSP is `script-src 'self'`, so the build must emit only external module scripts — the plan greps the built `index.html` to prove it. React escapes text by default and `dangerouslySetInnerHTML` is banned, which replaces v1's hand-rolled `esc()`.
```

and add these two bullets directly under it:

```
- **Hand-rolled OAuth + MCP rather than the SDK.** The `mcp` SDK would have been a fourth dependency and still would not have supplied login, a consent page or token issuance, which is most of the work. `app/oauth.py` and `app/mcp.py` are plain Starlette routes, and the transport is Streamable HTTP in its stateless JSON form: one JSON-RPC request in, one JSON response out, no SSE and no session ids.
- **Claude does the arithmetic, the app verifies.** A product catalogue and portion solver in the app was considered and put off. Instead `save_session` re-adds Claude's per-ingredient kcal and protein, divides by the portions and refuses the whole session — saving nothing — if any recipe misses the household targets, using exactly the rule `frontend/src/lib/macros.ts: macroStatus` draws the pills with, so the error text and the Meals tab can never disagree.
```

**5e.** "Before going live". Replace:

```
1. Set `ANTHROPIC_API_KEY`, the three `TWILIO_*` values for text-message sign-in, and keep `HRS_COOKIE_SECURE=true`.
```

with:

```
1. Set `HRS_PUBLIC_URL` to the public address with no trailing slash, the three `TWILIO_*` values for text-message sign-in, and keep `HRS_COOKIE_SECURE=true`.
```

**5f.** Two sentences §10 does not list, which this change makes untrue. There is no menu job thread any more — replace:

```
- **One uvicorn worker.** SQLite handles this load easily; the login lockout and the menu job thread live in-process. Moving to several workers would mean moving the lockout into the database.
```

with:

```
- **One uvicorn worker.** SQLite handles this load easily and the login lockout lives in-process. Moving to several workers would mean moving the lockout into the database.
```

and, because the backend plan has taken `004`, replace:

```
- **Raw SQL + numbered migrations rather than an ORM/Alembic.** Keeps the dependency count at three. Add a migration by creating `migrations/004_whatever.sql` (the next free number); it's applied once on start-up.
```

with:

```
- **Raw SQL + numbered migrations rather than an ORM/Alembic.** Keeps the dependency count at three. Add a migration by creating `migrations/005_whatever.sql` (the next free number); it's applied once on start-up.
```

- [ ] **Step 6: Edit `AGENTS.md`**

**6a.** Find:

```
- Never log a door code or a sign-in code, and never log a full phone number — redact it the way `app/sms.py: _redact` does.
```

and add these two bullets directly under it:

```
- Menus come from the Claude connector (`app/mcp.py` + `app/oauth.py`), never a server-side model call. There is no `ANTHROPIC_*` config and nothing generates a menu in this repo.
- `/mcp` is bearer-authenticated and owner-only; `/oauth/*` and `/.well-known/*` are public. All of them send `Cache-Control: no-store`.
```

**6b.** Find:

```
- CSP is `script-src 'self'`: no inline `<script>` in the built page (check with a grep after building), and never use `dangerouslySetInnerHTML`.
```

and add this bullet directly under it:

```
- The OAuth consent page's script lives in `frontend/public/oauth.js` (copied verbatim into `static/` by the build and served at `/oauth.js`), because the CSP forbids an inline script there too.
```

**6c.** Find:

```
- Visit days are Tuesday and Friday; weeks are keyed by their Monday (`YYYY-MM-DD`).
```

and add this bullet directly under it:

```
- A week's leftovers live inside each session (`plan.sessions.tue.leftovers`), not at the top of the plan.
```

- [ ] **Step 7: Run the check to verify it passes**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
grep -nE "ANTHROPIC|ai\.py|ai_jobs|api/jobs|generation|menu job|written by Claude|Generate a week|004_whatever" README.md ARCHITECTURE.md AGENTS.md .env.example; echo "exit=$?"
grep -c "HRS_PUBLIC_URL" README.md ARCHITECTURE.md .env.example
grep -n "Planning a menu" README.md
```
Expected: the first grep prints nothing with `exit=1`; the second prints `README.md:2`, `ARCHITECTURE.md:1` and `.env.example:1`; the third prints one line, `## Planning a menu`.

- [ ] **Step 8: Run typecheck and tests**

Docs do not affect the build, but the task still ends green:

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck && npm test
```
Expected: `tsc -b` exits 0; `Test Files  6 passed (6)` and `Tests  75 passed (75)`.

- [ ] **Step 9: Commit**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git add README.md ARCHITECTURE.md AGENTS.md .env.example
git commit -m "Document the Claude connector and drop the API key" \
  -m "README gains a Planning a menu section covering the one-time connect and the per-session chat, and loses ANTHROPIC_API_KEY everywhere; the layout tree lists menu.py, oauth.py and mcp.py. ARCHITECTURE records how machine access works, the three oauth_ tables in place of ai_jobs, per-session leftovers, and why OAuth and MCP are hand-rolled while Claude does the arithmetic; the menu job thread and the next free migration number are corrected while we are in there. AGENTS gains four house rules and .env.example swaps the Anthropic settings for HRS_PUBLIC_URL." \
  -m "Co-Authored-By: Claude Code <noreply@anthropic.com>"
```

---

### Task 5: Frontend gate

**Files:**
- Modify: none. This task only runs checks and records the result.

**Interfaces:**
- Consumes: everything Tasks 1–4 produced.
- Produces: a green `typecheck` + `test` + `build`, and proof that the built page still carries exactly one `<script>` tag and that it is external (spec §11, `AGENTS.md`).

- [ ] **Step 1: Run the full frontend gate**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet/frontend && npm run typecheck && npm test && npm run build
```
Expected: `tsc -b` prints nothing and exits 0; Vitest prints `Test Files  6 passed (6)` and `Tests  75 passed (75)`; Vite prints a list ending in `../static/index.html`, `../static/assets/index-*.css` and `../static/assets/index-*.js`, then `✓ built in …`.

- [ ] **Step 2: Prove the built page has no inline script**

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
grep -c "<script" static/index.html
grep -n "<script" static/index.html
```
Expected: the first command prints `1`; the second prints one line of the form

```
16:    <script type="module" crossorigin src="/assets/index-XXXXXXXX.js"></script>
```

The hash after `index-` changes every build — what matters is that there is exactly **one** `<script`, that it carries `src=`, and that it points into `/assets/`. If the count is anything but 1, or the tag has no `src=`, stop: the CSP (`script-src 'self'`) would break the page in production.

- [ ] **Step 3: Leave `static/` exactly as the build produced it**

`static/` is **gitignored** (see the repo-root `.gitignore`, which lists `static/`) and **not tracked** — `git ls-files static` prints nothing. Do not `git add` it, do not `git add -f` it, and do not hand-edit anything inside it: the Docker image rebuilds it in its node stage. Verify:

```bash
cd /Users/owenkeenan-lindsey/Desktop/Active_Applications/house-run-sheet
git ls-files static | wc -l
git status --short
```
Expected: the first command prints `0` (nothing under `static/` is tracked); `git status --short` shows no entry under `static/` (it may show nothing at all if Tasks 1–4 are committed).

- [ ] **Step 4: Report**

There is nothing to commit in this task — it is a gate, not a change. In the task summary record: the Vitest counts, the `grep -c "<script" static/index.html` result, and anything that failed.

If Tasks 1–4 left an uncommitted file behind (`git status --short` is not clean apart from ignored paths), commit it under the task it belongs to rather than inventing a new commit here.

---

## Manual smoke (optional, after Task 5)

Not a gate, but the fastest way to see the three screens for real. From the repo root, with the backend already migrated:

```bash
export HRS_DB_PATH=data/house.db HRS_COOKIE_SECURE=0 HRS_PUBLIC_URL=http://localhost:8000
python3 -m uvicorn app.main:app --reload --port 8000    # terminal 1
cd frontend && npm run dev                              # terminal 2, Vite on :5173
```

Open <http://localhost:5173>, sign in as an owner and check:

1. **Meals**, on a week with no menu → "No menu for this week yet — plan it in Claude and it'll appear here." and **no** Create/Replace menu panel anywhere on the tab.
2. **Meals**, on a week with a menu → the recipe cards are labelled plainly "Breakfast", "Main", "Dessert".
3. **Shopping**, on a week whose sessions have leftovers → "After Tuesday's cook" and "After Friday's cook" cards, each ending "The next session's menu is planned to use these up first.", and both still visible when the chips are switched to "Friday-only".
4. **Setup** → the **Claude** card sits between People and Regular tasks, shows `http://localhost:8000/mcp`, copies it to the clipboard with a "Connector URL copied" toast, and lists "Not connected yet." until a connector is added.
