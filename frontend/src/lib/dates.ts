// Local-time date helpers. Every date in this app is a local YYYY-MM-DD string.
// Ported 1:1 from v1 static/app.js.

export const pad = (n: number): string => String(n).padStart(2, "0");

export const iso = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

export function parse(s: string): Date {
  const [y = 0, m = 1, d = 1] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function addDays(s: string, n: number): string {
  const d = parse(s);
  d.setDate(d.getDate() + n);
  return iso(d);
}

export const dow = (s: string): number => parse(s).getDay();

export const diffDays = (a: string, b: string): number =>
  Math.round((parse(b).getTime() - parse(a).getTime()) / 86400000);

export const today = (): string => iso(new Date());

export const mondayOf = (s: string): string => addDays(s, -((dow(s) + 6) % 7));

export const isVisitDay = (s: string): boolean => dow(s) === 2 || dow(s) === 5;

export function nextVisit(from: string): string {
  for (let i = 0; i < 7; i++) {
    const d = addDays(from, i);
    if (isVisitDay(d)) return d;
  }
  return from;
}

export function stepVisit(s: string, dir: 1 | -1): string {
  for (let i = 1; i < 8; i++) {
    const d = addDays(s, dir * i);
    if (isVisitDay(d)) return d;
  }
  return s;
}

export const sessionOf = (s: string): "tue" | "fri" => (dow(s) === 2 ? "tue" : "fri");

export const fmt = (s: string, opts: Intl.DateTimeFormatOptions): string =>
  parse(s).toLocaleDateString("en-US", opts);

export const fmtLong = (s: string): string => fmt(s, { weekday: "long", month: "short", day: "numeric" });

export const fmtShort = (s: string): string => fmt(s, { weekday: "short", month: "short", day: "numeric" });

export const fmtDay = (s: string): string => fmt(s, { month: "short", day: "numeric" });

export const fmtWeekday = (s: string): string => fmt(s, { weekday: "long" });

/** Photo/upload timestamps are full ISO datetimes, not YYYY-MM-DD. */
export const fmtStamp = (timestamp: string): string =>
  new Date(timestamp).toLocaleString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
