import { Activity, useEffect, useState, type ReactNode } from "react";
import { api } from "./api/client";
import type { LoginOptions, Me, MeResponse } from "./api/types";
import { BottomNav, type NavItem } from "./components/BottomNav";
import { LoginScreen } from "./features/auth/LoginScreen";
import { HomeScreen } from "./features/home/HomeScreen";
import { HoursScreen } from "./features/hours/HoursScreen";
import { JobsScreen } from "./features/jobs/JobsScreen";
import { SetupScreen } from "./features/setup/SetupScreen";
import { VisitScreen } from "./features/visit/VisitScreen";
import { tabsFor } from "./lib/access";
import { AppProvider, tabFromHash, useApp, type Tab } from "./state/AppState";
import { usePolling } from "./state/usePolling";
import { ToastProvider } from "./state/useToast";

const NAV: Record<Tab, Omit<NavItem, "key">> = {
  home: { icon: "🌊", label: "Home" },
  visit: { icon: "✅", label: "Visit" },
  jobs: { icon: "🔨", label: "Jobs" },
  hours: { icon: "⏱️", label: "Hours" },
  setup: { icon: "⚙️", label: "Setup" },
};

function navItemsFor(me: Me): NavItem[] {
  return tabsFor(me).map((key) => ({ key, ...NAV[key] }));
}

function Shell() {
  const { state, dispatch, goToTab } = useApp();
  const [smsEnabled, setSmsEnabled] = useState<boolean | null>(null);
  usePolling();

  // Boot: who am I? (spec §7.3)
  useEffect(() => {
    void (async () => {
      try {
        const { me } = await api<MeResponse>("GET", "/api/me");
        dispatch({ type: "booted", me });
      } catch {
        dispatch({ type: "booted", me: null });
      }
    })();
  }, [dispatch]);

  // Signed out: is text-message sign-in configured on this server?
  useEffect(() => {
    if (!state.booted || state.me) return;
    void (async () => {
      try {
        const options = await api<LoginOptions>("GET", "/api/login/options");
        setSmsEnabled(options.sms);
      } catch {
        setSmsEnabled(false);
      }
    })();
  }, [state.booted, state.me]);

  // Back/forward and typed hashes move the tab.
  useEffect(() => {
    const onHashChange = () => {
      const tab = tabFromHash();
      if (tab) dispatch({ type: "tab", tab });
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, [dispatch]);

  const me = state.me;
  const tab = state.ui.tab;
  const items = me ? navItemsFor(me) : [];
  const allowed = items.some((item) => item.key === tab);

  // Someone landing on a tab that isn't theirs (a builder on #visit, staff on #setup) goes Home.
  useEffect(() => {
    if (me && !allowed) dispatch({ type: "tab", tab: "home" });
  }, [me, allowed, dispatch]);

  useEffect(() => {
    if (!me) return;
    try {
      window.history.replaceState(null, "", `#${tab}`);
    } catch {
      // Some embedded browsers refuse replaceState; the tab still works.
    }
  }, [me, tab]);

  if (!state.booted) return <div className="empty">Loading your run sheet…</div>;
  // A bare landmark: LoginScreen brings its own .login-wrap column, and .app would
  // add the bottom nav's gap under a screen that has no bottom nav.
  if (!me)
    return (
      <main>
        <LoginScreen smsEnabled={smsEnabled} />
      </main>
    );

  const current: Tab = allowed ? tab : "home";
  const mine = new Set(items.map((item) => item.key));
  const screens: [Tab, ReactNode][] = [
    ["home", <HomeScreen />],
    ["visit", <VisitScreen />],
    ["jobs", <JobsScreen />],
    ["hours", <HoursScreen />],
    ["setup", <SetupScreen />],
  ];

  return (
    <>
      <main className="app">
        {screens.map(([key, screen]) =>
          mine.has(key) ? (
            <Activity key={key} mode={current === key ? "visible" : "hidden"}>
              {screen}
            </Activity>
          ) : null,
        )}
      </main>
      <BottomNav items={items} active={current} onSelect={(next) => goToTab(next)} />
    </>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppProvider>
        <Shell />
      </AppProvider>
    </ToastProvider>
  );
}
