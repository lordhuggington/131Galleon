// Hours claims: minutes <-> hours text, weekly grouping and totals (spec §4, §6).
import type { ClaimStatus, HoursClaim, Person } from "../api/types";
import { mondayOf } from "./dates";

export const MIN_MINUTES = 15;
export const MAX_MINUTES = 960;
/** Same wording as the server's 400, so the inline error and a server refusal read alike. */
export const HOURS_RULE = "Hours go in quarter hours, from 0.25 to 16.";

/** 90 -> "1.5", 135 -> "2.25", 120 -> "2", 0 -> "0". */
export function hoursText(minutes: number): string {
  const hours = Math.round((minutes / 60) * 100) / 100;
  return String(hours);
}

/** 90 -> "1.5 h". */
export const hoursLabel = (minutes: number): string => `${hoursText(minutes)} h`;

/**
 * "1.5" -> 90. Anything that isn't a whole number of quarter hours from 0.25 to 16 is null.
 * Accepts a comma as the decimal mark, since phones in some locales type one.
 */
export function hoursToMinutes(input: string | number): number | null {
  const text = String(input).trim().replace(",", ".");
  if (!/^\d*\.?\d+$/.test(text)) return null;
  const quarters = Number(text) * 4;
  if (!Number.isFinite(quarters)) return null;
  const whole = Math.round(quarters);
  if (Math.abs(quarters - whole) > 1e-9) return null;
  const minutes = whole * 15;
  if (minutes < MIN_MINUTES || minutes > MAX_MINUTES) return null;
  return minutes;
}

export const sumMinutes = (claims: HoursClaim[]): number => claims.reduce((n, c) => n + c.minutes, 0);

export function minutesWith(claims: HoursClaim[], status: ClaimStatus): number {
  return sumMinutes(claims.filter((c) => c.status === status));
}

/** Minutes claimed on days in the week that starts on `week` (a Monday). */
export function minutesInWeek(claims: HoursClaim[], week: string): number {
  return sumMinutes(claims.filter((c) => mondayOf(c.date) === week));
}

export interface PersonTotal {
  person: Person;
  minutes: number;
}

export interface WeekGroup {
  /** Monday, YYYY-MM-DD. */
  week: string;
  /** Newest day first; same day newest claim first. */
  claims: HoursClaim[];
  minutes: number;
  /** One row per person who claimed that week, by name. */
  people: PersonTotal[];
  /** Ids of the week's claims still waiting for approval. */
  submittedIds: number[];
}

function newestFirst(a: HoursClaim, b: HoursClaim): number {
  if (a.date !== b.date) return a.date < b.date ? 1 : -1;
  return b.id - a.id;
}

/** Claims grouped by the Monday of their week, newest week first. */
export function groupByWeek(claims: HoursClaim[]): WeekGroup[] {
  const byWeek = new Map<string, HoursClaim[]>();
  for (const claim of claims) {
    const week = mondayOf(claim.date);
    const list = byWeek.get(week);
    if (list) list.push(claim);
    else byWeek.set(week, [claim]);
  }
  const weeks = [...byWeek.keys()].sort().reverse();
  return weeks.map((week) => {
    const list = [...(byWeek.get(week) ?? [])].sort(newestFirst);
    const people = new Map<number, PersonTotal>();
    for (const claim of list) {
      const row = people.get(claim.by.id);
      if (row) row.minutes += claim.minutes;
      else people.set(claim.by.id, { person: claim.by, minutes: claim.minutes });
    }
    return {
      week,
      claims: list,
      minutes: sumMinutes(list),
      people: [...people.values()].sort((a, b) => a.person.displayName.localeCompare(b.person.displayName)),
      submittedIds: list.filter((c) => c.status === "submitted").map((c) => c.id),
    };
  });
}

export interface HoursSummary {
  toApprove: number;
  toApproveCount: number;
  approvedUnpaid: number;
}

export function summarise(claims: HoursClaim[]): HoursSummary {
  return {
    toApprove: minutesWith(claims, "submitted"),
    toApproveCount: claims.filter((c) => c.status === "submitted").length,
    approvedUnpaid: minutesWith(claims, "approved"),
  };
}

export const STATUS_LABEL: Record<ClaimStatus, string> = {
  submitted: "Submitted",
  approved: "Approved",
  paid: "Paid",
};

export interface StatusMove {
  to: ClaimStatus;
  label: string;
}

/** The owner's buttons for a claim: forward first, then the undo (spec §4). */
export function ownerMoves(status: ClaimStatus): StatusMove[] {
  switch (status) {
    case "submitted":
      return [{ to: "approved", label: "Approve" }];
    case "approved":
      return [
        { to: "paid", label: "Mark paid" },
        { to: "submitted", label: "Undo approval" },
      ];
    case "paid":
      return [{ to: "approved", label: "Undo paid" }];
  }
}
