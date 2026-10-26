// Response shapes the app consumes. Field names must match app/api.py and app/store.py exactly.

export type Role = "owner" | "staff";
export type Kind = "housekeeper" | "builder";

export interface Me {
  id: number;
  username: string;
  displayName: string;
  role: Role;
  label: string;
  phone: string | null;
  doorCode: string | null;
  /** Only meaningful for staff: which run sheet they get. Owners see everything. */
  kind: Kind;
}

/** Owner-only /api/users rows: everything in Me plus admin flags. */
export interface User extends Me {
  active: boolean;
  hasPassword: boolean;
}

export type Freq = "visit" | "weekly" | "fortnightly" | "monthly";

export interface Task {
  id: string;
  title: string;
  area: string;
  freq: Freq;
  notes: string;
  order: number;
  active: boolean;
}

export interface Extra {
  title: string;
  notes: string;
  done: boolean;
  createdAt: string;
}

export type PhotoKind = "done" | "fix";

export interface Photo {
  id: number;
  kind: PhotoKind;
  caption: string;
  /** Server-built path, e.g. "/photos/ab12….jpg". */
  url: string;
  createdAt: string;
  by: Person;
}

/**
 * Everything recorded on one date. An extra sits under the date it was done on, or, while open,
 * under the date it was added on (open ones are sent however old); see lib/visits.
 */
export interface Visit {
  date: string;
  note: string;
  /** taskId -> ISO timestamp it was ticked. */
  done: Record<string, string>;
  /** extraId (stringified row id) -> extra. */
  extras: Record<string, Extra>;
  photos: Photo[];
}

/** A name attached to a comment, photo, claim or status change. */
export interface Person {
  id: number;
  displayName: string;
  /** Not in the spec's wire shape; read if the server ever sends it (see lib/jobs isOwnerComment). */
  role?: Role;
}

export type JobStatus = "todo" | "doing" | "done";

export interface JobComment {
  id: number;
  body: string;
  createdAt: string;
  by: Person;
}

export interface JobPhoto {
  id: number;
  caption: string;
  /** Server-built path, e.g. "/photos/ab12….jpg". */
  url: string;
  createdAt: string;
  by: Person;
}

export interface Job {
  id: number;
  title: string;
  details: string;
  link: string;
  status: JobStatus;
  sortOrder: number;
  createdAt: string;
  updatedAt: string;
  doneAt: string | null;
  doneBy: Person | null;
  /** Oldest first. */
  comments: JobComment[];
  /** Oldest first. */
  photos: JobPhoto[];
}

export type ClaimStatus = "submitted" | "approved" | "paid";

export interface HoursClaim {
  id: number;
  /** YYYY-MM-DD, the day worked. */
  date: string;
  minutes: number;
  note: string;
  status: ClaimStatus;
  createdAt: string;
  by: Person;
  approvedAt: string | null;
  approvedBy: Person | null;
  paidAt: string | null;
  paidBy: Person | null;
}

export interface MeResponse { me: Me }
export interface StateResponse {
  me: Me;
  /** Read defensively: a builder has no run sheet, so the server may leave these out. */
  tasks?: Task[];
  visits?: Record<string, Visit>;
  /** Owners and builders only. */
  jobs?: Job[];
  /** Owners (everyone's) and housekeepers (their own) only. */
  hours?: HoursClaim[];
}
export interface UsersResponse { users: User[] }
export interface UserResponse { user: User }
export interface TaskResponse { task: Task }
export interface JobsResponse { jobs: Job[] }
export interface JobResponse { job: Job }
export interface JobCommentResponse { comment: JobComment }
export interface JobPhotoResponse { photo: JobPhoto }
export interface HoursResponse { claims: HoursClaim[] }
export interface ClaimResponse { claim: HoursClaim }
export interface LoginOptions { sms: boolean }
export interface PhotoResponse { photo: Photo }
export interface OkResponse { ok: true }
/** POST /api/visits/{date}/extras returns the new row id as a string. */
export interface ExtraCreated { id: string }
