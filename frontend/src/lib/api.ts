/**
 * Typed client for the ForenSight API.
 *
 * Every request goes through Next's server (server components / route handlers),
 * never straight from the browser to the API. That is deliberate, for three
 * reasons that are load-bearing rather than stylistic:
 *
 *  1. Auth is cookie-based and the access token is HttpOnly, so JavaScript cannot
 *     read it. Every browser-side `fetch` to the API would be unauthenticated.
 *  2. CORS and CSRF are two problems that do not exist if the browser only ever
 *     talks to its own origin.
 *  3. The API's error shape is not the API's business to leak to a browser; a
 *     single choke point keeps that in one file.
 *
 * The cost is that every page which needs fresh data is a server render, and any
 * live interaction goes through a route handler. That is the right trade for a
 * case-management tool where correctness and auditability beat SPA snappiness.
 */

import { cookies } from "next/headers";

/** Base URL of the backend, server-side only. Never exposed to the browser. */
export const API_URL = process.env.FORENSIGHT_API_URL ?? "http://localhost:8000";

/** Absolute URL for an API path. */
function apiUrl(path: string): string {
  return `${API_URL}${path}`;
}

/**
 * An API error carrying the backend's own status and detail.
 *
 * The backend returns `{detail: "..."}` for every failure. Preserving the status
 * matters: the UI shows 401 as "session expired", 403 as "you do not have access",
 * 409 as "that already exists", and everything else as a generic failure. Losing
 * the status would collapse four different user actions into one.
 */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly body: unknown = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Pull a readable message out of a FastAPI error body.
 *
 * FastAPI's `detail` is a string for `HTTPException` but a list of objects for
 * validation errors, and the list has `msg`/`loc`. Both shapes appear in normal
 * use — a bad upload and a bad case number are both ordinary mistakes — so both
 * are flattened to a sentence here rather than in each caller.
 */
function extractDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as Record<string, unknown>;
    const loc = Array.isArray(first.loc) ? first.loc.join(" → ") : "";
    const msg = typeof first.msg === "string" ? first.msg : "";
    return loc && msg ? `${loc}: ${msg}` : msg || fallback;
  }
  return fallback;
}

/** Cookie + method headers for a backend call. */
async function authHeaders(): Promise<Record<string, string>> {
  const jar = await cookies();
  // `jar.toString()` already emits the `name=value; name=value` form the Cookie
  // header wants. Rebuilding it by hand would risk dropping or double-encoding a
  // value, which produces a confusing 401 rather than an obvious bug.
  const cookieHeader = jar.toString();

  // CSRF is a double-submit check: the backend sets a readable `csrf_token` cookie
  // at login and demands the same value in the `x-csrf-token` header on every
  // unsafe method. Forwarding both from the server is what satisfies it.
  const csrf = jar.get("csrf_token")?.value;

  return {
    ...(cookieHeader ? { cookie: cookieHeader } : {}),
    ...(csrf ? { "x-csrf-token": csrf } : {}),
  };
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  /** Treat 404 as `null` instead of throwing. Used for optional fetches. */
  nullOn404?: boolean;
}

/**
 * One call to the backend.
 *
 * `nullOn404` exists so a page can ask for a resource that may not exist without
 * wrapping every call in a try/catch — a case with no agent runs yet is normal,
 * not an error, and rendering an error state for it would be a lie.
 */
export async function apiFetch<T>(
  path: string,
  { method = "GET", body, nullOn404 = false }: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {
    accept: "application/json",
    ...(await authHeaders()),
  };
  if (body !== undefined) headers["content-type"] = "application/json";

  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (cause) {
    // A refused connection to the API is not an API error, and reporting it as a
    // 500 would send the user looking for a bug in their input.
    throw new ApiError(
      503,
      "Cannot reach the ForenSight API. Check that it is running.",
      cause,
    );
  }

  if (response.status === 404 && nullOn404) return null as T;

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      // A non-JSON body from an error response (an HTML 502 page from a proxy)
      // still has to reach the user as something readable.
      parsed = { detail: text.slice(0, 200) };
    }
  }

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractDetail(parsed, `Request failed (${response.status})`),
      parsed,
    );
  }
  return parsed as T;
}

/** GET. */
export function apiGet<T>(path: string, options?: { nullOn404?: boolean }): Promise<T> {
  return apiFetch<T>(path, options);
}

/** POST with a JSON body. */
export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "POST", body: body ?? {} });
}

/** PATCH with a JSON body. */
export function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return apiFetch<T>(path, { method: "PATCH", body });
}

/** DELETE. */
export function apiDelete<T>(path: string): Promise<T> {
  return apiFetch<T>(path, { method: "DELETE" });
}

/**
 * Forward a multipart upload to the backend.
 *
 * `content-type` is deliberately not set: the browser has to add the multipart
 * boundary itself, and setting the header by hand produces a body the backend
 * cannot parse. That is the whole reason this function exists separately from
 * `apiFetch` rather than being a flag on it.
 */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method: "POST",
      headers: { accept: "application/json", ...(await authHeaders()) },
      body: form,
      cache: "no-store",
    });
  } catch (cause) {
    throw new ApiError(503, "Cannot reach the ForenSight API.", cause);
  }

  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { detail: text.slice(0, 200) };
    }
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractDetail(parsed, `Upload failed (${response.status})`),
      parsed,
    );
  }
  return parsed as T;
}
