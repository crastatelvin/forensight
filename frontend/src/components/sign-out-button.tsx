"use client";

import { logoutAction } from "@/app/(auth)/login/actions";

/**
 * Sign out.
 *
 * A `<form action>` rather than an `onClick` fetch: sign-out must work without
 * JavaScript and must be a real navigation, since the session lives in an HttpOnly
 * cookie the client cannot clear itself.
 */
export function SignOutButton() {
  return (
    <form action={logoutAction}>
      <button
        type="submit"
        className="rounded-md border border-border-default px-2.5 py-1.5 text-xs font-medium text-secondary transition-colors hover:bg-surface-2 hover:text-primary"
      >
        Sign out
      </button>
    </form>
  );
}
