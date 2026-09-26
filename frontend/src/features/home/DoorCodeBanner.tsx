import { useState } from "react";

const STORAGE_KEY = "hrs.hideCode";

function readHidden(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // Private mode or blocked storage: show the code.
    return false;
  }
}

export function DoorCodeBanner({ code }: { code: string }) {
  const [hidden, setHidden] = useState<boolean>(readHidden);

  function toggle() {
    const next = !hidden;
    setHidden(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    } catch {
      // Nothing to remember it with; the choice lasts for this view only.
    }
  }

  return (
    <button
      type="button"
      className="door-code"
      aria-pressed={hidden}
      onClick={toggle}
    >
      <span>
        <span aria-hidden="true">🔑</span> Door code
        <span className="sr-only">{hidden ? " (hidden — press to show)" : " (press to hide)"}</span>
      </span>
      <span className="mono door-code-value">{hidden ? "••••" : code}</span>
    </button>
  );
}
