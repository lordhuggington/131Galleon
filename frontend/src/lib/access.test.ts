import { describe, expect, it } from "vitest";
import { tabsFor } from "./access";

describe("tabsFor", () => {
  it("gives owners every tab whatever their kind", () => {
    expect(tabsFor({ role: "owner", kind: "housekeeper" })).toEqual(["home", "visit", "jobs", "hours", "setup"]);
    expect(tabsFor({ role: "owner", kind: "builder" })).toEqual(["home", "visit", "jobs", "hours", "setup"]);
  });

  it("gives a housekeeper the visit and hours", () => {
    expect(tabsFor({ role: "staff", kind: "housekeeper" })).toEqual(["home", "visit", "hours"]);
  });

  it("gives a builder the jobs only", () => {
    expect(tabsFor({ role: "staff", kind: "builder" })).toEqual(["home", "jobs"]);
  });
});
