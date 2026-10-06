import Link from "next/link";
import type { ReactNode } from "react";
import type { CaseRead } from "@/lib/types";

/**
 * A case tile.
 *
 * The whole card is a link rather than a card with a link inside — larger hit
 * target, and no nested-interactive problem. Status and priority are passed as
 * nodes rather than strings so the parent owns the colour mapping, keeping the
 * pill styling in exactly one place.
 */
export function CaseCard({
  case_,
  status,
  priority,
  updated,
}: {
  case_: CaseRead;
  status: ReactNode;
  priority: ReactNode;
  updated: string;
}) {
  return (
    <Link
      href={`/cases/${case_.id}`}
      className="group flex h-full flex-col rounded-lg border border-border-subtle bg-surface-1 p-4 transition-all hover:border-accent-border hover:bg-surface-2"
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-xs font-semibold tracking-tight text-accent">
          {case_.number}
        </span>
        {priority}
      </div>

      <h2 className="mt-1.5 line-clamp-2 text-sm font-medium leading-snug group-hover:text-accent">
        {case_.title}
      </h2>

      {case_.description ? (
        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-secondary">
          {case_.description}
        </p>
      ) : null}

      <div className="mt-auto flex items-center gap-2 pt-3">
        {status}
        <span className="ml-auto text-[11px] text-tertiary">{updated}</span>
      </div>
    </Link>
  );
}
