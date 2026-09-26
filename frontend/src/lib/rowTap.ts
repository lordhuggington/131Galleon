const CONTROLS = "button, a, input, textarea, select, label";

// Makes a whole row a tap target for its check without stealing taps from the real controls
// inside it: the check itself (whose click would otherwise bubble up and toggle straight back),
// "Remove", "Open this visit's recipes". Pointer-only sugar — the Check button stays the
// accessible control, so the row gets no role and no key handling.
export function rowTap(toggle: () => void) {
  return (e: { target: EventTarget | null }) => {
    const el = e.target as { closest?: (selector: string) => unknown } | null;
    if (el?.closest?.(CONTROLS)) return;
    toggle();
  };
}
