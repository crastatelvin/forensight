import Link from "next/link";
import { apiGet, ApiError } from "@/lib/api";
import { requireSession, isAdmin } from "@/lib/session";
import type { CapabilitiesResponse, AnalysisJobRead, CaseRead } from "@/lib/types";
import { formatRelative } from "@/lib/format";
import { CaseCard } from "@/components/case-card";
import { CapabilityPanel } from "@/components/capability-panel";
import { StatusPill, PriorityPill } from "@/components/pills";
import { EmptyState, Card, CardHeader, CardBody } from "@/components/ui";

export const dynamic = "force-dynamic";

/**
 * The dashboard.
 *
 * Two independent fetches. If the case list fails the page still renders — a
 * dashboard that is entirely blank because one sub-request 500s tells the user
 * nothing, whereas a page with an error card in the right place tells them
 * exactly what is wrong.
 */
export default async function Dashboard() {
  const user = await requireSession();

  let cases: CaseRead[] = [];
  let casesError: string | null = null;
  try {
    cases = await apiGet<CaseRead[]>("/cases?limit=12");
  } catch (error) {
    casesError = error instanceof ApiError ? error.message : "Could not load cases.";
  }

  let capabilities: CapabilitiesResponse | null = null;
  try {
    capabilities = await apiGet<CapabilitiesResponse>("/capabilities");
  } catch {
    capabilities = null;
  }

  const open = cases.filter(
    (c) => !["closed", "archived"].includes(c.status.toLowerCase()),
  );

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Cases</h1>
          <p className="mt-0.5 text-sm text-secondary">
            {open.length} open of {cases.length} recent
            {user.role === "reviewer" ? " · read-only access" : null}
          </p>
        </div>
        {user.role !== "reviewer" ? (
          <Link
            href="/cases/new"
            className="inline-flex h-9 items-center rounded-md border border-transparent bg-accent px-3.5 text-sm font-medium text-accent-fg transition-colors hover:bg-accent-hover"
          >
            New case
          </Link>
        ) : null}
      </header>

      {casesError ? (
        <div role="alert">
          <Card className="border-alert-border bg-alert-bg p-4">
            <p className="text-sm font-medium text-alert">Could not load cases</p>
            <p className="mt-1 text-xs text-alert">{casesError}</p>
          </Card>
        </div>
      ) : cases.length === 0 ? (
        <Card>
          <EmptyState
            icon="◫"
            title="No cases yet"
            hint={
              user.role === "reviewer"
                ? "Cases appear here once an officer or expert opens one."
                : "Create a case to start collecting evidence. Every upload is hashed and written to the chain of custody on arrival."
            }
            action={
              user.role !== "reviewer" ? (
                <Link
                  href="/cases/new"
                  className="inline-flex h-9 items-center rounded-md bg-accent px-3.5 text-sm font-medium text-accent-fg hover:bg-accent-hover"
                >
                  New case
                </Link>
              ) : null
            }
          />
        </Card>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {cases.map((c) => (
            <li key={c.id}>
              <CaseCard
                case_={c}
                status={<StatusPill status={c.status} />}
                priority={<PriorityPill priority={c.priority} />}
                updated={formatRelative(c.updated_at)}
              />
            </li>
          ))}
        </ul>
      )}

      {isAdmin(user.role) || capabilities ? (
        <CapabilityPanel capabilities={capabilities} />
      ) : null}

      {cases.length > 0 ? <RecentJobs /> : null}
    </div>
  );
}

/**
 * Recent analysis jobs across visible cases.
 *
 * Bounded to the cases already listed rather than paginating the whole database:
 * this is a dashboard glance, not a report. A hard cap is stated here because an
 * unbounded query on a real deployment is a page that eventually never loads.
 */
async function RecentJobs() {
  let rows: Array<{ job: AnalysisJobRead; caseNumber: string }> = [];

  try {
    const cases = await apiGet<CaseRead[]>("/cases?limit=6");
    const batches = await Promise.all(
      cases.map(async (c) => {
        try {
          const jobs = await apiGet<AnalysisJobRead[]>(
            `/cases/${c.id}/analyses?limit=3`,
          );
          return jobs.map((job) => ({ job, caseNumber: c.number }));
        } catch {
          return [];
        }
      }),
    );
    rows = batches.flat().slice(0, 8);
  } catch {
    return null;
  }

  if (rows.length === 0) return null;

  return (
    <Card>
      <CardHeader title="Recent analyses" subtitle="Latest across the cases above" />
      <CardBody className="p-0">
        <ul className="divide-y divide-border-subtle">
          {rows.map(({ job, caseNumber }) => (
            <li
              key={job.id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-xs"
            >
              <span className="font-mono font-medium">{job.module}</span>
              <span className="text-tertiary">{caseNumber}</span>
              <span className="ml-auto">
                <JobStatusBadge status={job.status} />
              </span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

function JobStatusBadge({ status }: { status: string }) {
  const tone =
    status === "done"
      ? "calm"
      : status === "failed"
        ? "alert"
        : status === "not_applicable"
          ? "neutral"
          : "info";
  const label =
    status === "not_applicable" ? "not measured" : status.replace(/_/g, " ");
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium ${
        tone === "calm"
          ? "border-calm-border bg-calm-bg text-calm"
          : tone === "alert"
            ? "border-alert-border bg-alert-bg text-alert"
            : tone === "neutral"
              ? "border-border-default bg-surface-2 text-secondary"
              : "border-info-border bg-info-bg text-info"
      }`}
    >
      {label}
    </span>
  );
}
