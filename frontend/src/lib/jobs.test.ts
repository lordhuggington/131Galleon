import { describe, expect, it } from "vitest";
import type { Job, JobComment, JobStatus, Role } from "../api/types";
import { countJobs, fmtAgo, isOwnerComment, isWebLink, numberJobs } from "./jobs";

function job(id: number, status: JobStatus, sortOrder = id): Job {
  return {
    id, title: `Job ${id}`, details: "", link: "", status, sortOrder,
    createdAt: "2026-10-26T09:00:00", updatedAt: "2026-10-26T09:00:00", doneAt: null, doneBy: null,
    comments: [], photos: [],
  };
}

function comment(authorId: number, role?: Role): JobComment {
  return {
    id: 1, body: "Hi", createdAt: "2026-10-26T09:00:00",
    by: role ? { id: authorId, displayName: "X", role } : { id: authorId, displayName: "X" },
  };
}

describe("countJobs", () => {
  it("counts each status", () => {
    expect(countJobs([job(1, "todo"), job(2, "doing"), job(3, "todo"), job(4, "done")])).toEqual({
      all: 4, todo: 2, doing: 1, done: 1,
    });
    expect(countJobs([])).toEqual({ all: 0, todo: 0, doing: 0, done: 0 });
  });
});

describe("numberJobs", () => {
  const jobs = [job(3, "done", 2), job(1, "todo", 0), job(2, "doing", 1), job(4, "todo", 1)];

  it("orders by sortOrder then id and numbers from 1", () => {
    expect(numberJobs(jobs, "all").map(({ number, job: j }) => [number, j.id])).toEqual([
      [1, 1], [2, 2], [3, 4], [4, 3],
    ]);
  });

  it("keeps each job's number when filtered", () => {
    expect(numberJobs(jobs, "todo").map(({ number, job: j }) => [number, j.id])).toEqual([[1, 1], [3, 4]]);
    expect(numberJobs(jobs, "done").map(({ number }) => number)).toEqual([4]);
  });
});

describe("isWebLink", () => {
  it("only accepts http and https", () => {
    expect(isWebLink("https://example.com/a")).toBe(true);
    expect(isWebLink("http://example.com")).toBe(true);
    expect(isWebLink("javascript:alert(1)")).toBe(false);
    expect(isWebLink("")).toBe(false);
  });
});

describe("isOwnerComment", () => {
  const owner = { id: 1, role: "owner" as const };
  const builder = { id: 9, role: "staff" as const };

  it("trusts a role the server sends", () => {
    expect(isOwnerComment(comment(5, "owner"), builder, null)).toBe(true);
    expect(isOwnerComment(comment(5, "staff"), owner, null)).toBe(false);
  });

  it("knows the viewer's own comments", () => {
    expect(isOwnerComment(comment(1), owner, null)).toBe(true);
    expect(isOwnerComment(comment(9), builder, null)).toBe(false);
  });

  it("asks the people list for an owner viewer", () => {
    const roles = new Map<number, Role>([[2, "owner"], [9, "staff"]]);
    expect(isOwnerComment(comment(2), owner, roles)).toBe(true);
    expect(isOwnerComment(comment(9), owner, roles)).toBe(false);
  });

  it("reads anyone else as a homeowner for a builder, and as the builder for an owner without the list", () => {
    expect(isOwnerComment(comment(1), builder, null)).toBe(true);
    expect(isOwnerComment(comment(9), owner, null)).toBe(false);
  });
});

describe("fmtAgo", () => {
  const now = new Date(2026, 9, 26, 15, 0);
  const at = (y: number, m: number, d: number, h = 12, min = 0) => new Date(y, m, d, h, min).toISOString();

  it("reads recent times in minutes and hours", () => {
    expect(fmtAgo(at(2026, 9, 26, 14, 59), now)).toBe("1 min ago");
    expect(fmtAgo(new Date(now.getTime() - 20000).toISOString(), now)).toBe("just now");
    expect(fmtAgo(at(2026, 9, 26, 14, 15), now)).toBe("45 min ago");
    expect(fmtAgo(at(2026, 9, 26, 9, 0), now)).toBe("6 h ago");
  });

  it("reads older times in days, then as a date", () => {
    expect(fmtAgo(at(2026, 9, 25, 8, 0), now)).toBe("yesterday");
    expect(fmtAgo(at(2026, 9, 22), now)).toBe("4 days ago");
    expect(fmtAgo(at(2026, 8, 25), now)).toBe("Sep 25");
    expect(fmtAgo(at(2025, 8, 25), now)).toBe("Sep 25, 2025");
  });

  it("is blank for a bad timestamp", () => {
    expect(fmtAgo("nope", now)).toBe("");
  });
});
