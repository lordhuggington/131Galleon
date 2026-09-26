import { describe, expect, it } from "vitest";
import { digitsOnly, prettyPhone } from "./phone";

describe("digitsOnly", () => {
  it("strips everything that is not a digit", () => {
    expect(digitsOnly("+1 (310) 555-1234")).toBe("13105551234");
    expect(digitsOnly("")).toBe("");
  });
});

describe("prettyPhone", () => {
  it("formats a US E.164 number", () => {
    expect(prettyPhone("+13105551234")).toBe("(310) 555-1234");
  });

  it("formats a bare 10-digit number", () => {
    expect(prettyPhone("3105551234")).toBe("(310) 555-1234");
  });

  it("returns non-US numbers unchanged", () => {
    expect(prettyPhone("+447700900123")).toBe("+447700900123");
  });

  it("returns an empty string for no number", () => {
    expect(prettyPhone(null)).toBe("");
    expect(prettyPhone(undefined)).toBe("");
    expect(prettyPhone("")).toBe("");
  });
});
