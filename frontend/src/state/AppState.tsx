import {
  createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, type ReactNode,
} from "react";
import { ApiError, api, setUnauthorizedHandler, type Method } from "../api/client";
import type {
  ClaimStatus, HoursClaim, Job, JobStatus, Me, StateResponse, Task, User, UsersResponse, Visit,
} from "../api/types";
import { TABS, isOwner, seesHours, seesJobs, seesVisit, type Tab } from "../lib/access";
import { nextVisit, today } from "../lib/dates";
import { useToast } from "./useToast";

export type { Tab } from "../lib/access";
export { TABS } from "../lib/access";

/** A Home tile can ask the Visit screen to open the photo picker or focus the note. */
export type Intent = "photo" | "note" | null;

export interface UiState {
  tab: Tab;
  /** Visit date on screen (a Tuesday or Friday). */
  date: string;
  /** Inline confirmation key, e.g. "del:t07", "pw:3", "photo:12", "job:4", "claim:9". */
  confirm: string | null;
  intent: Intent;
}

export interface AppData {
  me: Me | null;
  booted: boolean;
  loadedState: boolean;
  tasks: Record<string, Task>;
  visits: Record<string, Visit>;
  /** Owners and builders; null until /api/state has sent them (or for people who can't see jobs). */
  jobs: Job[] | null;
  /** Owners (everyone's) and housekeepers (their own); null otherwise. */
  hours: HoursClaim[] | null;
  users: User[] | null;
  ui: UiState;
}

export function tabFromHash(): Tab | null {
  const hash = (window.location.hash || "").slice(1);
  return (TABS as string[]).includes(hash) ? (hash as Tab) : null;
}

export function initialAppData(): AppData {
  return {
    me: null,
    booted: false,
    loadedState: false,
    tasks: {},
    visits: {},
    jobs: null,
    hours: null,
    users: null,
    ui: {
      tab: tabFromHash() ?? "home",
      date: nextVisit(today()),
      confirm: null,
      intent: null,
    },
  };
}

export type Action =
  | { type: "booted"; me: Me | null }
  | { type: "signed-in"; me: Me }
  | { type: "signed-out" }
  | { type: "server-state"; payload: StateResponse }
  | { type: "users"; users: User[] }
  | { type: "tab"; tab: Tab; intent?: Intent }
  | { type: "intent"; intent: Intent }
  | { type: "date"; date: string }
  | { type: "confirm"; key: string | null }
  | { type: "toggle-task"; date: string; taskId: string; done: boolean }
  | { type: "toggle-extra"; date: string; extraId: string; done: boolean }
  | { type: "remove-extra"; date: string; extraId: string }
  | { type: "visit-note"; date: string; note: string }
  | { type: "remove-photo"; date: string; photoId: number }
  | { type: "patch-task"; taskId: string; fields: Partial<Task> }
  | { type: "delete-task"; taskId: string }
  | { type: "user-active"; userId: number; active: boolean }
  | { type: "job-status"; jobId: number; status: JobStatus }
  | { type: "remove-job"; jobId: number }
  | { type: "remove-job-comment"; jobId: number; commentId: number }
  | { type: "remove-job-photo"; jobId: number; photoId: number }
  | { type: "claim-status"; claimIds: number[]; status: ClaimStatus }
  | { type: "remove-claim"; claimId: number };

function emptyVisit(date: string): Visit {
  return { date, note: "", done: {}, extras: {}, photos: [] };
}

function withVisit(state: AppData, date: string, change: (visit: Visit) => Visit): AppData {
  const current = state.visits[date] ?? emptyVisit(date);
  return { ...state, visits: { ...state.visits, [date]: change(current) } };
}

function withJob(state: AppData, jobId: number, change: (job: Job) => Job): AppData {
  if (!state.jobs) return state;
  return { ...state, jobs: state.jobs.map((job) => (job.id === jobId ? change(job) : job)) };
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
      for (const t of action.payload.tasks ?? []) tasks[t.id] = t;
      return {
        ...state,
        me: action.payload.me,
        tasks,
        visits: action.payload.visits ?? {},
        jobs: action.payload.jobs ?? null,
        hours: action.payload.hours ?? null,
        loadedState: true,
      };
    }
    case "users":
      return { ...state, users: action.users };
    case "tab":
      return { ...state, ui: { ...state.ui, tab: action.tab, confirm: null, intent: action.intent ?? null } };
    case "intent":
      return { ...state, ui: { ...state.ui, intent: action.intent } };
    case "date":
      return { ...state, ui: { ...state.ui, date: action.date, confirm: null } };
    case "confirm":
      return { ...state, ui: { ...state.ui, confirm: action.key } };
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
    case "job-status":
      return withJob(state, action.jobId, (job) => ({ ...job, status: action.status }));
    case "remove-job":
      if (!state.jobs) return state;
      return {
        ...state,
        jobs: state.jobs.filter((job) => job.id !== action.jobId),
        ui: { ...state.ui, confirm: null },
      };
    case "remove-job-comment":
      return {
        ...withJob(state, action.jobId, (job) => ({
          ...job,
          comments: job.comments.filter((c) => c.id !== action.commentId),
        })),
        ui: { ...state.ui, confirm: null },
      };
    case "remove-job-photo":
      return {
        ...withJob(state, action.jobId, (job) => ({
          ...job,
          photos: job.photos.filter((p) => p.id !== action.photoId),
        })),
        ui: { ...state.ui, confirm: null },
      };
    case "claim-status": {
      if (!state.hours) return state;
      const ids = new Set(action.claimIds);
      return {
        ...state,
        hours: state.hours.map((c) => (ids.has(c.id) ? { ...c, status: action.status } : c)),
      };
    }
    case "remove-claim":
      if (!state.hours) return state;
      return {
        ...state,
        hours: state.hours.filter((c) => c.id !== action.claimId),
        ui: { ...state.ui, confirm: null },
      };
  }
}

export interface WriteRequest {
  method: Method;
  path: string;
  body?: unknown;
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
  /** Several writes at once (e.g. "Approve all"), one toast and one resync for the lot. */
  mutateAll: (requests: WriteRequest[], okMsg?: string) => Promise<void>;
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

  const refresh = useCallback(async () => {
    if (!session.current.signedIn) return;
    // Any sign-in/sign-out while a request is in flight makes this pass stale: bail after
    // every await so a late response can't dispatch the previous session's data.
    const started = session.current.epoch;
    const snapshot = stateRef.current;
    try {
      const data = await api<StateResponse>("GET", "/api/state");
      if (session.current.epoch !== started) return;
      const { tab } = snapshot.ui;
      // Setup lists people; Jobs uses the same list to tell a homeowner's comment from the builder's.
      let users: User[] | null = null;
      if ((tab === "setup" || tab === "jobs") && isOwner(data.me)) {
        users = (await api<UsersResponse>("GET", "/api/users")).users;
        if (session.current.epoch !== started) return;
      }
      const sig = JSON.stringify([data.me, data.tasks, data.visits, data.jobs, data.hours, users]);
      if (sig === lastSig.current) return;
      lastSig.current = sig;
      dispatch({ type: "server-state", payload: data });
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
      // A sign-out while the write is in flight must not pop a success toast over the login screen.
      const started = session.current.epoch;
      try {
        await api<unknown>(method, path, body);
        if (okMsg && session.current.epoch === started) toast(okMsg);
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

  const mutateAll = useCallback(
    async (requests: WriteRequest[], okMsg?: string): Promise<void> => {
      const started = session.current.epoch;
      try {
        const results = await Promise.allSettled(requests.map((r) => api<unknown>(r.method, r.path, r.body)));
        const failed = results.find((r): r is PromiseRejectedResult => r.status === "rejected");
        if (failed) {
          lastSig.current = "";
          const e: unknown = failed.reason;
          if (e instanceof ApiError && e.status !== 401) toast(e.message);
          throw e;
        }
        if (okMsg && session.current.epoch === started) toast(okMsg);
      } finally {
        await refresh();
      }
    },
    [refresh, toast],
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
    () => ({ state, dispatch, refresh, mutate, mutateAll, goToTab, signIn, signOut }),
    [state, dispatch, refresh, mutate, mutateAll, goToTab, signIn, signOut],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

/** What the signed-in person may see. All false while signed out. */
export function useAccess(): { isOwner: boolean; visit: boolean; jobs: boolean; hours: boolean } {
  const me = useApp().state.me;
  if (!me) return { isOwner: false, visit: false, jobs: false, hours: false };
  return { isOwner: isOwner(me), visit: seesVisit(me), jobs: seesJobs(me), hours: seesHours(me) };
}
