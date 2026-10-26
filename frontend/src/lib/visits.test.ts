import { describe, expect, it } from "vitest";
import type { Extra, Task, Visit } from "../api/types";
import { extrasOn, hasActivity, openExtras, readyCount, stepThrough, todayLabel, toggleExtra, visitDates } from "./visits";

const TODAY = "2026-10-26";

function visit(date: string, over: Partial<Visit> = {}): Visit {
  return { date, note: "", done: {}, extras: {}, photos: [], ...over };
}

function extra(title: string, done: boolean, createdAt = "2026-10-01T09:00:00Z"): Extra {
  return { title, notes: "", done, createdAt };
}

function task(id: string, freq: Task["freq"] = "visit"): Task {
  return { id, title: id, area: "Kitchen", freq, notes: "", order: 0, active: true };
}

const photo = {
  id: 1, kind: "done" as const, caption: "", url: "/photos/x.jpg", createdAt: "x", by: { id: 1, displayName: "M" },
};

describe("hasActivity", () => {
  it("is false for a missing or empty visit, and for one holding only open extras", () => {
    expect(hasActivity(undefined)).toBe(false);
    expect(hasActivity(visit(TODAY))).toBe(false);
    expect(hasActivity(visit(TODAY, { note: "   ", extras: { "1": extra("x", false) } }))).toBe(false);
  });

  it("counts a tick, an extra done, a note or a photo", () => {
    expect(hasActivity(visit(TODAY, { done: { t1: "x" } }))).toBe(true);
    expect(hasActivity(visit(TODAY, { extras: { "1": extra("x", true) } }))).toBe(true);
    expect(hasActivity(visit(TODAY, { note: "Out of soap" }))).toBe(true);
    expect(hasActivity(visit(TODAY, { photos: [photo] }))).toBe(true);
  });
});

describe("todayLabel", () => {
  it("is Next visit until something is recorded today", () => {
    expect(todayLabel({}, TODAY)).toBe("Next visit");
    expect(todayLabel({ [TODAY]: visit(TODAY, { extras: { "1": extra("x", false) } }) }, TODAY)).toBe("Next visit");
    expect(todayLabel({ [TODAY]: visit(TODAY, { done: { t1: "x" } }) }, TODAY)).toBe("Today's visit");
  });
});

describe("visitDates / stepThrough", () => {
  const visits = {
    "2026-10-02": visit("2026-10-02", { done: { t1: "x" } }),
    "2026-10-09": visit("2026-10-09", { extras: { "5": extra("open", false) } }), // nothing recorded
    "2026-10-14": visit("2026-10-14", { note: "Hi" }),
    "2026-10-20": visit("2026-10-20", { photos: [photo] }),
    "2026-10-30": visit("2026-10-30", { done: { t1: "x" } }), // future: never listed
  };

  it("lists past dates with activity, oldest first, then always today", () => {
    expect(visitDates(visits, TODAY)).toEqual(["2026-10-02", "2026-10-14", "2026-10-20", TODAY]);
    expect(visitDates({}, TODAY)).toEqual([TODAY]);
  });

  it("keeps today once, even when it has activity", () => {
    expect(visitDates({ [TODAY]: visit(TODAY, { done: { t1: "x" } }) }, TODAY)).toEqual([TODAY]);
  });

  it("steps to the neighbouring date and stops at either end", () => {
    const dates = visitDates(visits, TODAY);
    expect(stepThrough(dates, TODAY, -1)).toBe("2026-10-20");
    expect(stepThrough(dates, "2026-10-20", -1)).toBe("2026-10-14");
    expect(stepThrough(dates, "2026-10-14", 1)).toBe("2026-10-20");
    expect(stepThrough(dates, TODAY, 1)).toBeNull();
    expect(stepThrough(dates, "2026-10-02", -1)).toBeNull();
  });

  it("steps from a date that is no longer in the list", () => {
    const dates = visitDates(visits, TODAY);
    expect(stepThrough(dates, "2026-10-10", -1)).toBe("2026-10-02");
    expect(stepThrough(dates, "2026-10-10", 1)).toBe("2026-10-14");
  });
});

describe("extrasOn / openExtras", () => {
  const visits = {
    "2026-10-02": visit("2026-10-02", {
      extras: { "3": extra("Old open job", false, "2026-10-02T09:00:00Z"), "4": extra("Done that day", true) },
    }),
    "2026-10-20": visit("2026-10-20", { extras: { "7": extra("Done on the 20th", true, "2026-10-19T09:00:00Z") } }),
    [TODAY]: visit(TODAY, {
      extras: { "8": extra("Added today", false, "2026-10-26T08:00:00Z"), "9": extra("Done today", true, "2026-10-25T08:00:00Z") },
    }),
  };

  it("puts every open extra on today's sheet, plus the ones done today, oldest first", () => {
    expect(extrasOn(visits, TODAY, TODAY).map((x) => [x.id, x.storedOn])).toEqual([
      ["3", "2026-10-02"],
      ["9", TODAY],
      ["8", TODAY],
    ]);
  });

  it("shows only the extras done on a past date", () => {
    expect(extrasOn(visits, "2026-10-02", TODAY).map((x) => x.id)).toEqual(["4"]);
    expect(extrasOn(visits, "2026-10-20", TODAY).map((x) => x.id)).toEqual(["7"]);
    expect(extrasOn(visits, "2026-10-14", TODAY)).toEqual([]);
  });

  it("gathers open extras wherever they are stored", () => {
    expect(openExtras(visits).map((x) => x.id).sort()).toEqual(["3", "8"]);
  });
});

describe("toggleExtra", () => {
  const visits = {
    "2026-10-02": visit("2026-10-02", { extras: { "3": extra("Job", false) }, note: "keep" }),
  };

  it("moves an extra onto the day it was ticked", () => {
    const next = toggleExtra(visits, "2026-10-02", TODAY, "3", true);
    expect(next["2026-10-02"]!.extras).toEqual({});
    expect(next["2026-10-02"]!.note).toBe("keep");
    expect(next[TODAY]!.extras["3"]).toEqual({ ...extra("Job", false), done: true });
    expect(visits["2026-10-02"]!.extras["3"]!.done).toBe(false); // not mutated
  });

  it("leaves an unticked extra where it is", () => {
    const done = toggleExtra(visits, "2026-10-02", TODAY, "3", true);
    const undone = toggleExtra(done, TODAY, "2026-10-28", "3", false);
    expect(undone[TODAY]!.extras["3"]!.done).toBe(false);
    expect(undone["2026-10-28"]).toBeUndefined();
  });

  it("ignores an extra it can't find", () => {
    expect(toggleExtra(visits, TODAY, TODAY, "3", true)).toBe(visits);
  });
});

describe("readyCount", () => {
  it("counts due tasks not yet ticked today plus every open extra", () => {
    const visits = {
      "2026-10-20": visit("2026-10-20", { done: { w: "x" }, extras: { "3": extra("Open", false) } }),
      [TODAY]: visit(TODAY, { done: { a: "x" }, extras: { "8": extra("Done", true) } }),
    };
    // a is ticked, b is due, w (weekly) was done 6 days ago so it's due again, m (monthly) never done.
    const tasks = [task("a"), task("b"), task("w", "weekly"), task("m", "monthly")];
    expect(readyCount(tasks, visits, TODAY)).toBe(3 + 1);
    const recent = { ...visits, "2026-10-23": visit("2026-10-23", { done: { w: "x" } }) };
    expect(readyCount(tasks, recent, TODAY)).toBe(2 + 1);
  });
});
