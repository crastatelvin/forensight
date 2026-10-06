"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { CaseRead } from "@/lib/types";
import { transitionCaseAction } from "@/app/cases/actions";
import { Button, Notice } from "@/components/ui";

/**
 * Case status transitions.
 *
 * The options come from `case.allowed_transitions`, which the API fills in from
 * the one state machine. Rendering the full status list here and letting the API
 * reject illegal moves would be simpler code and a worse tool: an operator who
 * clicks "closed" on a case in `intake` gets a 409 and no idea the workflow
 * forbids it.
 *
 * When there are no legal moves this renders an explicit terminal note rather than
 * nothing — "archived, no further moves" is information, and an empty space is
 * indistinguishable from a failed load.
 */
export function CaseWorkflow({ case_ }: { case_: CaseRead }) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  const moves = case_.allowed_transitions ?? [];

  if (moves.length === 0) {
    return (
      <p className="rounded-md border border-border-subtle bg-surface-2 px-3 py-2 text-xs text-secondary">
        Case is{" "}
        <span className="font-medium text-primary">
          {case_.status.replace(/_/g, " ")}
        </span>
        . No further status changes are permitted from here.
      </p>
    );
  }

  function run(target: string) {
    setError(null);
    startTransition(async () => {
      const result = await transitionCaseAction(case_.id, target);
      if (!result.ok) {
        setError(result.error ?? "The transition was refused.");
        setConfirming(null);
        return;
      }
      setConfirming(null);
      router.refresh();
    });
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium uppercase tracking-wide text-tertiary">
          Move to
        </span>
        {moves.map((target) => (
          <Button
            key={target}
            size="sm"
            variant={target === "archived" ? "secondary" : "primary"}
            disabled={pending}
            onClick={() => {
              // `archived` is one-way in practice, so it gets a confirm. The
              // forward moves do not: making every status change a dialog trains
              // people to click through dialogs, including the ones that matter.
              if (target === "archived") setConfirming(target);
              else run(target);
            }}
          >
            {target.replace(/_/g, " ")}
          </Button>
        ))}
      </div>

      {confirming ? (
        <Notice tone="warn" title="Archive this case?" className="max-w-sm">
          <p>
            Archiving is effectively final — no further status changes are permitted
            afterwards. Evidence and results are kept.
          </p>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant="danger"
              disabled={pending}
              onClick={() => run(confirming)}
            >
              {pending ? "Archiving…" : "Yes, archive"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </Notice>
      ) : null}

      {error ? (
        <div role="alert">
          <Notice tone="alert">{error}</Notice>
        </div>
      ) : null}
    </div>
  );
}
