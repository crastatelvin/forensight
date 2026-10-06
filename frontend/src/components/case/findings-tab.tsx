"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import type { FindingRead, AnalysisResultRead } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { createFindingAction } from "@/app/cases/actions";
import {
  Button,
  Card,
  CardHeader,
  CardBody,
  EmptyState,
  Notice,
  Badge,
} from "@/components/ui";

/**
 * Findings.
 *
 * A finding is a human's assertion, so the UI never lets one be created silently
 * attached to nothing: it asks what it is based on. Every result is offered as a
 * citation, and creating a finding with no basis is possible but has to be
 * deliberate, because a finding with no source is the thing a forensic report is
 * not allowed to contain.
 */
export function FindingsTab({
  caseId,
  findings,
  results,
  canWrite,
}: {
  caseId: string;
  findings: FindingRead[] | null;
  results: AnalysisResultRead[] | null;
  canWrite: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [cited, setCited] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  if (findings === null) {
    return (
      <Notice tone="alert" title="Could not load findings">
        This list is unavailable. Do not read that as &ldquo;this case has no
        findings&rdquo;.
      </Notice>
    );
  }

  function create() {
    setError(null);
    startTransition(async () => {
      // The citations go into the body as an explicit provenance block rather than
      // a hidden field, because the record has to be self-describing: a reader of
      // the finding — or of the exported report — must see what it rests on
      // without having to query the UI.
      const citedResults = (results ?? []).filter((r) => cited.includes(r.id));
      const provenance = citedResults.length
        ? `\n\n---\nBased on:\n${citedResults
            .map(
              (r) =>
                `- ${r.module} = ${r.score.toFixed(4)} (threshold ${r.threshold}, ${r.model_version}) on evidence ${r.evidence_id}`,
            )
            .join("\n")}`
        : "";

      const result = await createFindingAction(caseId, {
        title,
        body: `${body}${provenance}`,
      });
      if (!result.ok) {
        setError(result.error ?? "The finding could not be saved.");
        return;
      }
      setTitle("");
      setBody("");
      setCited([]);
      router.refresh();
    });
  }

  return (
    <div className="space-y-5">
      {canWrite ? (
        <Card>
          <CardHeader
            title="Record a finding"
            subtitle="State what you conclude and what it rests on."
          />
          <CardBody className="space-y-3">
            <div>
              <label
                htmlFor="finding-title"
                className="mb-1.5 block text-xs font-medium"
              >
                Title
              </label>
              <input
                id="finding-title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                maxLength={512}
                placeholder="Image region shows inconsistent compression history"
                className="h-10 w-full rounded-md border border-border-default bg-surface-inset px-3 text-sm outline-none placeholder:text-tertiary focus:border-accent"
              />
            </div>
            <div>
              <label
                htmlFor="finding-body"
                className="mb-1.5 block text-xs font-medium"
              >
                Basis
              </label>
              <textarea
                id="finding-body"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                rows={4}
                placeholder="What you observed, and how you know."
                className="w-full rounded-md border border-border-default bg-surface-inset px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-tertiary focus:border-accent"
              />
            </div>

            {results && results.length > 0 ? (
              <fieldset>
                <legend className="mb-1.5 text-xs font-medium">
                  Supporting results{" "}
                  <span className="font-normal text-tertiary">
                    ({cited.length} selected)
                  </span>
                </legend>
                <ul className="max-h-40 space-y-1 overflow-auto rounded-md border border-border-subtle p-2">
                  {results.map((r) => (
                    <li key={r.id}>
                      <label className="flex items-center gap-2 px-1 py-1 text-xs hover:bg-surface-2">
                        <input
                          type="checkbox"
                          checked={cited.includes(r.id)}
                          onChange={(e) =>
                            setCited(
                              e.target.checked
                                ? [...cited, r.id]
                                : cited.filter((id) => id !== r.id),
                            )
                          }
                          className="h-3.5 w-3.5 accent-[var(--accent)]"
                        />
                        <span className="font-mono font-medium">{r.module}</span>
                        <span className="text-secondary">{r.score.toFixed(4)}</span>
                        <span className="text-tertiary">thr {r.threshold}</span>
                      </label>
                    </li>
                  ))}
                </ul>
              </fieldset>
            ) : null}

            {cited.length === 0 ? (
              <Notice tone="warn">
                No supporting result selected. A finding recorded with no analysis
                behind it is a bare assertion, and it will read as one in the export.
              </Notice>
            ) : null}

            {error ? (
              <div role="alert">
                <Notice tone="alert">{error}</Notice>
              </div>
            ) : null}

            <Button
              variant="primary"
              disabled={pending || !title.trim()}
              onClick={create}
            >
              {pending ? "Saving…" : "Record finding"}
            </Button>
          </CardBody>
        </Card>
      ) : null}

      {findings.length === 0 ? (
        <Card>
          <EmptyState
            icon="◇"
            title="No findings recorded"
            hint="Findings are written by people, from evidence. This system will not write one for you."
          />
        </Card>
      ) : (
        <ul className="space-y-2.5">
          {findings.map((f) => (
            <li key={f.id}>
              <Card>
                <CardBody className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-sm font-medium">{f.title}</h3>
                    <Badge
                      tone={
                        f.status === "confirmed"
                          ? "calm"
                          : f.status === "rejected"
                            ? "alert"
                            : "neutral"
                      }
                    >
                      {f.status}
                    </Badge>
                    <span className="ml-auto text-[11px] text-tertiary">
                      {formatDateTime(f.created_at)}
                    </span>
                  </div>
                  <div className="text-xs leading-relaxed whitespace-pre-wrap text-secondary">
                    {f.body}
                  </div>
                </CardBody>
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
