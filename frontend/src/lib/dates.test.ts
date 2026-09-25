import { describe, expect, it } from "vitest";
import {
  addDays, diffDays, dow, fmt, fmtDay, fmtLong, fmtShort, fmtStamp, fmtWeekday, iso, isVisitDay,
  mondayOf, nextVisit, pad, parse, sessionOf, stepVisit, today,
} from "./dates";

describe("pad / iso / parse", () => {
  it("pads single digits to two", () => {
    expect(pad(3)).toBe("03");
    expect(pad(12)).toBe("12");
  });

  it("round-trips a date string", () => {
    expect(iso(parse("2026-09-22"))).toBe("2026-09-22");
    expect(iso(parse("2026-01-05"))).toBe("2026-01-05");
  });

  it("parses to local midnight, not UTC", () => {
    const d = parse("2026-09-22");
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(8);
    expect(d.getDate()).toBe(22);
    expect(d.getHours()).toBe(0);
  });

  it("today() is a valid YYYY-MM-DD string", () => {
    expect(today()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("addDays", () => {
  it("crosses a month end", () => {
    expect(addDays("2026-01-31", 1)).toBe("2026-02-01");
  });

  it("crosses February in a non-leap year", () => {
    expect(addDays("2026-02-28", 1)).toBe("2026-03-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });

  it("crosses a year end", () => {
    expect(addDays("2026-12-31", 1)).toBe("2027-01-01");
  });

  it("steps whole weeks", () => {
    expect(addDays("2026-09-21", 7)).toBe("2026-09-28");
    expect(addDays("2026-09-21", -7)).toBe("2026-09-14");
  });
});

describe("dow / diffDays", () => {
  it("numbers the weekdays Sunday-first", () => {
    expect(dow("2026-09-27")).toBe(0);
    expect(dow("2026-09-21")).toBe(1);
    expect(dow("2026-09-22")).toBe(2);
    expect(dow("2026-09-25")).toBe(5);
  });

  it("counts whole days, including across a daylight-saving change", () => {
    expect(diffDays("2026-09-22", "2026-09-25")).toBe(3);
    expect(diffDays("2026-09-25", "2026-09-22")).toBe(-3);
    expect(diffDays("2026-03-07", "2026-03-09")).toBe(2);
    expect(diffDays("2026-10-31", "2026-11-02")).toBe(2);
  });
});

describe("mondayOf", () => {
  it("returns the same day for a Monday", () => {
    expect(mondayOf("2026-09-21")).toBe("2026-09-21");
  });

  it("walks back from midweek", () => {
    expect(mondayOf("2026-09-24")).toBe("2026-09-21");
  });

  it("treats Sunday as the last day of its week", () => {
    expect(mondayOf("2026-09-27")).toBe("2026-09-21");
  });
});

describe("visit days", () => {
  it("only Tuesday and Friday are visit days", () => {
    expect(isVisitDay("2026-09-21")).toBe(false);
    expect(isVisitDay("2026-09-22")).toBe(true);
    expect(isVisitDay("2026-09-23")).toBe(false);
    expect(isVisitDay("2026-09-24")).toBe(false);
    expect(isVisitDay("2026-09-25")).toBe(true);
    expect(isVisitDay("2026-09-26")).toBe(false);
    expect(isVisitDay("2026-09-27")).toBe(false);
  });

  it("nextVisit returns the day itself on a visit day", () => {
    expect(nextVisit("2026-09-22")).toBe("2026-09-22");
    expect(nextVisit("2026-09-25")).toBe("2026-09-25");
  });

  it("nextVisit walks forward from every other weekday", () => {
    expect(nextVisit("2026-09-21")).toBe("2026-09-22");
    expect(nextVisit("2026-09-23")).toBe("2026-09-25");
    expect(nextVisit("2026-09-24")).toBe("2026-09-25");
    expect(nextVisit("2026-09-26")).toBe("2026-09-29");
    expect(nextVisit("2026-09-27")).toBe("2026-09-29");
  });

  it("stepVisit moves between Tuesdays and Fridays", () => {
    expect(stepVisit("2026-09-22", 1)).toBe("2026-09-25");
    expect(stepVisit("2026-09-25", 1)).toBe("2026-09-29");
    expect(stepVisit("2026-09-25", -1)).toBe("2026-09-22");
    expect(stepVisit("2026-09-29", -1)).toBe("2026-09-25");
  });

  it("sessionOf names the prep session", () => {
    expect(sessionOf("2026-09-22")).toBe("tue");
    expect(sessionOf("2026-09-25")).toBe("fri");
  });
});

describe("formatters", () => {
  it("formats the visit header", () => {
    expect(fmtLong("2026-09-25")).toBe("Friday, Sep 25");
  });

  it("formats short dates", () => {
    expect(fmtShort("2026-09-25")).toBe("Fri, Sep 25");
  });

  it("formats bare days", () => {
    expect(fmtDay("2026-09-25")).toBe("Sep 25");
  });

  it("names the weekday for Home tiles", () => {
    expect(fmtWeekday("2026-09-25")).toBe("Friday");
  });

  it("formats an arbitrary option set", () => {
    expect(fmt("2026-09-21", { month: "long", day: "numeric" })).toBe("September 21");
  });

  it("formats photo timestamps with weekday, date and time", () => {
    const stamp = new Date(2026, 8, 25, 14, 5).toISOString();
    expect(fmtStamp(stamp)).toBe("Fri, Sep 25, 2:05 PM");
  });
});
