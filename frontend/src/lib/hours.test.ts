import { describe, expect, it } from "vitest";
import type { ClaimStatus, HoursClaim } from "../api/types";
import {
  HOURS_RULE, groupByWeek, hoursLabel, hoursText, hoursToMinutes, minutesInWeek, minutesWith, ownerMoves,
  sumMinutes, summarise,
} from "./hours";

const MARIA = { id: 2, displayName: "Maria" };
const ANA = { id: 3, displayName: "Ana" };

function claim(id: number, date: string, minutes: number, status: ClaimStatus = "submitted", by = MARIA): HoursClaim {
  return {
    id, date, minutes, note: "", status, createdAt: `${date}T18:00:00`, by,
    approvedAt: null, approvedBy: null, paidAt: null, paidBy: null,
  };
}

describe("hoursText / hoursLabel", () => {
  it("drops trailing zeros", () => {
    expect(hoursText(120)).toBe("2");
    expect(hoursText(90)).toBe("1.5");
    expect(hoursText(135)).toBe("2.25");
    expect(hoursText(15)).toBe("0.25");
    expect(hoursText(0)).toBe("0");
  });

  it("adds the unit", () => {
    expect(hoursLabel(270)).toBe("4.5 h");
  });
});

describe("hoursToMinutes", () => {
  it("turns quarter hours into minutes", () => {
    expect(hoursToMinutes("0.25")).toBe(15);
    expect(hoursToMinutes("1.5")).toBe(90);
    expect(hoursToMinutes("16")).toBe(960);
    expect(hoursToMinutes(" 3.75 ")).toBe(225);
    expect(hoursToMinutes(2)).toBe(120);
    expect(hoursToMinutes(".5")).toBe(30);
  });

  it("accepts a decimal comma", () => {
    expect(hoursToMinutes("2,5")).toBe(150);
  });

  it("refuses anything that isn't a quarter hour from 0.25 to 16", () => {
    expect(hoursToMinutes("0")).toBeNull();
    expect(hoursToMinutes("0.1")).toBeNull();
    expect(hoursToMinutes("1.3")).toBeNull();
    expect(hoursToMinutes("16.25")).toBeNull();
    expect(hoursToMinutes("-1")).toBeNull();
    expect(hoursToMinutes("")).toBeNull();
    expect(hoursToMinutes("abc")).toBeNull();
    expect(hoursToMinutes("1e1")).toBeNull();
  });

  it("matches the server's wording", () => {
    expect(HOURS_RULE).toBe("Hours go in quarter hours, from 0.25 to 16.");
  });
});

describe("totals", () => {
  const claims = [
    claim(1, "2026-10-20", 240, "submitted"),
    claim(2, "2026-10-23", 180, "approved"),
    claim(3, "2026-10-13", 60, "paid"),
    claim(4, "2026-10-26", 90, "submitted", ANA),
  ];

  it("sums minutes, by status and by week", () => {
    expect(sumMinutes(claims)).toBe(570);
    expect(minutesWith(claims, "submitted")).toBe(330);
    expect(minutesWith(claims, "paid")).toBe(60);
    expect(minutesInWeek(claims, "2026-10-19")).toBe(420);
    expect(minutesInWeek(claims, "2026-10-26")).toBe(90);
    expect(minutesInWeek(claims, "2026-10-05")).toBe(0);
  });

  it("summarises what's waiting and what's unpaid", () => {
    expect(summarise(claims)).toEqual({ toApprove: 330, toApproveCount: 2, approvedUnpaid: 180 });
    expect(summarise([])).toEqual({ toApprove: 0, toApproveCount: 0, approvedUnpaid: 0 });
  });
});

describe("groupByWeek", () => {
  it("groups by Monday, newest week first, with totals per week and per person", () => {
    const groups = groupByWeek([
      claim(1, "2026-10-13", 60, "paid"),
      claim(2, "2026-10-20", 240),
      claim(3, "2026-10-25", 120, "submitted", ANA), // Sunday: still the week of the 19th
      claim(4, "2026-10-23", 180, "approved"),
      claim(5, "2026-10-23", 30),
    ]);
    expect(groups.map((g) => g.week)).toEqual(["2026-10-19", "2026-10-12"]);
    const [latest, earlier] = groups;
    expect(latest?.claims.map((c) => c.id)).toEqual([3, 5, 4, 2]);
    expect(latest?.minutes).toBe(570);
    expect(latest?.people).toEqual([
      { person: ANA, minutes: 120 },
      { person: MARIA, minutes: 450 },
    ]);
    expect(latest?.submittedIds).toEqual([3, 5, 2]);
    expect(earlier?.minutes).toBe(60);
    expect(earlier?.submittedIds).toEqual([]);
  });

  it("returns nothing for no claims", () => {
    expect(groupByWeek([])).toEqual([]);
  });

  it("does not reorder the input", () => {
    const input = [claim(1, "2026-10-13", 60), claim(2, "2026-10-20", 60)];
    groupByWeek(input);
    expect(input.map((c) => c.id)).toEqual([1, 2]);
  });
});

describe("ownerMoves", () => {
  it("offers approve, then paid, with an undo back each step", () => {
    expect(ownerMoves("submitted").map((m) => m.to)).toEqual(["approved"]);
    expect(ownerMoves("approved").map((m) => m.to)).toEqual(["paid", "submitted"]);
    expect(ownerMoves("paid").map((m) => m.to)).toEqual(["approved"]);
  });
});
