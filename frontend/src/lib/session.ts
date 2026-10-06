import { cookies } from "next/headers";
import { apiGet, ApiError } from "./api";
import type { UserRead } from "./types";

/**
 * The signed-in user, or `null`.
 *
 * Returns `null` rather than throwing on 401 because "not signed in" is a normal
 * state that pages handle by redirecting — not an error. Any other failure is
 * allowed to propagate, because silently treating an API outage as "logged out"
 * would bounce a working user to the login page and destroy their session
 * narrative for no reason.
 */
export async function getSession(): Promise<UserRead | null> {
  const jar = await cookies();
  if (!jar.get("access_token")) return null;

  try {
    return await apiGet<UserRead>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

/**
 * The signed-in user, or a redirect to login.
 *
 * For pages that cannot render anything without a session. The `from` parameter
 * lets the login page send the user back where they were, which is the difference
 * between "log in again" and "log in again and lose your place".
 */
export async function requireSession(): Promise<UserRead> {
  const user = await getSession();
  if (user) return user;
  const { redirect } = await import("next/navigation");
  redirect("/login");
  // `redirect` is typed as `never`, but the narrowing is lost across the dynamic
  // import above, so TypeScript cannot see that this function always returns a
  // user or throws. Stated rather than cast: the alternative is a lie a reader
  // would have to check.
  throw new Error("unreachable: redirect() above does not return");
}

/** Roles that may perform a given mutation, mirroring the backend's `MUTATING_ROLES`. */
const WRITE_ROLES = new Set(["admin", "officer", "expert"]);

export function canWrite(role: string): boolean {
  return WRITE_ROLES.has(role);
}

/** Only an admin may create users or view the audit log. */
export function isAdmin(role: string): boolean {
  return role === "admin";
}

/**
 * Whether the signed-in user may act on a case.
 *
 * Mirrors the backend's visibility rule rather than inventing a UI-only one: the
 * backend already decides, and a frontend that disagreed with it would show
 * buttons that always 404.
 */
export async function canAccessCase(caseId: string): Promise<boolean> {
  const user = await getSession();
  if (!user) return false;
  if (user.role === "reviewer") return true; // reviewers see everything, read-only
  try {
    await apiGet(`/cases/${caseId}`);
    return true;
  } catch {
    return false;
  }
}
