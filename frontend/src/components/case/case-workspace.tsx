"use client";

import { useState } from "react";
import type { ReactNode } from "react";
import type { CaseBundle } from "@/app/cases/[caseId]/page";
import type { ModuleRead } from "@/lib/types";
import { cx } from "@/components/ui";
import { EvidenceTab } from "./evidence-tab";
import { AnalysisTab } from "./analysis-tab";
import { AgentTab } from "./agent-tab";
import { FindingsTab } from "./findings-tab";
import { TimelineTab } from "./timeline-tab";
import { AuditTab } from "./audit-tab";
import { ResultsOverview } from "./results-overview";
import { ReportPanel } from "./report-panel";
import { GeospatialPanel, CoordinateTable } from "./geospatial-panel";
import { collectCoordinates } from "@/lib/geo";

/**
 * The tabbed case workspace.
 *
 * Client-side tabs over one server-fetched bundle. Two reasons, and both matter:
 * switching tabs is instant, and — more importantly — the agent console keeps its
 * SSE connection and stream state alive while you look at the evidence. A
 * route-based tab would drop the stream every time you went to check the image the
 * agent was talking about.
 *
 * Implemented with ARIA tabs and roving tabindex rather than a `<details>` stack:
 * arrow-key navigation between tabs is what a screen-reader user expects, and the
 * panel association is what lets them jump straight to the content.
 */
export function CaseWorkspace({
  bundle,
  role,
  modules,
}: {
  bundle: CaseBundle;
  role: string;
  modules: ModuleRead[] | null;
}) {
  const [tab, setTab] = useState<TabId>("evidence");
  const canWrite = role !== "reviewer";

  // Null-safe throughout: the sections are nullable by design (see CaseBundle),
  // and a count badge that has to guess would be a badge that lies.
  const pendingJobs = (bundle.jobs ?? []).filter(
    (j) => j.status === "queued" || j.status === "running",
  );

  const tabs: Array<{ id: TabId; label: string; badge?: ReactNode }> = [
    { id: "evidence", label: "Evidence", badge: badge(bundle.evidence?.length) },
    {
      id: "analyses",
      label: "Analyses",
      badge:
        pendingJobs.length > 0 ? (
          <span className="ml-1.5 animate-pulse text-info">{pendingJobs.length}</span>
        ) : (
          badge(bundle.results?.length)
        ),
    },
    { id: "agent", label: "Agent" },
    { id: "findings", label: "Findings", badge: badge(bundle.findings?.length) },
    { id: "timeline", label: "Timeline", badge: badge(bundle.timeline?.length) },
    { id: "audit", label: "Audit", badge: badge(bundle.audit?.length) },
  ];

  return (
    <div>
      <div
        role="tablist"
        aria-label="Case sections"
        className="flex flex-wrap gap-1 border-b border-border-subtle"
      >
        {tabs.map((t) => (
          <TabButton
            key={t.id}
            id={t.id}
            label={t.label}
            badge={t.badge}
            selected={tab === t.id}
            onSelect={setTab}
          />
        ))}
      </div>

      <div
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
        tabIndex={0}
        className="pt-5 focus-visible:outline-none"
      >
        {tab === "evidence" ? (
          <EvidenceTab
            caseId={bundle.case.id}
            evidence={bundle.evidence}
            results={bundle.results}
            canWrite={canWrite}
          />
        ) : null}
        {tab === "analyses" ? (
          <>
            <ResultsOverview results={bundle.results} tiers={bundle.tiers ?? []} />
            <AnalysisTab
              tiers={bundle.tiers ?? []}
              caseId={bundle.case.id}
              evidence={bundle.evidence}
              jobs={bundle.jobs}
              results={bundle.results}
              modules={modules}
              canWrite={canWrite}
            />
          </>
        ) : null}
        {tab === "agent" ? (
          <AgentTab
            caseId={bundle.case.id}
            evidence={bundle.evidence ?? []}
            existingRuns={bundle.runs}
          />
        ) : null}
        {tab === "findings" ? (
          <FindingsTab
            caseId={bundle.case.id}
            findings={bundle.findings}
            results={bundle.results}
            canWrite={canWrite}
          />
        ) : null}
        {tab === "findings" ? (
          <ReportPanel caseId={bundle.case.id} disabled={!canWrite} />
        ) : null}
        {tab === "timeline" ? (
          <>
            <TimelineTab events={bundle.timeline} />
            <GeospatialPanel
              points={collectCoordinates(bundle.results, bundle.evidence)}
            >
              <CoordinateTable
                points={collectCoordinates(bundle.results, bundle.evidence)}
              />
            </GeospatialPanel>
          </>
        ) : null}
        {tab === "audit" ? (
          <AuditTab entries={bundle.audit} caseId={bundle.case.id} />
        ) : null}
      </div>
    </div>
  );
}

type TabId = "evidence" | "analyses" | "agent" | "findings" | "timeline" | "audit";

function badge(n: number | undefined): ReactNode {
  if (!n) return null;
  return <span className="ml-1.5 text-tertiary">{n}</span>;
}

/**
 * One tab button.
 *
 * Roving tabindex: exactly one tab is tabbable, and arrow keys move between them.
 * Without this a keyboard user has to press Tab once per tab to reach the panel,
 * which on a six-tab strip is the difference between usable and not.
 */
function TabButton({
  id,
  label,
  badge,
  selected,
  onSelect,
}: {
  id: TabId;
  label: string;
  badge?: ReactNode;
  selected: boolean;
  onSelect: (id: TabId) => void;
}) {
  function onKeyDown(event: React.KeyboardEvent) {
    const order: TabId[] = [
      "evidence",
      "analyses",
      "agent",
      "findings",
      "timeline",
      "audit",
    ];
    const index = order.indexOf(id);
    let next: number | null = null;
    if (event.key === "ArrowRight") next = (index + 1) % order.length;
    else if (event.key === "ArrowLeft")
      next = (index - 1 + order.length) % order.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = order.length - 1;
    if (next === null) return;

    event.preventDefault();
    onSelect(order[next]);
    // Move focus with the selection, which is what "automatic activation" means.
    // `queueMicrotask` because the target does not exist until the re-render.
    queueMicrotask(() => document.getElementById(`tab-${order[next!]}`)?.focus());
  }

  return (
    <button
      id={`tab-${id}`}
      role="tab"
      type="button"
      aria-selected={selected}
      aria-controls={`panel-${id}`}
      tabIndex={selected ? 0 : -1}
      onClick={() => onSelect(id)}
      onKeyDown={onKeyDown}
      className={cx(
        "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
        selected
          ? "border-accent text-accent"
          : "border-transparent text-secondary hover:border-border-strong hover:text-primary",
      )}
    >
      {label}
      {badge}
    </button>
  );
}
