import {
  createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode,
} from "react";
import { ApiError, api, setUnauthorizedHandler, type Method } from "../api/client";
import type {
  Me, Plan, PlanResponse, SessionKey, Settings, StateResponse, Task, User, UsersResponse, Visit,
} from "../api/types";
import { mondayOf, nextVisit, sessionOf, today } from "../lib/dates";
import type { ShopFilter } from "../lib/shopping";
import { useToast } from "./useToast";

export type Tab = "home" | "visit" | "meals" | "shopping" | "setup";
export const TABS: Tab[] = ["home", "visit", "meals", "shopping", "setup"];

/** A Home tile can ask the Visit screen to open the photo picker or focus the note. */
export type Intent = "photo" | "note" | null;

export interface UiState {
  tab: Tab;
  /** Visit date on screen (a Tuesday or Friday). */
  date: string;
  /** Monday of the week on screen. */
  week: string;
  session: SessionKey;
  shopFilter: ShopFilter;
  /** Inline confirmation key, e.g. "del:t07", "pw:3", "photo:12", "regen". */
  confirm: string | null;
  /** Shopping list text shown when the clipboard is blocked. */
  copyText: string | null;
  intent: Intent;
}

export interface AppData {
  me: Me | null;
  booted: boolean;
  loadedState: boolean;
  tasks: Record<string, Task>;
  visits: Record<string, Visit>;
  settings: Settings | null;
  plans: Record<string, Plan | null>;
  planLoaded: Record<string, boolean>;
  users: User[] | null;
  ui: UiState;
}

/** Mirrors store.DEFAULT_SETTINGS so screens can render before /api/state lands. */
export const DEFAULT_SETTINGS: Settings = {
  kcal: 500,
  protein: 50,
  tue: { breakfast: 3, main: 9, dessert: 3, covers: "Wed, Thu, Fri" },
  fri: { breakfast: 4, main: 12, dessert: 4, covers: "Sat, Sun, Mon, Tue" },
  store: "",
  likes: "",
  dislikes: "",
  pantry: "",
};

export function tabFromHash(): Tab | null {
  const hash = (window.location.hash || "").slice(1);
  return (TABS as string[]).includes(hash) ? (hash as Tab) : null;
}

export function initialAppData(): AppData {
  const date = nextVisit(today());
  return {
    me: null,
    booted: false,
    loadedState: false,
    tasks: {},
    visits: {},
    settings: null,
    plans: {},
    planLoaded: {},
    users: null,
    ui: {
      tab: tabFromHash() ?? "home",
      date,
      week: mondayOf(date),
      session: sessionOf(date),
      shopFilter: "all",
      confirm: null,
      copyText: null,
      intent: null,
    },
  };
}

export type Action =
  | { type: "booted"; me: Me | null }
  | { type: "signed-in"; me: Me }
  | { type: "signed-out" }
  | { type: "server-state"; payload: StateResponse }
  | { type: "plan"; week: string; plan: Plan | null }
  | { type: "users"; users: User[] }
  | { type: "tab"; tab: Tab; intent?: Intent }
  | { type: "intent"; intent: Intent }
  | { type: "date"; date: string }
  | { type: "week"; week: string }
  | { type: "session"; session: SessionKey }
  | { type: "shop-filter"; filter: ShopFilter }
  | { type: "confirm"; key: string | null }
  | { type: "copy-text"; text: string | null }
  | { type: "toggle-task"; date: string; taskId: string; done: boolean }
  | { type: "toggle-extra"; date: string; extraId: string; done: boolean }
  | { type: "remove-extra"; date: string; extraId: string }
  | { type: "visit-note"; date: string; note: string }
  | { type: "remove-photo"; date: string; photoId: number }
  | { type: "got"; week: string; itemId: string; got: boolean }
  | { type: "patch-task"; taskId: string; fields: Partial<Task> }
  | { type: "delete-task"; taskId: string }
  | { type: "user-active"; userId: number; active: boolean };

function emptyVisit(date: string): Visit {
  return { date, note: "", done: {}, extras: {}, photos: [] };
}

function withVisit(state: AppData, date: string, change: (visit: Visit) => Visit): AppData {
  const current = state.visits[date] ?? emptyVisit(date);
  return { ...state, visits: { ...state.visits, [date]: change(current) } };
}

export function reduce(state: AppData, action: Action): AppData {
  switch (action.type) {
    case "booted":
      return { ...state, booted: true, me: action.me };
    case "signed-in":
      return { ...state, me: action.me, users: null };
    case "signed-out":
      return { ...initialAppData(), booted: true };
    case "server-state": {
      const tasks: Record<string, Task> = {};
      for (const t of action.payload.tasks) tasks[t.id] = t;
      return {
        ...state,
        me: action.payload.me,
        tasks,
        visits: action.payload.visits,
        settings: action.payload.settings,
        loadedState: true,
      };
    }
    case "plan":
      return {
        ...state,
        plans: { ...state.plans, [action.week]: action.plan },
        planLoaded: { ...state.planLoaded, [action.week]: true },
      };
    case "users":
      return { ...state, users: action.users };
    case "tab":
      return {
        ...state,
        ui: { ...state.ui, tab: action.tab, confirm: null, copyText: null, intent: action.intent ?? null },
      };
    case "intent":
      return { ...state, ui: { ...state.ui, intent: action.intent } };
    case "date":
      return {
        ...state,
        ui: {
          ...state.ui,
          date: action.date,
          week: mondayOf(action.date),
          session: sessionOf(action.date),
          confirm: null,
        },
      };
    case "week":
      return { ...state, ui: { ...state.ui, week: action.week, confirm: null, copyText: null } };
    case "session":
      return { ...state, ui: { ...state.ui, session: action.session } };
    case "shop-filter":
      return { ...state, ui: { ...state.ui, shopFilter: action.filter, copyText: null } };
    case "confirm":
      return { ...state, ui: { ...state.ui, confirm: action.key } };
    case "copy-text":
      return { ...state, ui: { ...state.ui, copyText: action.text } };
    case "toggle-task":
      return withVisit(state, action.date, (visit) => {
        const done = { ...visit.done };
        if (action.done) done[action.taskId] = new Date().toISOString();
        else delete done[action.taskId];
        return { ...visit, done };
      });
    case "toggle-extra":
      return withVisit(state, action.date, (visit) => {
        const existing = visit.extras[action.extraId];
        if (!existing) return visit;
        return { ...visit, extras: { ...visit.extras, [action.extraId]: { ...existing, done: action.done } } };
      });
    case "remove-extra":
      return withVisit(state, action.date, (visit) => {
        const extras = { ...visit.extras };
        delete extras[action.extraId];
        return { ...visit, extras };
      });
    case "visit-note":
      return withVisit(state, action.date, (visit) => ({ ...visit, note: action.note }));
    case "remove-photo":
      return withVisit(state, action.date, (visit) => ({
        ...visit,
        photos: visit.photos.filter((p) => p.id !== action.photoId),
      }));
    case "got": {
      const plan = state.plans[action.week];
      if (!plan) return state;
      const got = { ...(plan.got ?? {}) };
      if (action.got) got[action.itemId] = true;
      else delete got[action.itemId];
      return { ...state, plans: { ...state.plans, [action.week]: { ...plan, got } } };
    }
    case "patch-task": {
      const existing = state.tasks[action.taskId];
      if (!existing) return state;
      return { ...state, tasks: { ...state.tasks, [action.taskId]: { ...existing, ...action.fields } } };
    }
    case "delete-task": {
      const tasks = { ...state.tasks };
      delete tasks[action.taskId];
      return { ...state, tasks, ui: { ...state.ui, confirm: null } };
    }
    case "user-active": {
      if (!state.users) return state;
      return {
        ...state,
        users: state.users.map((u) => (u.id === action.userId ? { ...u, active: action.active } : u)),
        ui: { ...state.ui, confirm: null },
      };
    }
  }
}

export interface AppContextValue {
  state: AppData;
  dispatch: (action: Action) => void;
  refresh: () => Promise<void>;
  /**
   * Optimistic-write companion: toast on success/failure, always resync, rethrow.
   * Pass `{ quiet: true }` when the caller renders its own inline error, so the
   * same message isn't also shown as a toast.
   */
  mutate: (
    method: Method,
    path: string,
    body?: unknown,
    okMsg?: string,
    opts?: { quiet?: boolean },
  ) => Promise<void>;
  loadPlan: (week: string) => Promise<void>;
  showWeek: (week: string) => Promise<void>;
  goToTab: (tab: Tab, intent?: Intent) => void;
  signIn: (me: Me) => Promise<void>;
  signOut: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp must be used inside <AppProvider>");
  return ctx;
}

export function useIsOwner(): boolean {
  return useApp().state.me?.role === "owner";
}

export function useCanSeeMeals(): boolean {
  const { me } = useApp().state;
  return me !== null && (me.role === "owner" || me.canSeeMeals);
}

export function useSettings(): Settings {
  return useApp().state.settings ?? DEFAULT_SETTINGS;
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, rawDispatch] = useReducer(reduce, undefined, initialAppData);
  const { toast } = useToast();
  // Effects and callbacks read the latest state without re-subscribing.
  const stateRef = useRef(state);
  stateRef.current = state;
  // Server data is only re-applied when its JSON signature changed (v1's lastSig trick).
  const lastSig = useRef("");
  // Session identity for in-flight requests: a ref, not render state, because stateRef lags a
  // render and an await must never resurrect the data of a session that has since ended. The
  // dispatch wrapper below is the one place transitions are recorded, so no caller can forget
  // to bump it -- including screens that dispatch "booted" through the context directly.
  const session = useRef({ epoch: 0, signedIn: false });

  /** Context dispatch: records session transitions, then forwards to the reducer. */
  const dispatch = useCallback((action: Action) => {
    switch (action.type) {
      case "booted":
        session.current = { epoch: session.current.epoch + 1, signedIn: action.me !== null };
        break;
      case "signed-in":
        session.current = { epoch: session.current.epoch + 1, signedIn: true };
        break;
      case "signed-out":
        session.current = { epoch: session.current.epoch + 1, signedIn: false };
        break;
    }
    rawDispatch(action);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      lastSig.current = "";
      dispatch({ type: "signed-out" });
    });
  }, [dispatch]);

  const loadPlan = useCallback(
    async (week: string) => {
      const started = session.current.epoch;
      try {
        const { plan } = await api<PlanResponse>("GET", `/api/plans/${week}`);
        if (session.current.epoch !== started) return;
        dispatch({ type: "plan", week, plan });
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 401)) console.warn(e);
      }
    },
    [dispatch],
  );

  const refresh = useCallback(async () => {
    if (!session.current.signedIn) return;
    // Any sign-in/sign-out while a request is in flight makes this pass stale: bail after
    // every await so a late response can't dispatch the previous session's data.
    const started = session.current.epoch;
    const snapshot = stateRef.current;
    try {
      const data = await api<StateResponse>("GET", "/api/state");
      if (session.current.epoch !== started) return;
      const isOwner = data.me.role === "owner";
      const mealsAllowed = isOwner || data.me.canSeeMeals;
      const { tab } = snapshot.ui;
      // Home shows this week's meals/shopping tiles; Meals and Shopping show the week on screen.
      const planWeek = tab === "home" ? mondayOf(today()) : snapshot.ui.week;
      const wantPlan = (tab === "home" || tab === "meals" || tab === "shopping") && mealsAllowed;
      let plan: Plan | null = null;
      if (wantPlan) {
        plan = (await api<PlanResponse>("GET", `/api/plans/${planWeek}`)).plan;
        if (session.current.epoch !== started) return;
      }
      let users: User[] | null = null;
      if (tab === "setup" && isOwner) {
        users = (await api<UsersResponse>("GET", "/api/users")).users;
        if (session.current.epoch !== started) return;
      }
      const sig = JSON.stringify([data.me, data.tasks, data.visits, data.settings, planWeek, plan, users]);
      if (sig === lastSig.current) return;
      lastSig.current = sig;
      dispatch({ type: "server-state", payload: data });
      if (wantPlan) dispatch({ type: "plan", week: planWeek, plan });
      if (users) dispatch({ type: "users", users });
    } catch (e) {
      // A 401 already signed us out via the client's unauthorized handler.
      if (!(e instanceof ApiError && e.status === 401)) console.warn(e);
    }
  }, [dispatch]);

  const mutate = useCallback(
    async (
      method: Method,
      path: string,
      body?: unknown,
      okMsg?: string,
      opts?: { quiet?: boolean },
    ): Promise<void> => {
      try {
        await api<unknown>(method, path, body);
        if (okMsg) toast(okMsg);
      } catch (e) {
        // Force the resync below to replace local state even if the server data is unchanged.
        lastSig.current = "";
        if (e instanceof ApiError && e.status !== 401 && !opts?.quiet) toast(e.message);
        throw e;
      } finally {
        await refresh();
      }
    },
    [refresh, toast],
  );

  const showWeek = useCallback(
    async (week: string) => {
      dispatch({ type: "week", week });
      if (stateRef.current.planLoaded[week]) return;
      await loadPlan(week);
    },
    [dispatch, loadPlan],
  );

  const goToTab = useCallback(
    (tab: Tab, intent: Intent = null) => {
      dispatch({ type: "tab", tab, intent });
      window.scrollTo(0, 0);
    },
    [dispatch],
  );

  // No refresh() here: usePolling's scope effect fires on the sign-in transition
  // and does the first load, so calling it here would fetch twice.
  const signIn = useCallback(
    async (me: Me) => {
      lastSig.current = "";
      dispatch({ type: "signed-in", me });
    },
    [dispatch],
  );

  const signOut = useCallback(async () => {
    try {
      await api<unknown>("POST", "/api/logout");
    } catch {
      // The local session is going away either way.
    }
    lastSig.current = "";
    dispatch({ type: "signed-out" });
  }, [dispatch]);

  const value = useMemo<AppContextValue>(
    () => ({ state, dispatch, refresh, mutate, loadPlan, showWeek, goToTab, signIn, signOut }),
    [state, refresh, mutate, loadPlan, showWeek, goToTab, signIn, signOut],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
