"use server";

import { redirect } from "next/navigation";
import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Sign in.
 *
 * This does its own `fetch` rather than going through `apiFetch`, and that is
 * load-bearing rather than an inconsistency. The API returns its tokens as
 * `Set-Cookie`, and a server-side `fetch` throws that header away — the cookie jar
 * belongs to the *browser*, not to Next's server. So the headers have to be read
 * here and written onto the Next response explicitly, or login appears to succeed
 * while nothing is actually stored and the next render bounces straight back to
 * /login.
 *
 * `redirect` is thrown by Next and must not be caught, which is why the try block
 * covers only the fetch.
 */

/** Whether the frontend itself is being served over HTTPS. */
function secureCookies(): boolean {
  // On localhost the app is served over plain http, and a `Secure` cookie is
  // dropped by the browser — which reproduces the exact "logged in but not" bug
  // this function exists to fix. Behind TLS this is true and the cookies harden.
  return process.env.FORENSIGHT_INSECURE_COOKIES !== "1";
}

export async function loginAction(
  _prev: { error: string } | null,
  formData: FormData,
): Promise<{ error: string } | null> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const from = String(formData.get("from") ?? "/");

  if (!email || !password) {
    return { error: "Enter both an email address and a password." };
  }

  let setCookies: string[];
  try {
    const response = await fetch(`${API_URL}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password }),
      cache: "no-store",
    });
    setCookies = response.headers.getSetCookie();
    if (!response.ok) {
      // 401 is deliberately vague from the backend (user enumeration), and kept
      // vague here. 429 needs its own message: "invalid credentials" would send
      // the user round a loop when the actual fix is to wait.
      if (response.status === 429) {
        return {
          error:
            "Too many attempts. Wait a minute before trying again — repeated " +
            "failures are rate limited.",
        };
      }
      if (response.status === 403) {
        return { error: "That account is disabled. Contact an administrator." };
      }
      return { error: "Sign-in failed. Check the email and password." };
    }
  } catch {
    return { error: "Cannot reach the server. Check that it is running." };
  }

  const jar = await cookies();
  const secure = secureCookies();
  for (const raw of setCookies) {
    // Only the name/value/max-age matter here; Path is fixed by `cookies().set`
    // and the attributes we care about are re-stated explicitly rather than
    // inherited, because a browser will not widen them for us.
    const [pair, ...attrs] = raw.split(";");
    const eq = pair.indexOf("=");
    if (eq < 1) continue;
    const name = pair.slice(0, eq).trim();
    const value = pair.slice(eq + 1).trim();

    let maxAge = 60 * 60 * 24 * 14;
    for (const attr of attrs) {
      const [k, v] = attr.split("=");
      if (k?.trim().toLowerCase() === "max-age" && v) {
        const parsed = Number(v);
        if (Number.isFinite(parsed)) maxAge = parsed;
      }
    }

    jar.set(name, value, {
      httpOnly: name !== "csrf_token", // the frontend has to echo this one back
      secure,
      sameSite: "lax",
      path: "/",
      maxAge,
    });
  }

  if (!jar.get("access_token")) {
    return {
      error: "Sign-in succeeded but no session was stored. Try again.",
    };
  }

  // Only ever redirect within this app. An attacker-controlled `from` would
  // otherwise turn the login page into an open redirect.
  redirect(from.startsWith("/") && !from.startsWith("//") ? from : "/");
}

/** Sign out, clearing the session cookies. */
export async function logoutAction() {
  const jar = await cookies();
  for (const name of ["access_token", "refresh_token", "csrf_token"]) {
    jar.delete(name);
  }
  redirect("/login");
}
