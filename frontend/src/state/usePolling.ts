import { useEffect, useEffectEvent } from "react";
import { useApp } from "./AppState";

export const POLL_MS = 20000;

export function usePolling(): void {
  const { state, refresh } = useApp();
  const signedIn = state.me !== null;
  const { tab, week } = state.ui;

  // useEffectEvent keeps the latest refresh without re-running the effects below.
  const poll = useEffectEvent(() => {
    void refresh();
  });

  useEffect(() => {
    if (!signedIn) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") poll();
    }, POLL_MS);
    const onVisibility = () => {
      if (document.visibilityState === "visible") poll();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [signedIn]);

  // Refresh when the scope changes: a new tab or week needs different data.
  useEffect(() => {
    if (signedIn) poll();
  }, [signedIn, tab, week]);
}
