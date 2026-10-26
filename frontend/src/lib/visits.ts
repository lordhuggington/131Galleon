// The run sheet is not tied to visit days: the open sheet is always today's, and history is the
// dates that have something recorded on them.
import type { Extra, Task, Visit } from "../api/types";
import { doneDatesByTask, isDue } from "./schedule";

/** Something was done or written on this date: a tick, an extra done, a note or a photo. */
export function hasActivity(visit: Visit | undefined): boolean {
  if (!visit) return false;
  return (
    Object.keys(visit.done).length > 0 ||
    Object.values(visit.extras).some((x) => x.done) ||
    visit.note.trim() !== "" ||
    visit.photos.length > 0
  );
}

/** Today's sheet is "Next visit" until something is recorded on it, then "Today's visit". */
export const todayLabel = (visits: Record<string, Visit>, today: string): string =>
  hasActivity(visits[today]) ? "Today's visit" : "Next visit";

/** Dates the ‹ › arrows step through: every past date with activity, then today. Ascending, no future. */
export function visitDates(visits: Record<string, Visit>, today: string): string[] {
  const past = Object.keys(visits).filter((d) => d < today && hasActivity(visits[d]));
  return [...past.sort(), today];
}

/** The neighbouring date in `dates` (ascending) from `current`, or null at either end. */
export function stepThrough(dates: string[], current: string, dir: 1 | -1): string | null {
  if (dir === 1) return dates.find((d) => d > current) ?? null;
  for (let i = dates.length - 1; i >= 0; i--) {
    const d = dates[i]!;
    if (d < current) return d;
  }
  return null;
}

export interface ExtraEntry {
  id: string;
  extra: Extra;
  /** The visits key it is stored under (where it was done, or where it was added while open). */
  storedOn: string;
}

/**
 * One-off jobs on the run sheet for `date`. Today shows every open extra, whenever it was added,
 * plus the ones done today; a past date shows the ones done that day. Oldest first.
 */
export function extrasOn(visits: Record<string, Visit>, date: string, today: string): ExtraEntry[] {
  const out: ExtraEntry[] = [];
  for (const [storedOn, visit] of Object.entries(visits)) {
    for (const [id, extra] of Object.entries(visit.extras)) {
      if (extra.done ? storedOn === date : date === today) out.push({ id, extra, storedOn });
    }
  }
  return out.sort((a, b) => a.extra.createdAt.localeCompare(b.extra.createdAt) || Number(a.id) - Number(b.id));
}

/** Every extra that hasn't been ticked yet, wherever it is stored. */
export const openExtras = (visits: Record<string, Visit>): ExtraEntry[] =>
  Object.entries(visits).flatMap(([storedOn, visit]) =>
    Object.entries(visit.extras)
      .filter(([, extra]) => !extra.done)
      .map(([id, extra]) => ({ id, extra, storedOn })),
  );

function emptyVisit(date: string): Visit {
  return { date, note: "", done: {}, extras: {}, photos: [] };
}

/**
 * The optimistic version of PATCH /api/visits/{date}/extras/{id}: ticking files the extra under
 * `date` (the day it was done); unticking leaves it where it is. Returns a new map.
 */
export function toggleExtra(
  visits: Record<string, Visit>,
  storedOn: string,
  date: string,
  id: string,
  done: boolean,
): Record<string, Visit> {
  const from = visits[storedOn];
  const existing = from?.extras[id];
  if (!from || !existing) return visits;
  const updated: Extra = { ...existing, done };
  if (!done || storedOn === date) {
    return { ...visits, [storedOn]: { ...from, extras: { ...from.extras, [id]: updated } } };
  }
  const rest = { ...from.extras };
  delete rest[id];
  const to = visits[date] ?? emptyVisit(date);
  return {
    ...visits,
    [storedOn]: { ...from, extras: rest },
    [date]: { ...to, extras: { ...to.extras, [id]: updated } },
  };
}

/** Home's "n tasks ready": tasks due today not yet ticked, plus every open extra. */
export function readyCount(tasks: Task[], visits: Record<string, Visit>, today: string): number {
  const doneMap = doneDatesByTask(visits);
  const ticked = visits[today]?.done ?? {};
  const undone = tasks.filter((t) => isDue(t, today, doneMap, visits) && !ticked[t.id]).length;
  return undone + openExtras(visits).length;
}
