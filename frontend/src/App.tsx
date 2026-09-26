import { Activity, useEffect, useState } from "react";
import { api } from "./api/client";
import type { LoginOptions, Me, MeResponse } from "./api/types";
import { BottomNav, type NavItem } from "./components/BottomNav";
import { LoginScreen } from "./features/auth/LoginScreen";
import { HomeScreen } from "./features/home/HomeScreen";
import { MealsScreen } from "./features/meals/MealsScreen";
import { SetupScreen } from "./features/setup/SetupScreen";
import { ShoppingScreen } from "./features/shopping/ShoppingScreen";
import { VisitScreen } from "./features/visit/VisitScreen";
import { AppProvider, tabFromHash, useApp, type Tab } from "./state/AppState";
import { usePolling } from "./state/usePolling";
import { ToastProvider } from "./state/useToast";

function navItemsFor(me: Me): NavItem[] {
  const items: NavItem[] = [
    { key: "home", icon: "🌊", label: "Home" },
    { key: "visit", icon: "✅", label: "Visit" },
  ];
  if (me.role === "owner" || me.canSeeMeals) items.push({ key: "meals", icon: "🍳", label: "Meals" });
  if (me.role === "owner") {
    items.push({ key: "shopping", icon: "🛒", label: "Shopping" });
    items.push({ key: "setup", icon: "⚙️", label: "Setup" });
  }
  return items;
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

  // A staff member landing on #setup (or losing Meals) goes Home.
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

  const isOwner = me.role === "owner";
  const canSeeMeals = isOwner || me.canSeeMeals;
  const current: Tab = allowed ? tab : "home";

  return (
    <>
      <main className="app">
        <Activity mode={current === "home" ? "visible" : "hidden"}>
          <HomeScreen />
        </Activity>
        <Activity mode={current === "visit" ? "visible" : "hidden"}>
          <VisitScreen />
        </Activity>
        {canSeeMeals ? (
          <Activity mode={current === "meals" ? "visible" : "hidden"}>
            <MealsScreen />
          </Activity>
        ) : null}
        {isOwner ? (
          <Activity mode={current === "shopping" ? "visible" : "hidden"}>
            <ShoppingScreen />
          </Activity>
        ) : null}
        {isOwner ? (
          <Activity mode={current === "setup" ? "visible" : "hidden"}>
            <SetupScreen />
          </Activity>
        ) : null}
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
