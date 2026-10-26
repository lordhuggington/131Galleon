// Builder jobs: counts, filtering, who wrote a comment, and relative times (spec §3, §6).
import type { Job, JobComment, JobStatus, Role } from "../api/types";

export type JobFilter = "all" | JobStatus;

export const STATUS_LABEL: Record<JobStatus, string> = {
  todo: "To do",
  doing: "In progress",
  done: "Done",
};

export const JOB_STATUSES: JobStatus[] = ["todo", "doing", "done"];

export interface JobCounts {
  all: number;
  todo: number;
  doing: number;
  done: number;
}

export function countJobs(jobs: Job[]): JobCounts {
  const counts: JobCounts = { all: jobs.length, todo: 0, doing: 0, done: 0 };
  for (const job of jobs) counts[job.status] += 1;
  return counts;
}

export interface NumberedJob {
  /** 1-based position in the full list, so a job keeps its number under any filter. */
  number: number;
  job: Job;
}

/** Jobs in sort order (sortOrder, then id), numbered by position, then filtered. */
export function numberJobs(jobs: Job[], filter: JobFilter): NumberedJob[] {
  const ordered = [...jobs].sort((a, b) => a.sortOrder - b.sortOrder || a.id - b.id);
  return ordered
    .map((job, index) => ({ number: index + 1, job }))
    .filter(({ job }) => filter === "all" || job.status === filter);
}

/** A link is only drawn as a link when it is http(s) — the server checks the same on save. */
export const isWebLink = (link: string): boolean => /^https?:\/\//i.test(link.trim());

export interface Viewer {
  id: number;
  role: Role;
}

/**
 * Did a homeowner write this comment? The wire `Person` is only id + name, so:
 * a `role` on the author wins if the server sends one; else the viewer's own comments are
 * their own role; else an owner viewer asks the people list (`roles`, owner-only) and a
 * builder viewer reads anyone else as a homeowner, since only owners and builders comment.
 */
export function isOwnerComment(
  comment: JobComment,
  viewer: Viewer,
  roles: ReadonlyMap<number, Role> | null,
): boolean {
  const author = comment.by;
  if (author.role) return author.role === "owner";
  if (author.id === viewer.id) return viewer.role === "owner";
  const known = roles?.get(author.id);
  if (known) return known === "owner";
  return viewer.role !== "owner";
}

/** "just now", "5 min ago", "3 h ago", "yesterday", "4 days ago", then "Sep 25". */
export function fmtAgo(timestamp: string, now: Date = new Date()): string {
  const then = new Date(timestamp);
  const seconds = (now.getTime() - then.getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(then)) / 86400000);
  if (days <= 1) return "yesterday";
  if (days < 7) return `${days} days ago`;
  const sameYear = then.getFullYear() === now.getFullYear();
  return then.toLocaleDateString("en-US", sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}
