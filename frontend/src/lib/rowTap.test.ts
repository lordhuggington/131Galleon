import { describe, expect, it, vi } from "vitest";
import { rowTap } from "./rowTap";

// These tests run in node (no DOM), and the handler only ever reads `target.closest`,
// so a stub with that one method stands in for the clicked element.
const target = (closest: (selector: string) => unknown) => ({ closest }) as unknown as EventTarget;

describe("rowTap", () => {
  it("toggles when the tap missed every control", () => {
    const toggle = vi.fn();
    rowTap(toggle)({ target: target(() => null) });
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("leaves a tap on a control alone", () => {
    const toggle = vi.fn();
    rowTap(toggle)({ target: target(() => ({})) });
    expect(toggle).not.toHaveBeenCalled();
  });

  it("guards the check button itself", () => {
    const seen: string[] = [];
    rowTap(() => {})({
      target: target((selector) => {
        seen.push(selector);
        return null;
      }),
    });
    expect(seen).toHaveLength(1);
    expect(seen[0]).toContain("button");
  });

  it("toggles when there is no target to guard", () => {
    const toggle = vi.fn();
    rowTap(toggle)({ target: null });
    expect(toggle).toHaveBeenCalledTimes(1);
  });

  it("toggles when the target cannot answer closest", () => {
    const toggle = vi.fn();
    rowTap(toggle)({ target: {} as unknown as EventTarget });
    expect(toggle).toHaveBeenCalledTimes(1);
  });
});
