import { useEffect, useRef, useState } from "react";
import { Card } from "../../components/Card";
import { useApp } from "../../state/AppState";

const SAVE_DELAY_MS = 900;

export function VisitNote({
  date,
  note,
  focusOnMount,
  isOwner,
}: {
  date: string;
  note: string;
  focusOnMount: boolean;
  isOwner: boolean;
}) {
  const { dispatch, mutate } = useApp();
  const [draft, setDraft] = useState(note);
  const [focused, setFocused] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const box = useRef<HTMLTextAreaElement | null>(null);
  // The saved value, read inside timers without re-arming them.
  const saved = useRef(note);
  saved.current = note;
  // Read by the unmount cleanup so a tab switch mid-typing saves instead of dropping the draft.
  const flush = useRef(() => {});
  flush.current = () => save(draft);

  // A poll must never overwrite what someone is typing (spec §7.2).
  useEffect(() => {
    if (!focused) setDraft(note);
  }, [note, focused]);

  // Fires on mount and whenever the Home "Leave a note" tile turns the flag on.
  useEffect(() => {
    if (focusOnMount) box.current?.focus();
  }, [focusOnMount]);

  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
      flush.current();
    },
    [],
  );

  function save(value: string) {
    window.clearTimeout(timer.current);
    if (value === saved.current) return;
    dispatch({ type: "visit-note", date, note: value });
    void mutate("PUT", `/api/visits/${date}/note`, { note: value }).catch(() => {});
  }

  return (
    <Card>
      <label className="field" htmlFor="visitNote">
        <span>
          Notes for {isOwner ? "you" : "Owen"}: anything running low, broken or needing a decision
        </span>
        <textarea
          id="visitNote"
          ref={box}
          maxLength={4000}
          placeholder="e.g. Out of dish soap. Spare room blind is sticking."
          value={draft}
          onFocus={() => setFocused(true)}
          onChange={(e) => {
            const value = e.target.value;
            setDraft(value);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => save(value), SAVE_DELAY_MS);
          }}
          onBlur={() => {
            setFocused(false);
            save(draft);
          }}
        />
      </label>
    </Card>
  );
}
