import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { apiGet, ApiError } from "@/lib/api";
import { requireSession } from "@/lib/session";
import type {
  AgentRunRead,
  AnalysisJobRead,
  AnalysisResultRead,
  AuditEntryRead,
  CaseRead,
  EvidenceRead,
  FindingRead,
  ModuleRead,
  TimelineEventRead,
} from "@/lib/types";
import { CaseHeader } from "@/components/case/case-header";
import { CaseWorkspace } from "@/components/case/case-workspace";

export const dynamic = "force-dynamic";

/**
 * Everything the workspace needs, fetched in parallel on the server.
 *
 * Every list is nullable, and that is not optional: a section that failed to load
 * is a *different state* from a section that is empty, and rendering them the same
 * way is how a broken audit log becomes an apparently clean one. `null` means
 * "could not load"; `[]` means "loaded, and there is nothing".
 *
 * An earlier version of this typed these as `T` and returned `null as T`. That
 * cast silenced the compiler and produced a hard crash three components later,
 * on a page that had loaded perfectly well in every unit test.
 */
export interface CaseBundle {
  case: CaseRead;
  evidence: EvidenceRead[] | null;
  jobs: AnalysisJobRead[] | null;
  results: AnalysisResultRead[] | null;
  findings: FindingRead[] | null;
  timeline: TimelineEventRead[] | null;
  audit: AuditEntryRead[] | null;
  /**
   * Agent runs on this case, newest first.
   *
   * Fetched here rather than on tab selection so a reload does not present the
   * console as empty. A run's report, citations and tool-call audit are durable
   * records of what was done; showing "no run yet" because the browser that
   * started it is closed would be the wrong default for a forensic tool.
   */
  runs: AgentRunRead[] | null;
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ caseId: string }>;
}): Promise<Metadata> {
  const { caseId } = await params;
  try {
    const found = await apiGet<CaseRead>(`/cases/${caseId}`);
    return { title: `${found.number} — ${found.title}` };
  } catch {
    return { title: "Case" };
  }
}

/**
 * The case workspace.
 *
 * All six resources are fetched here and handed to the client as one bundle
 * rather than fetched per-tab. Two reasons: the tabs are client-side so switching
 * them is instant and does not lose console state, and six parallel server fetches
 * on one render is faster than six sequential ones on tab switch.
 *
 * Each is wrapped individually so a failure is contained. A case with results but
 * a broken audit fetch should render everything else, not an error page — and the
 * broken section says so rather than disappearing.
 */
export default async function CasePage({
  params,
}: {
  params: Promise<{ caseId: string }>;
}) {
  const { caseId } = await params;
  const user = await requireSession();

  let case_: CaseRead;
  try {
    case_ = await apiGet<CaseRead>(`/cases/${caseId}`);
  } catch (error) {
    // 403 and 404 are the same answer to the user: not something you can see. The
    // backend already refuses to leak whether a case exists.
    if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
      notFound();
    }
    throw error;
  }

  const [evidence, jobs, results, findings, timeline, audit, runs, modules] =
    await Promise.all([
      safe<EvidenceRead[]>(`/cases/${caseId}/evidence`),
      safe<AnalysisJobRead[]>(`/cases/${caseId}/analyses`),
      safe<AnalysisResultRead[]>(`/cases/${caseId}/results`),
      safe<FindingRead[]>(`/cases/${caseId}/findings`),
      safe<TimelineEventRead[]>(`/cases/${caseId}/timeline`),
      safe<AuditEntryRead[]>(`/cases/audit?limit=200`),
      // The analyzer catalogue is fetched here rather than by the picker on mount,
      // because a `<select>` that fills in after hydration renders as an empty
      // form first — which reads as broken rather than as loading.
      safe<AgentRunRead[]>(`/agent/cases/${caseId}/runs`),
      safe<ModuleRead[]>("/modules"),
    ]);

  const bundle: CaseBundle = {
    case: case_,
    evidence,
    jobs,
    results,
    findings,
    timeline,
    audit,
    runs,
  };

  return (
    <div className="space-y-5">
      <CaseHeader case_={case_} findings={findings ?? []} />
      <CaseWorkspace bundle={bundle} role={user.role} modules={modules} />
    </div>
  );
}

/**
 * Fetch a section, returning `null` on failure.
 *
 * The return type is `T | null` rather than `T`, so every consumer is forced to
 * decide what an unavailable section looks like. Casting `null` to `T` was tried
 * first and it hid a crash: a list was `.filter`ed three components downstream
 * without a check, because the compiler had been told the null could not happen.
 */
async function safe<T>(path: string): Promise<T | null> {
  try {
    return await apiGet<T>(path);
  } catch {
    return null;
  }
}
