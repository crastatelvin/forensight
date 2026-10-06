"use client";

import { useState, useTransition, useMemo } from "react";
import { useRouter } from "next/navigation";
import type {
  AnalysisJobRead,
  AnalysisResultRead,
  EvidenceRead,
  ModuleRead,
} from "@/lib/types";
import {
  explainJob,
  explainModule,
  explainScore,
  explainTier,
  calibrationFlags,
  detailOf,
  type Outcome,
} from "@/lib/explain";
import { runAnalysisAction } from "@/app/cases/actions";
import {
  Button,
  Card,
  CardHeader,
  CardBody,
  EmptyState,
  Notice,
  Badge,
  DefList,
  IndeterminateBar,
} from "@/components/ui";

/**
 * The analyses tab: run an analyzer, watch it, read the result honestly.
 *
 * The one rule this screen enforces: a job that did not measure anything produces
 * no score anywhere on it. `explainJob` is the only path from a job to a rendered
 * outcome, so "not_applicable" cannot reach the UI as a zero.
 */

/** Modules that need a second image, and the parameter they need. */
const NEEDS_REFERENCE = new Set(["face-compare", "image-prnu"]);

export function AnalysisTab({
  caseId,
  evidence,
  jobs,
  results,
  modules,
  canWrite,
}: {
  caseId: string;
  evidence: EvidenceRead[] | null;
  jobs: AnalysisJobRead[] | null;
  results: AnalysisResultRead[] | null;
  /** Fetched in the server render, so the picker is never briefly empty. */
  modules: ModuleRead[] | null;
  canWrite: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [module, setModule] = useState<string>("");
  const [evidenceId, setEvidenceId] = useState<string>("");
  const [referenceId, setReferenceId] = useState<string>("");
  const [expanded, setExpanded] = useState<string | null>(null);

  const jobRows = useMemo(() => {
    const byJob = new Map<string, AnalysisResultRead>();
    for (const r of results ?? []) byJob.set(r.job_id, r);
    return (jobs ?? [])
      .slice()
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((job) => ({ job, outcome: explainJob(job, byJob.get(job.id)) }));
  }, [jobs, results]);

  const anyRunning = (jobs ?? []).some(
    (j) => j.status === "queued" || j.status === "running",
  );

  function run() {
    if (!module || !evidenceId) return;
    setError(null);
    startTransition(async () => {
      const result = await runAnalysisAction(caseId, {
        module,
        evidenceId,
        params:
          NEEDS_REFERENCE.has(module) && referenceId
            ? { reference_evidence_id: referenceId }
            : {},
      });
      if (!result.ok) {
        setError(result.error ?? "The analysis could not be queued.");
        return;
      }
      router.refresh();
      // A queued job needs polling to become visible. `router.refresh()` alone
      // leaves the row showing "queued" forever, which reads as a stalled system.
      pollUntilTerminal(result.jobId);
    });
  }

  /**
   * Refresh until the job stops moving.
   *
   * Bounded rather than open-ended: an unbounded poll against a wedged job is a
   * request every two seconds forever, and a page that never stops asking looks
   * identical to one that is working. 60 attempts is ~2 minutes, past which the
   * status is shown as whatever it is, unverified.
   */
  function pollUntilTerminal(jobId: string | undefined) {
    if (!jobId) return;
    let attempts = 0;
    const tick = async () => {
      attempts += 1;
      router.refresh();
      if (attempts > 60) return;
      const res = await fetch(`/api/jobs/${jobId}`, { cache: "no-store" }).catch(
        () => null,
      );
      if (!res?.ok) return;
      const body = (await res.json()) as { status?: string };
      if (body.status && !["queued", "running"].includes(body.status)) return;
      setTimeout(tick, 2000);
    };
    setTimeout(tick, 1500);
  }

  return (
    <div className="space-y-5">
      {canWrite && evidence && evidence.length > 0 ? (
        <Card>
          <CardHeader
            title="Run an analysis"
            subtitle="Each analyzer scores on its own scale. A score only means something next to its threshold."
          />
          <CardBody className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Analyzer" htmlFor="module">
                <select
                  id="module"
                  value={module}
                  onChange={(e) => setModule(e.target.value)}
                  className="h-9 w-full rounded-md border border-border-default bg-surface-inset px-2.5 text-sm outline-none focus:border-accent"
                >
                  <option value="">Choose an analyzer…</option>
                  {(modules ?? [])
                    .filter((m) => m.available)
                    .map((m) => (
                      <option key={m.name} value={m.name}>
                        {m.name} — Tier {m.tier}
                      </option>
                    ))}
                </select>
              </Field>

              <Field label="Evidence" htmlFor="evidence">
                <select
                  id="evidence"
                  value={evidenceId}
                  onChange={(e) => setEvidenceId(e.target.value)}
                  className="h-9 w-full rounded-md border border-border-default bg-surface-inset px-2.5 text-sm outline-none focus:border-accent"
                >
                  <option value="">Choose evidence…</option>
                  {evidence.map((e) => (
                    <option key={e.id} value={e.id}>
                      {e.filename}
                    </option>
                  ))}
                </select>
              </Field>

              {NEEDS_REFERENCE.has(module) ? (
                <Field
                  label="Reference image"
                  htmlFor="reference"
                  hint={
                    module === "face-compare"
                      ? "Compares the two faces' similarity. It verifies a pair you chose — it never searches for who someone is."
                      : "A second frame from the same camera. PRNU answers whether two images share a camera, not a person."
                  }
                >
                  <select
                    id="reference"
                    value={referenceId}
                    onChange={(e) => setReferenceId(e.target.value)}
                    className="h-9 w-full rounded-md border border-border-default bg-surface-inset px-2.5 text-sm outline-none focus:border-accent"
                  >
                    <option value="">Choose a reference…</option>
                    {evidence
                      .filter((e) => e.id !== evidenceId)
                      .map((e) => (
                        <option key={e.id} value={e.id}>
                          {e.filename}
                        </option>
                      ))}
                  </select>
                </Field>
              ) : null}
            </div>

            {modules !== null && modules.length === 0 ? (
              <Notice tone="warn">
                The analyzer catalogue could not be read. Nothing can be run until the
                API is reachable — which is different from having no analyzers.
              </Notice>
            ) : null}

            {module && !referenceId && NEEDS_REFERENCE.has(module) ? (
              <Notice tone="info">
                {module} needs a reference image. Without one it will report{" "}
                <em>not measured</em>, not a score of zero.
              </Notice>
            ) : null}

            {error ? (
              <div role="alert">
                <Notice tone="alert">{error}</Notice>
              </div>
            ) : null}

            <Button
              variant="primary"
              disabled={
                pending ||
                !module ||
                !evidenceId ||
                (NEEDS_REFERENCE.has(module) && !referenceId)
              }
              onClick={run}
            >
              {pending ? "Queuing…" : "Run analysis"}
            </Button>
          </CardBody>
        </Card>
      ) : null}

      {anyRunning ? (
        <div role="status" aria-live="polite">
          <Card className="p-3">
            <IndeterminateBar label="Analysis running — results appear when it finishes" />
          </Card>
        </div>
      ) : null}

      {jobs === null ? (
        <Notice tone="alert" title="Could not load analyses">
          This list is unavailable. Do not read that as &ldquo;nothing has been
          analysed&rdquo;.
        </Notice>
      ) : jobRows.length === 0 ? (
        <Card>
          <EmptyState
            icon="◌"
            title="No analyses yet"
            hint={
              canWrite && evidence && evidence.length > 0
                ? "Run one above. Results carry their threshold, model version and limitations — a bare number is not a finding."
                : "Upload evidence first; there is nothing to analyse yet."
            }
          />
        </Card>
      ) : (
        <ul className="space-y-2.5">
          {jobRows.map(({ job, outcome }) => (
            <ResultRow
              key={job.id}
              job={job}
              outcome={outcome}
              expanded={expanded === job.id}
              onToggle={() => setExpanded(expanded === job.id ? null : job.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function Field({
  label,
  htmlFor,
  hint,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1.5 block text-xs font-medium">
        {label}
      </label>
      {children}
      {hint ? (
        <p className="mt-1 text-[11px] leading-relaxed text-tertiary">{hint}</p>
      ) : null}
    </div>
  );
}

/**
 * One analysis outcome.
 *
 * Three visually distinct states and no fourth: a score, a refusal, or a failure.
 * The refusal state is the important one — it is styled as a neutral notice
 * rather than an error, because "this technique does not apply here" is a correct
 * answer, not a problem. Rendering it in red would train people to ignore it,
 * which is precisely when it matters most.
 */
function ResultRow({
  job,
  outcome,
  expanded,
  onToggle,
}: {
  job: AnalysisJobRead;
  outcome: Outcome;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (outcome.kind === "not_measured") {
    return (
      <li>
        <Card className="border-l-2 border-l-border-strong">
          <CardBody className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-medium">{job.module}</span>
              <Badge tone="neutral">not measured</Badge>
              <span className="text-[11px] text-tertiary">
                {new Date(job.created_at).toLocaleString()}
              </span>
            </div>
            <p className="text-xs leading-relaxed text-secondary">{outcome.reason}</p>
            <p className="text-[11px] leading-relaxed text-tertiary">
              No score was produced, and none is shown. An absent measurement is not a
              passing result.
            </p>
          </CardBody>
        </Card>
      </li>
    );
  }

  if (outcome.kind === "failed") {
    return (
      <li>
        <Card className="border-l-2 border-l-alert">
          <CardBody className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-medium">{job.module}</span>
              <Badge tone="alert">failed</Badge>
              {job.attempts > 1 ? (
                <span className="text-[11px] text-tertiary">
                  {job.attempts} attempts
                </span>
              ) : null}
            </div>
            <p className="font-mono text-[11px] leading-relaxed break-words text-secondary">
              {outcome.reason}
            </p>
          </CardBody>
        </Card>
      </li>
    );
  }

  if (outcome.kind === "pending") {
    return (
      <li>
        <Card>
          <CardBody className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-medium">{job.module}</span>
              <Badge tone="info">{job.status}</Badge>
            </div>
            <IndeterminateBar label="Queued" />
          </CardBody>
        </Card>
      </li>
    );
  }

  const { result } = outcome;
  const score = explainScore(result.score, result.threshold);
  const tier = explainTier(result.tier);
  const flags = calibrationFlags(result.payload);
  const explanation = explainModule(result.module);

  return (
    <li>
      <Card
        className={
          score.tone === "alert"
            ? "border-l-2 border-l-alert"
            : score.tone === "warn"
              ? "border-l-2 border-l-warn"
              : "border-l-2 border-l-calm"
        }
      >
        <CardBody className="space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-semibold">{result.module}</span>
                <Badge
                  tone={result.tier === "A" ? "accent" : "info"}
                  title={tier.meaning}
                >
                  {tier.label}
                </Badge>
                <Badge
                  tone={
                    score.tone === "alert"
                      ? "alert"
                      : score.tone === "warn"
                        ? "warn"
                        : "calm"
                  }
                >
                  {score.verdict}
                </Badge>
              </div>
              <p className="mt-1 text-[11px] text-tertiary">
                {new Date(result.created_at).toLocaleString()}
              </p>
            </div>

            <div className="text-right">
              <div className="font-mono text-2xl leading-none tabular-nums">
                {result.score.toFixed(4)}
              </div>
              <div className="mt-1 font-mono text-[11px] text-tertiary">
                threshold {result.threshold}
              </div>
            </div>
          </div>

          {/* The score and the threshold are visually one unit, always. A score
              rendered on its own is the thing this project exists to avoid. */}
          <p className="text-xs leading-relaxed text-secondary">{score.detail}</p>

          <Notice tone={result.tier === "A" ? "info" : "warn"} title={tier.label}>
            {result.disclaimer}
            <span className="mt-1 block opacity-80">{tier.meaning}</span>
          </Notice>

          {flags.map((flag) => (
            <Notice key={flag} tone="warn">
              {flag}
            </Notice>
          ))}

          {explanation ? (
            <details
              open={expanded}
              onToggle={(e) => {
                if ((e.target as HTMLDetailsElement).open !== expanded) onToggle();
              }}
              className="rounded border border-border-subtle bg-surface-2/40"
            >
              <summary className="cursor-pointer px-3 py-2 text-xs font-medium">
                What this measures, and what it cannot tell you
              </summary>
              <div className="space-y-3 border-t border-border-subtle px-3 py-2.5">
                <p className="text-[11px] leading-relaxed text-secondary">
                  {explanation}
                </p>
                <DefList
                  columns={2}
                  items={[
                    {
                      term: "Model version",
                      value: <span className="font-mono">{result.model_version}</span>,
                    },
                    { term: "Threshold", value: result.threshold },
                    {
                      term: "Score means",
                      value:
                        typeof result.payload.score_is === "string"
                          ? result.payload.score_is
                          : "See the analyzer's description above",
                    },
                    {
                      term: "Evidence",
                      value: (
                        <span className="font-mono text-[11px]">
                          {result.evidence_id}
                        </span>
                      ),
                    },
                  ]}
                />
                <ResultPayload payload={result.payload} />
              </div>
            </details>
          ) : null}
        </CardBody>
      </Card>
    </li>
  );
}

/**
 * The analyzer's own payload, rendered as readable key/value pairs.
 *
 * Rendered generically rather than per-module because every analyzer ships a
 * different body and a per-module renderer would be six components that drift.
 * Nested objects are shown as indented JSON, which is honest about the fact that
 * this part of the payload is machine-shaped and not prose.
 */
function ResultPayload({ payload }: { payload: Record<string, unknown> }) {
  // Rendered from `detail`, which is where every module puts its own facts.
  const detail = detailOf(payload);
  const OMIT = new Set(["known_limitations", "interpretation", "score_is", "params"]);
  const entries = Object.entries(detail).filter(
    ([k, v]) => !OMIT.has(k) && v !== null && typeof v !== "object",
  );

  if (entries.length === 0) return null;

  return (
    <details className="rounded border border-border-subtle">
      <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium">
        Raw analyzer output
      </summary>
      <div className="space-y-2.5 border-t border-border-subtle px-3 py-2.5">
        {typeof detail.interpretation === "string" ? (
          <p className="text-[11px] leading-relaxed text-secondary">
            {detail.interpretation}
          </p>
        ) : null}

        {Array.isArray(detail.known_limitations) ? (
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-tertiary">
              Known limitations
            </div>
            <ul className="list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-secondary">
              {(detail.known_limitations as string[]).map((limit) => (
                <li key={limit}>{limit}</li>
              ))}
            </ul>
          </div>
        ) : null}

        <DefList
          columns={2}
          items={entries.map(([key, value]) => ({
            term: key,
            value:
              value === null || value === undefined ? (
                <span className="text-tertiary">—</span>
              ) : typeof value === "object" ? (
                <pre className="max-h-40 overflow-auto rounded bg-surface-0 p-2 font-mono text-[10px] leading-relaxed">
                  {JSON.stringify(value, null, 1)}
                </pre>
              ) : (
                <span className="font-mono">{String(value)}</span>
              ),
          }))}
        />
      </div>
    </details>
  );
}
