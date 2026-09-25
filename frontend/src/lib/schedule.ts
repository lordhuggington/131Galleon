// Which regular tasks are due on a given visit date. Ported 1:1 from v1 static/app.js.
import type { Day, Freq, Task, Visit } from "../api/types";
import { diffDays, dow } from "./dates";

export const AREA_ORDER: string[] = [
  "Meal prep",
  "Kitchen",
  "Bathrooms",
  "Bedrooms",
  "Living areas",
  "Laundry",
  "Outdoor",
  "Spare rooms",
  "Whole house",
];

export const FREQ_LABEL: Record<Freq, string> = {
  visit: "Every visit",
  weekly: "Weekly",
  fortnightly: "Every 2 weeks",
  monthly: "Monthly",
};

/** Minimum days since the last completion before a task is due again. */
export const FREQ_GAP: Partial<Record<Freq, number>> = {
  weekly: 5,
  fortnightly: 12,
  monthly: 26,
};

export const DAY_LABEL: Record<Day, string> = {
  any: "Tue or Fri",
  tue: "Tuesdays",
  fri: "Fridays",
};

/** taskId -> ascending list of dates it was ticked. */
export type DoneMap = Record<string, string[]>;

export function doneDatesByTask(visits: Record<string, Visit>): DoneMap {
  const map: DoneMap = {};
  for (const [date, visit] of Object.entries(visits)) {
    for (const id of Object.keys(visit?.done ?? {})) {
      const list = map[id];
      if (list) list.push(date);
      else map[id] = [date];
    }
  }
  for (const list of Object.values(map)) list.sort();
  return map;
}

export function lastDoneBefore(list: string[] | undefined, date: string): string | null {
  if (!list) return null;
  let last: string | null = null;
  for (const d of list) {
    if (d < date) last = d;
    else break;
  }
  return last;
}

export function isDue(
  t: Task,
  date: string,
  doneMap: DoneMap,
  visits: Record<string, Visit>,
): boolean {
  if (t.active === false) return false;
  const d = dow(date);
  if (t.day === "tue" && d !== 2) return false;
  if (t.day === "fri" && d !== 5) return false;
  const freq: Freq = t.freq || "visit";
  if (freq === "visit") return true;
  // Already ticked on this date: keep it on the list so it can be un-ticked.
  if (visits[date]?.done[t.id]) return true;
  if (freq === "weekly" && t.day && t.day !== "any") return true;
  const last = lastDoneBefore(doneMap[t.id], date);
  return !last || diffDays(last, date) >= (FREQ_GAP[freq] ?? 0);
}

export function areaRank(area: string): number {
  const i = AREA_ORDER.indexOf(area);
  return i < 0 ? 100 : i;
}

/** Returns a new array; never mutates the input. */
export function sortTasks(tasks: Task[]): Task[] {
  return [...tasks].sort(
    (a, b) =>
      areaRank(a.area) - areaRank(b.area) ||
      String(a.area).localeCompare(String(b.area)) ||
      (a.order || 0) - (b.order || 0),
  );
}

/** Groups in the order the tasks arrive, so pass sortTasks() output. */
export function groupByArea(tasks: Task[]): Array<[string, Task[]]> {
  const groups = new Map<string, Task[]>();
  for (const t of tasks) {
    const key = t.area || "Other";
    const list = groups.get(key);
    if (list) list.push(t);
    else groups.set(key, [t]);
  }
  return [...groups.entries()];
}
