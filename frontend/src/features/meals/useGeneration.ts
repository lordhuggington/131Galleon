import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { Job, JobStarted } from "../../api/types";
import { useApp } from "../../state/AppState";

export interface GenState {
  running: boolean;
  status: string;
  titles: string[];
  error: string;
  jobId: number | null;
}

const IDLE: GenState = { running: false, status: "", titles: [], error: "", jobId: null };
const JOB_POLL_MS = 2000;
const FIRST_STATUS = "Thinking about this week's menu… (the first words usually take 20–60 seconds)";
const GENERIC_ERROR = "Something went wrong while writing the menu. Try again.";

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    window.setTimeout(resolve, ms);
  });

export function useGeneration(): { gen: GenState; start: (note: string) => void; stop: () => void } {
  const { state, dispatch, loadPlan } = useApp();
  const [gen, setGen] = useState<GenState>(IDLE);
  const alive = useRef(true);
  const jobRef = useRef<number | null>(null);
  const polling = useRef(false);
  const week = state.ui.week;

  // Polls one job until it settles. Exits quietly when the screen is hidden so the
  // show effect can pick it back up without two pollers running.
  const poll = useCallback(
    async (jobId: number) => {
      polling.current = true;
      try {
        for (;;) {
          await sleep(JOB_POLL_MS);
          if (!alive.current) return;
          let job: Job;
          try {
            job = await api<Job>("GET", `/api/jobs/${jobId}`);
          } catch (e) {
            if (e instanceof ApiError && e.status === 401) {
              jobRef.current = null;
              return;
            }
            continue;
          }
          if (job.status === "running" || job.status === "cancelling") {
            setGen((g) => ({
              ...g,
              status: job.progressChars
                ? `Writing the menu… ${job.progressChars.toLocaleString()} characters so far`
                : g.status,
              titles: job.titles,
            }));
            continue;
          }
          jobRef.current = null;
          if (job.status === "done") {
            setGen({ ...IDLE, status: "Saved. Recipes are on this tab and the shopping list is ready." });
            dispatch({ type: "week", week: job.week });
            dispatch({ type: "session", session: "tue" });
            await loadPlan(job.week);
          } else if (job.status === "cancelled") {
            setGen(IDLE);
          } else {
            setGen({ ...IDLE, error: job.error ?? GENERIC_ERROR });
          }
          return;
        }
      } finally {
        polling.current = false;
      }
    },
    [dispatch, loadPlan],
  );

  // Activity hides this screen by cleaning up effects but keeping state: on re-show,
  // pick a still-running job's poller back up.
  useEffect(() => {
    alive.current = true;
    if (jobRef.current !== null && !polling.current) void poll(jobRef.current);
    return () => {
      alive.current = false;
    };
  }, [poll]);

  const start = useCallback(
    (note: string) => {
      if (jobRef.current !== null) return;
      dispatch({ type: "confirm", key: null });
      setGen({ running: true, status: FIRST_STATUS, titles: [], error: "", jobId: null });
      void (async () => {
        let jobId: number;
        try {
          jobId = (await api<JobStarted>("POST", `/api/plans/${week}/generate`, { note })).jobId;
        } catch (e) {
          // 409 means a job is already writing this menu: follow that one instead.
          const running = e instanceof ApiError && e.status === 409 ? (e.body as { jobId?: number } | null)?.jobId : undefined;
          if (typeof running === "number") {
            jobId = running;
          } else {
            setGen({ ...IDLE, error: e instanceof Error ? e.message : GENERIC_ERROR });
            return;
          }
        }
        jobRef.current = jobId;
        setGen((g) => ({ ...g, jobId }));
        await poll(jobId);
      })();
    },
    [dispatch, poll, week],
  );

  const stop = useCallback(() => {
    const jobId = jobRef.current;
    if (jobId !== null) void api<unknown>("POST", `/api/jobs/${jobId}/cancel`).catch(() => {});
    setGen((g) => ({ ...g, status: "Stopping…" }));
  }, []);

  return { gen, start, stop };
}
