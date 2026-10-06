"use client";

import { useState } from "react";
import { Button, Notice } from "@/components/ui";

/**
 * Chain-of-custody verification.
 *
 * A button rather than a silent check, because a verification that passes
 * unremarked is indistinguishable from one that never ran. Someone has to be able
 * to say "yes, the chain verified, at this time" — and in a court context that
 * means it happened in front of them.
 */
export function ChainStatus({
  caseId,
  status,
  onCheck,
}: {
  caseId: string;
  status: unknown;
  onCheck: (value: unknown) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function verify() {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/audit/verify", { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) {
        setError(body.detail ?? `Verification failed (${response.status})`);
        onCheck(null);
        return;
      }
      onCheck(body);
    } catch {
      setError("Could not reach the server to verify the chain.");
      onCheck(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" onClick={verify} disabled={busy}>
          {busy ? "Verifying…" : "Verify audit chain"}
        </Button>

        {status ? <ChainVerdict status={status} /> : null}

        {status ? (
          <span className="text-[11px] text-tertiary">
            Verified just now, in this browser.
          </span>
        ) : (
          <span className="text-[11px] text-tertiary">
            Not verified in this session.
          </span>
        )}
      </div>

      {error ? (
        <div role="alert">
          <Notice tone="alert">{error}</Notice>
        </div>
      ) : null}
      {/* `caseId` is read for the future per-case scoping; the API verifies the
          whole chain, so it is deliberately unused here rather than threaded
          through to a call that ignores it. */}
      <span className="sr-only">Case {caseId}</span>
    </div>
  );
}

function ChainVerdict({ status }: { status: unknown }) {
  const body = status as { valid?: boolean; checked?: number; broken_at?: string };
  if (body.valid === true) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-calm-border bg-calm-bg px-2 py-0.5 text-[11px] font-medium text-calm">
        ✓ Chain intact{body.checked ? ` — ${body.checked} entries` : ""}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-alert-border bg-alert-bg px-2 py-0.5 text-[11px] font-medium text-alert">
      ! Chain broken
      {body.broken_at ? ` at ${body.broken_at}` : ""}
    </span>
  );
}
