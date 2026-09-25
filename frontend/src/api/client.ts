import type { Photo, PhotoKind, PhotoResponse } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

/** A 401 from these paths is a bad sign-in attempt, not an expired session. */
const LOGIN_PATHS = new Set([
  "/api/login",
  "/api/login/options",
  "/api/login/sms/start",
  "/api/login/sms/check",
]);

let onUnauthorized: () => void = () => {};

/** AppState registers a handler that signs the user out locally (spec §7.2). */
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

function messageFor(data: unknown, status: number): string {
  if (data && typeof data === "object" && "error" in data) {
    const { error } = data as { error: unknown };
    if (typeof error === "string" && error) return error;
  }
  return `Request failed (${status}).`;
}

async function send(method: Method, path: string, init: RequestInit): Promise<unknown> {
  let res: Response;
  try {
    res = await fetch(path, { credentials: "same-origin", method, ...init });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    // No body, or not JSON. Status still decides.
  }
  if (res.status === 401 && !LOGIN_PATHS.has(path)) {
    onUnauthorized();
    throw new ApiError(401, "Please sign in again.", data);
  }
  if (!res.ok) throw new ApiError(res.status, messageFor(data, res.status), data);
  return data;
}

/** JSON request. X-HRS: 1 is the CSRF guard the server requires on every non-GET. */
export async function api<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-HRS": "1" };
  const init: RequestInit = { headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  return (await send(method, path, init)) as T;
}

/**
 * Raw-JPEG upload (spec §8.2): the body is the resized image itself, so the
 * server needs no multipart parser. kind and caption travel in the query string.
 */
export async function uploadPhoto(
  date: string,
  blob: Blob,
  kind: PhotoKind,
  caption: string,
): Promise<Photo> {
  const query = new URLSearchParams({ kind, caption }).toString();
  const data = await send("POST", `/api/visits/${date}/photos?${query}`, {
    headers: { "X-HRS": "1", "Content-Type": "image/jpeg" },
    body: blob,
  });
  return (data as PhotoResponse).photo;
}
