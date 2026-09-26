import { describe, expect, it } from "vitest";
import type { Task, Visit } from "../api/types";
import {
  AREA_ORDER, DAY_LABEL, FREQ_GAP, FREQ_LABEL, areaRank, doneDatesByTask, groupByArea,
  isDue, lastDoneBefore, sortTasks,
} from "./schedule";

function task(over: Partial<Task> & { id: string }): Task {
  return {
    id: over.id,
    title: over.title ?? "A task",
    area: over.area ?? "Kitchen",
    freq: over.freq ?? "visit",
    day: over.day ?? "any",
    notes: over.notes ?? "",
    order: over.order ?? 0,
    active: over.active ?? true,
    ...(over.link === undefined ? {} : { link: over.link }),
  };
}

/** Build a visits map from { date: [taskIds ticked that day] }. */
function visitsWith(map: Record<string, string[]>): Record<string, Visit> {
  const out: Record<string, Visit> = {};
  for (const [date, ids] of Object.entries(map)) {
    const done: Record<string, string> = {};
    for (const id of ids) done[id] = `${date}T18:00:00`;
    out[date] = { date, note: "", done, extras: {}, photos: [] };
  }
  return out;
}

const TUE = "2026-09-22";
const FRI = "2026-09-25";
const NEXT_TUE = "2026-09-29";

describe("constants", () => {
  it("orders areas the way the visit screen groups them", () => {
    expect(AREA_ORDER[0]).toBe("Meal prep");
    expect(AREA_ORDER).toContain("Whole house");
    expect(AREA_ORDER.indexOf("Kitchen")).toBeLessThan(AREA_ORDER.indexOf("Outdoor"));
  });

  it("keeps the v1 frequency gaps", () => {
    expect(FREQ_GAP.weekly).toBe(5);
    expect(FREQ_GAP.fortnightly).toBe(12);
    expect(FREQ_GAP.monthly).toBe(26);
    expect(FREQ_GAP.visit).toBeUndefined();
  });

  it("labels frequencies and days for the pills", () => {
    expect(FREQ_LABEL.visit).toBe("Every visit");
    expect(FREQ_LABEL.fortnightly).toBe("Every 2 weeks");
    expect(DAY_LABEL.any).toBe("Tue or Fri");
    expect(DAY_LABEL.tue).toBe("Tuesdays");
    expect(DAY_LABEL.fri).toBe("Fridays");
  });
});

describe("doneDatesByTask", () => {
  it("collects completion dates per task in ascending order", () => {
    const map = doneDatesByTask(visitsWith({ [FRI]: ["t1"], [TUE]: ["t1", "t2"] }));
    expect(map.t1).toEqual([TUE, FRI]);
    expect(map.t2).toEqual([TUE]);
    expect(map.t3).toBeUndefined();
  });

  it("ignores visits with no completions", () => {
    const visits = visitsWith({ [TUE]: [] });
    expect(doneDatesByTask(visits)).toEqual({});
  });
});

describe("lastDoneBefore", () => {
  it("returns null when there is no history", () => {
    expect(lastDoneBefore(undefined, TUE)).toBeNull();
    expect(lastDoneBefore([], TUE)).toBeNull();
  });

  it("returns the latest date strictly before the given date", () => {
    expect(lastDoneBefore([TUE, FRI], NEXT_TUE)).toBe(FRI);
    expect(lastDoneBefore([TUE, FRI], FRI)).toBe(TUE);
    expect(lastDoneBefore([TUE, FRI], TUE)).toBeNull();
  });
});

describe("isDue", () => {
  const noVisits: Record<string, Visit> = {};

  it("skips inactive tasks", () => {
    expect(isDue(task({ id: "t1", active: false }), TUE, {}, noVisits)).toBe(false);
  });

  it("pins tasks to their day", () => {
    const tueOnly = task({ id: "t1", day: "tue" });
    const friOnly = task({ id: "t2", day: "fri" });
    expect(isDue(tueOnly, TUE, {}, noVisits)).toBe(true);
    expect(isDue(tueOnly, FRI, {}, noVisits)).toBe(false);
    expect(isDue(friOnly, FRI, {}, noVisits)).toBe(true);
    expect(isDue(friOnly, TUE, {}, noVisits)).toBe(false);
  });

  it("shows every-visit tasks on both days", () => {
    const t = task({ id: "t1", freq: "visit" });
    expect(isDue(t, TUE, {}, noVisits)).toBe(true);
    expect(isDue(t, FRI, {}, noVisits)).toBe(true);
  });

  it("keeps a task visible on a day it was already ticked, even inside the gap", () => {
    const t = task({ id: "t1", freq: "weekly" });
    const visits = visitsWith({ [TUE]: ["t1"], [FRI]: ["t1"] });
    const doneMap = doneDatesByTask(visits);
    // Last done 3 days ago (< the 5-day weekly gap) but ticked today, so it stays on the list.
    expect(isDue(t, FRI, doneMap, visits)).toBe(true);
  });

  it("always shows a weekly task pinned to a day, on that day", () => {
    const t = task({ id: "t1", freq: "weekly", day: "tue" });
    const visits = visitsWith({ "2026-09-21": ["t1"] });
    expect(isDue(t, TUE, doneDatesByTask(visits), visits)).toBe(true);
  });

  it("shows a never-done recurring task the first time", () => {
    expect(isDue(task({ id: "t1", freq: "monthly" }), TUE, {}, noVisits)).toBe(true);
  });

  it("applies the weekly gap of 5 days", () => {
    const t = task({ id: "t1", freq: "weekly" });
    const visits = visitsWith({ [TUE]: ["t1"] });
    const doneMap = doneDatesByTask(visits);
    expect(isDue(t, FRI, doneMap, visits)).toBe(false);
    expect(isDue(t, NEXT_TUE, doneMap, visits)).toBe(true);
  });

  it("applies the fortnightly gap of 12 days", () => {
    const t = task({ id: "t1", freq: "fortnightly" });
    const visits = visitsWith({ [TUE]: ["t1"] });
    const doneMap = doneDatesByTask(visits);
    expect(isDue(t, "2026-10-02", doneMap, visits)).toBe(false);
    expect(isDue(t, "2026-10-06", doneMap, visits)).toBe(true);
  });

  it("applies the monthly gap of 26 days", () => {
    const t = task({ id: "t1", freq: "monthly" });
    const visits = visitsWith({ [TUE]: ["t1"] });
    const doneMap = doneDatesByTask(visits);
    expect(isDue(t, "2026-10-13", doneMap, visits)).toBe(false);
    expect(isDue(t, "2026-10-20", doneMap, visits)).toBe(true);
  });
});

describe("areaRank / sortTasks / groupByArea", () => {
  it("ranks known areas by AREA_ORDER and unknown areas last", () => {
    expect(areaRank("Meal prep")).toBe(0);
    expect(areaRank("Garage")).toBe(100);
    expect(areaRank("Kitchen")).toBeLessThan(areaRank("Laundry"));
  });

  it("sorts by area order, then area name, then task order", () => {
    const sorted = sortTasks([
      task({ id: "c", area: "Kitchen", order: 2 }),
      task({ id: "a", area: "Meal prep", order: 9 }),
      task({ id: "d", area: "Zoo", order: 1 }),
      task({ id: "b", area: "Kitchen", order: 1 }),
    ]);
    expect(sorted.map((t) => t.id)).toEqual(["a", "b", "c", "d"]);
  });

  it("does not mutate the input array", () => {
    const input = [task({ id: "b", area: "Kitchen" }), task({ id: "a", area: "Meal prep" })];
    sortTasks(input);
    expect(input.map((t) => t.id)).toEqual(["b", "a"]);
  });

  it("groups sorted tasks by area, keeping order, and labels blanks Other", () => {
    const groups = groupByArea(
      sortTasks([
        task({ id: "a", area: "Meal prep" }),
        task({ id: "c", area: "" }),
        task({ id: "b", area: "Kitchen" }),
      ]),
    );
    expect(groups.map(([area, list]) => [area, list.map((t) => t.id)])).toEqual([
      ["Meal prep", ["a"]],
      ["Kitchen", ["b"]],
      ["Other", ["c"]],
    ]);
  });
});
