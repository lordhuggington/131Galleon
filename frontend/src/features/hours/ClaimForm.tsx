import { useActionState, useState } from "react";
import type { HoursClaim } from "../../api/types";
import { Button } from "../../components/Button";
import { Field } from "../../components/Field";
import { today } from "../../lib/dates";
import { HOURS_RULE, hoursText, hoursToMinutes } from "../../lib/hours";
import { useApp } from "../../state/AppState";

const FUTURE = "You can't claim hours for a future day.";

/** The housekeeper's claim: a new one (no `claim`) or an edit while it's still submitted. */
export function ClaimForm({ claim, onDone }: { claim?: HoursClaim; onDone?: () => void }) {
  const { mutate } = useApp();
  const prefix = claim ? `claim${claim.id}` : "newClaim";
  const [date, setDate] = useState(claim?.date ?? today());
  const [hours, setHours] = useState(claim ? hoursText(claim.minutes) : "");
  const [note, setNote] = useState(claim?.note ?? "");
  const max = today();

  const [error, save, saving] = useActionState<string | null, FormData>(async () => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return "Pick the day you worked.";
    // YYYY-MM-DD compares as text.
    if (date > today()) return FUTURE;
    const minutes = hoursToMinutes(hours);
    if (minutes === null) return HOURS_RULE;
    const body = { date, minutes, note: note.trim() };
    try {
      // quiet: the failure is shown inline under the form.
      if (claim) await mutate("PATCH", `/api/hours/${claim.id}`, body, "Claim saved", { quiet: true });
      else await mutate("POST", "/api/hours", body, "Hours claimed", { quiet: true });
      if (!claim) {
        // Keep the date: several claims in a row are usually for neighbouring days.
        setHours("");
        setNote("");
      }
      onDone?.();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't save that claim.";
    }
  }, null);

  return (
    <form className={claim ? "claim-form edit" : "claim-form"} action={save}>
      <Field label="Day worked" htmlFor={`${prefix}Date`}>
        <input
          id={`${prefix}Date`}
          type="date"
          max={max}
          value={date}
          onChange={(e) => setDate(e.target.value)}
          required
        />
      </Field>
      <Field label="Hours" htmlFor={`${prefix}Hours`} hint="Quarter hours, e.g. 3.5">
        <input
          id={`${prefix}Hours`}
          type="number"
          inputMode="decimal"
          min={0.25}
          max={16}
          step={0.25}
          value={hours}
          onChange={(e) => setHours(e.target.value)}
          placeholder="4"
          required
        />
      </Field>
      <Field label="Note (optional)" htmlFor={`${prefix}Note`}>
        <input
          id={`${prefix}Note`}
          type="text"
          maxLength={500}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Stayed late for the oven clean"
        />
      </Field>
      <div className="row span-all">
        <Button type="submit" variant="primary" disabled={saving}>
          {claim ? "Save claim" : "Claim hours"}
        </Button>
        {onDone ? (
          <Button variant="ghost" disabled={saving} onClick={onDone}>
            Cancel
          </Button>
        ) : null}
      </div>
      {error ? (
        <div className="err span-all" role="alert">
          {error}
        </div>
      ) : null}
    </form>
  );
}
