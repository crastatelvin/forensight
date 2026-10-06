"use client";

import { useMemo, useState } from "react";
import type { AuditEntryRead } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { Card, CardHeader, CardBody, EmptyState, Notice, Badge } from "@/components/ui";
import { ChainStatus } from "./chain-status";

/**
 * The audit log.
 *
 * Filtered client-side over a bounded window, and the window is stated in the UI.
 * A log that silently shows the last 200 entries and calls itself "the audit log"
 * is worse than one that admits a limit — a reviewer needs to know they are not
 * seeing everything.
 */
export function AuditTab({
  entries,
  caseId,
}: {
  entries: AuditEntryRead[] | null;
  caseId: string;
}) {
  const [filter, setFilter] = useState("");
  const [chain, setChain] = useState<unknown>(null);

  const filtered = useMemo(() => {
    if (!entries) return null;
    if (!filter.trim()) return entries;
    const needle = filter.toLowerCase();
    return entries.filter(
      (e) =>
        e.action.toLowerCase().includes(needle) ||
        String(e.target).toLowerCase().includes(needle) ||
        JSON.stringify(e.payload).toLowerCase().includes(needle),
    );
  }, [entries, filter]);

  const actions = useMemo(
    () => [...new Set((entries ?? []).map((e) => e.action))].sort(),
    [entries],
  );

  if (entries === null) {
    return (
      <div className="space-y-4">
        <Notice tone="alert" title="Could not load the audit log">
          The audit log is the record of who did what. A blank panel here is a failure
          to load, not an absence of activity — treat any conclusion drawn from this
          view as unsupported until it renders.
        </Notice>
        <ChainStatus caseId={caseId} status={chain} onCheck={setChain} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <ChainStatus caseId={caseId} status={chain} onCheck={setChain} />

      <Card>
        <CardHeader
          title={`Audit entries (${entries.length})`}
          subtitle={
            entries.length >= 200
              ? "Showing the most recent 200 entries. This is a window, not the whole record — use the export for a complete trail."
              : undefined
          }
          action={
            <div className="flex gap-2">
              <select
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                aria-label="Filter by action"
                className="h-7 rounded-md border border-border-default bg-surface-inset px-2 text-xs outline-none focus:border-accent"
              >
                <option value="">All actions</option>
                {actions.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Search…"
                aria-label="Search the audit log"
                className="h-7 w-40 rounded-md border border-border-default bg-surface-inset px-2 text-xs outline-none placeholder:text-tertiary focus:border-accent"
              />
            </div>
          }
        />

        {filtered && filtered.length === 0 ? (
          <EmptyState
            icon="◌"
            title={filter ? "Nothing matches that filter" : "No audit entries"}
            hint={
              filter
                ? "Clear the filter to see everything loaded."
                : "Entries appear as work happens on this case."
            }
          />
        ) : (
          <CardBody className="p-0">
            <div className="max-h-[32rem] overflow-auto">
              <table className="w-full text-left text-xs">
                <caption className="sr-only">
                  Audit log entries, most recent first
                </caption>
                <thead className="sticky top-0 bg-surface-1">
                  <tr className="border-b border-border-subtle">
                    <th scope="col" className="px-4 py-2 font-medium">
                      When
                    </th>
                    <th scope="col" className="px-2 py-2 font-medium">
                      Action
                    </th>
                    <th scope="col" className="px-2 py-2 font-medium">
                      Actor
                    </th>
                    <th scope="col" className="px-4 py-2 font-medium">
                      Detail
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {(filtered ?? [])
                    .slice()
                    .reverse()
                    .map((e) => (
                      <tr key={e.id} className="hover:bg-surface-2/50">
                        <td className="px-4 py-2 font-mono text-[11px] whitespace-nowrap text-tertiary">
                          {formatDateTime(e.created_at)}
                        </td>
                        <td className="px-2 py-2">
                          <Badge
                            tone={
                              e.action.includes("delete") || e.action.includes("reject")
                                ? "alert"
                                : e.action.includes("create") ||
                                    e.action.includes("upload")
                                  ? "calm"
                                  : "neutral"
                            }
                          >
                            {e.action}
                          </Badge>
                        </td>
                        <td className="px-2 py-2 font-mono text-[11px] text-tertiary">
                          {e.actor_id ? e.actor_id.slice(0, 8) : "system"}
                        </td>
                        <td className="px-4 py-2 font-mono text-[11px] break-all text-secondary">
                          {JSON.stringify(e.payload)}
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        )}
      </Card>
    </div>
  );
}
