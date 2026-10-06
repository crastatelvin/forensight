"use client";

import Link from "next/link";
import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { createCaseAction } from "../actions";
import { Button, Notice, Card, CardBody } from "@/components/ui";

function Submit() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" variant="primary" disabled={pending}>
      {pending ? "Creating…" : "Create case"}
    </Button>
  );
}

export function NewCaseForm() {
  const [state, action] = useActionState(createCaseAction, null);

  return (
    <Card>
      <CardBody>
        <form action={action} className="space-y-4">
          <div className="grid gap-4 sm:grid-cols-[1fr_2fr]">
            <div>
              <label htmlFor="number" className="mb-1.5 block text-xs font-medium">
                Case number{" "}
                <span aria-hidden="true" className="text-alert">
                  *
                </span>
              </label>
              <input
                id="number"
                name="number"
                required
                maxLength={64}
                autoFocus
                placeholder="2026-CF-0142"
                className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 font-mono text-sm outline-none placeholder:font-sans placeholder:text-tertiary focus:border-accent"
              />
            </div>
            <div>
              <label htmlFor="title" className="mb-1.5 block text-xs font-medium">
                Title{" "}
                <span aria-hidden="true" className="text-alert">
                  *
                </span>
              </label>
              <input
                id="title"
                name="title"
                required
                maxLength={256}
                placeholder="Arsonside warehouse, Dock Road"
                className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 text-sm outline-none placeholder:text-tertiary focus:border-accent"
              />
            </div>
          </div>

          <div>
            <label htmlFor="description" className="mb-1.5 block text-xs font-medium">
              Description
            </label>
            <textarea
              id="description"
              name="description"
              rows={4}
              maxLength={4000}
              placeholder="What prompted the case, what is in scope, and anything that constrains how the evidence may be used."
              className="w-full rounded-md border border-border-default bg-surface-inset px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-tertiary focus:border-accent"
            />
          </div>

          <div className="max-w-48">
            <label htmlFor="priority" className="mb-1.5 block text-xs font-medium">
              Priority
            </label>
            <select
              id="priority"
              name="priority"
              className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 text-sm outline-none focus:border-accent"
            >
              {["low", "normal", "high", "critical"].map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          </div>

          <div role="alert" aria-live="assertive">
            {state?.error ? <Notice tone="alert">{state.error}</Notice> : null}
          </div>

          <div className="flex gap-2">
            <Submit />
            <Link
              href="/"
              className="inline-flex h-9 items-center rounded-md border border-border-default px-3.5 text-sm font-medium text-secondary hover:bg-surface-2"
            >
              Cancel
            </Link>
          </div>
        </form>
      </CardBody>
    </Card>
  );
}
