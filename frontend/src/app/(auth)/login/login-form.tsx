"use client";

import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { loginAction } from "./actions";
import { Button, Notice } from "@/components/ui";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant="primary" className="w-full" disabled={pending}>
      {pending ? "Signing in…" : "Sign in"}
    </Button>
  );
}

/**
 * The sign-in form.
 *
 * `useActionState` rather than a hand-rolled `useState` for the error: it keeps
 * the pending state and the result in one place, and survives a re-render without
 * a flash of the old error. The form is a real `<form>` with a real submit button
 * so Enter works and the whole thing is keyboard-operable with no extra handlers.
 */
export function LoginForm({ from }: { from: string }) {
  const [state, formAction] = useActionState(loginAction, null);

  return (
    <form action={formAction} className="space-y-4">
      <input type="hidden" name="from" value={from} />

      <div>
        <label htmlFor="email" className="mb-1.5 block text-xs font-medium">
          Email address
        </label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="username"
          required
          autoFocus
          className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 text-sm outline-none placeholder:text-tertiary focus:border-accent"
          placeholder="you@agency.gov"
        />
      </div>

      <div>
        <label htmlFor="password" className="mb-1.5 block text-xs font-medium">
          Password
        </label>
        <input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 text-sm outline-none placeholder:text-tertiary focus:border-accent"
        />
      </div>

      {/* `role="alert"` so a screen reader announces the failure immediately
          rather than leaving the user on a form that silently did nothing. */}
      <div role="alert" aria-live="assertive">
        {state?.error ? <Notice tone="alert">{state.error}</Notice> : null}
      </div>

      <SubmitButton />
    </form>
  );
}
