import type { CaseRead, FindingRead } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { StatusPill, PriorityPill } from "@/components/pills";
import { CaseWorkflow } from "./case-workflow";

/**
 * Case header.
 *
 * Status and priority are rendered as pills rather than free text, but each pill
 * spells its own value out — colour alone would fail a colour-blind reviewer, and
 * in a forensic tool a misread status is a correctness problem rather than a
 * cosmetic one.
 */
export function CaseHeader({
  case_,
  findings,
}: {
  case_: CaseRead;
  findings: FindingRead[];
}) {
  const openFindings = findings.filter(
    (f) => !["confirmed", "rejected", "closed"].includes(f.status.toLowerCase()),
  ).length;

  return (
    <header className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2.5">
            <span className="font-mono text-sm font-semibold text-accent">
              {case_.number}
            </span>
            <StatusPill status={case_.status} />
            <PriorityPill priority={case_.priority} />
          </div>
          <h1 className="mt-1.5 text-lg font-semibold tracking-tight">{case_.title}</h1>
          {case_.description ? (
            <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-secondary">
              {case_.description}
            </p>
          ) : null}
        </div>

        <div className="shrink-0">
          <CaseWorkflow case_={case_} />
        </div>
      </div>

      <dl className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-tertiary">
        <div className="flex gap-1.5">
          <dt>Opened</dt>
          <dd className="text-secondary">{formatDate(case_.created_at)}</dd>
        </div>
        <div className="flex gap-1.5">
          <dt>Open findings</dt>
          <dd className="text-secondary">{openFindings}</dd>
        </div>
        {case_.due_at ? (
          <div className="flex gap-1.5">
            <dt>Due</dt>
            <dd className="text-secondary">{formatDate(case_.due_at)}</dd>
          </div>
        ) : null}
      </dl>
    </header>
  );
}
