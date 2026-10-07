"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { AgentRunRead, EvidenceRead, PendingApproval } from "@/lib/types";
import {
  startRunAction,
  approveActionAction,
  rejectActionAction,
} from "@/app/cases/[caseId]/agent-actions";
import { formatDateTime } from "@/lib/format";
import {
  Button,
  Card,
  CardHeader,
  CardBody,
  Notice,
  Badge,
  EmptyState,
  IndeterminateBar,
  cx,
} from "@/components/ui";

/**
 * The agent console.
 *
 * Streams the run over SSE and renders it as it arrives, with two things treated
 * as non-negotiable:
 *
 *  1. **Claims without citations are not rendered as claims.** The agent's own
 *     contract is that it declines rather than asserting what it cannot source. A
 *     UI that renders its prose without showing the sources would quietly undo
 *     that, which is the whole reason the contract exists.
 *  2. **Nothing state-changing happens without a human.** The stream can park an
 *     action; only an explicit click applies it.
 */

type Phase = "idle" | "starting" | "streaming" | "awaiting" | "settled" | "error";

interface StreamEvent {
  event: string;
  data: Record<string, unknown>;
}

export function AgentTab({
  caseId,
  evidence,
  existingRuns,
}: {
  caseId: string;
  evidence: EvidenceRead[];
  /** Runs already on this case, newest first. `null` means the list failed. */
  existingRuns: AgentRunRead[] | null;
}) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [objective, setObjective] = useState("");
  const [run, setRun] = useState<AgentRunRead | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<string[]>([]);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
  // Runs whose parked action this session has answered.
  //
  // Needed because `router.refresh()` did not clear the gate: the server action
  // returned ok and the database had the run at `running`, yet the gate stayed on
  // screen until a *hard* reload. Answering a question and still being shown that
  // same question is the worst outcome for a HITL control — it reads as "it did not
  // work", and the operator's next move is to approve it a second time.
  //
  // The decision is already recorded server-side by the time this is set, so the
  // local state is not asserting anything the backend has not confirmed; it is just
  // not waiting for a refresh that does not arrive.
  const [decided, setDecided] = useState<ReadonlySet<string>>(new Set());
  const sourceRef = useRef<EventSource | null>(null);

  // Closing the stream on unmount matters: an EventSource left open keeps the
  // request — and the agent's work — alive after the operator has navigated away.
  useEffect(() => {
    return () => {
      sourceRef.current?.close();
      sourceRef.current = null;
    };
  }, []);

  const onEvent = useCallback((message: StreamEvent) => {
    switch (message.event) {
      case "start":
      case "done":
        setRun(message.data as unknown as AgentRunRead);
        setPhase((p) => (p === "streaming" ? "settled" : p));
        break;
      case "step": {
        const step = String(message.data.step ?? "");
        setSteps((prev) => (prev.includes(step) ? prev : [...prev, step]));
        break;
      }
      case "awaiting_approval":
        setPending(message.data as unknown as PendingApproval);
        setPhase("awaiting");
        break;
      case "declined":
        setRun((r) => (r ? { ...r, refusal: String(message.data.reason ?? "") } : r));
        break;
      case "error":
        setError(String(message.data.error ?? "The run failed."));
        setPhase("error");
        break;
      default:
        break;
    }
  }, []);

  async function start() {
    setError(null);
    setPending(null);
    setSteps([]);
    setRun(null);
    setPhase("starting");

    const result = await startRunAction(caseId, objective);
    if (!result.ok) {
      setError(result.error);
      setPhase("error");
      return;
    }

    // The stream is opened after the run exists. Opening first would race the run
    // creation and miss the `start` event entirely.
    const source = new EventSource(`/api/agent/${result.runId}/stream`);
    sourceRef.current = source;
    setPhase("streaming");

    source.onmessage = (e) => {
      let parsed: StreamEvent;
      try {
        parsed = JSON.parse(e.data) as StreamEvent;
      } catch {
        return;
      }
      onEvent(parsed);
      if (parsed.event === "done") {
        source.close();
        sourceRef.current = null;
      }
    };
    source.onerror = () => {
      source.close();
      sourceRef.current = null;
      setPhase((p) => (p === "streaming" ? "error" : p));
      setError((e) => e ?? "The stream ended unexpectedly.");
    };
  }

  /**
   * Approve or reject a parked action, then follow the run to its end.
   *
   * Takes the run id rather than reading it from the live run, because the history
   * rows act on their own runs: answering a question left by a run started in an
   * earlier session has to work, and that is exactly the case the buttons used to
   * miss. The buttons only existed on the live panel, so a parked action became
   * permanently unactionable once the operator reloaded — the run waited forever
   * for a person who could no longer see the question.
   *
   * The stream is reconnected with `resume=0` so it continues from the checkpoint
   * the decision just moved rather than re-running the step that was approved.
   */
  async function decide(runId: string, actionId: string, approve: boolean) {
    setDecisionBusy(true);
    setDecisionError(null);

    // The case id is passed because the server action needs it to revalidate the
    // page. Without it the gate the operator just answered stays on screen.
    const result = approve
      ? await approveActionAction(caseId, runId, actionId)
      : await rejectActionAction(caseId, runId, actionId);

    if (!result.ok) {
      setDecisionBusy(false);
      setDecisionError(result.error ?? "The decision was not applied.");
      return;
    }
    setPending(null);
    setDecided((previous) => new Set(previous).add(runId));
    router.refresh();

    const source = new EventSource(`/api/agent/${runId}/stream?resume=0`);
    sourceRef.current = source;
    setPhase("streaming");

    source.onmessage = (e) => {
      let parsed: StreamEvent;
      try {
        parsed = JSON.parse(e.data) as StreamEvent;
      } catch {
        return;
      }
      onEvent(parsed);
      if (parsed.event === "done") {
        source.close();
        sourceRef.current = null;
        router.refresh();
      }
    };
    source.onerror = () => {
      source.close();
      sourceRef.current = null;
      setPhase((p) => (p === "streaming" ? "error" : p));
    };
    setDecisionBusy(false);
  }

  const suggestions = [
    "Summarise what the evidence shows and cite every result you use.",
    "Check the metadata of every image for inconsistencies.",
    "Identify which images came from the same camera, if any.",
  ];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Ask the agent"
          subtitle="It reads and runs analyzers through its tools, and cites what it uses. Anything that changes the case waits for you."
        />
        <CardBody className="space-y-3">
          <div>
            <label htmlFor="objective" className="mb-1.5 block text-xs font-medium">
              Objective
            </label>
            <textarea
              id="objective"
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              rows={2}
              placeholder="What should it look at?"
              className="w-full rounded-md border border-border-default bg-surface-inset px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-tertiary focus:border-accent"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              disabled={
                objective.trim().length < 3 ||
                phase === "starting" ||
                phase === "streaming"
              }
              onClick={start}
            >
              {phase === "starting"
                ? "Starting…"
                : phase === "streaming"
                  ? "Running…"
                  : "Start run"}
            </Button>
            {phase === "streaming" || phase === "starting" ? (
              <Button
                variant="ghost"
                onClick={() => {
                  sourceRef.current?.close();
                  sourceRef.current = null;
                  setPhase("idle");
                }}
              >
                Disconnect
              </Button>
            ) : null}
          </div>

          {evidence.length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setObjective(s)}
                  className="rounded-full border border-border-default px-2.5 py-1 text-[11px] text-secondary transition-colors hover:bg-surface-2 hover:text-primary"
                >
                  {s}
                </button>
              ))}
            </div>
          ) : null}

          {steps.length > 0 ? (
            <ol className="flex flex-wrap gap-1.5">
              {steps.map((step) => (
                <li key={step}>
                  <Badge tone="info">{step.replace(/_/g, " ")}</Badge>
                </li>
              ))}
            </ol>
          ) : null}
        </CardBody>
      </Card>

      {error ? (
        <div role="alert">
          <Notice tone="alert" title="The run did not complete">
            {error}
          </Notice>
        </div>
      ) : null}

      {phase === "streaming" || phase === "awaiting" ? (
        <div role="status" aria-live="polite">
          <Card className="p-3">
            <IndeterminateBar label="Agent working — tokens appear as they arrive" />
          </Card>
        </div>
      ) : null}

      {pending ? (
        <ApprovalGate
          runId={run!.run_id}
          approval={pending}
          busy={decisionBusy}
          error={decisionError}
          onDecide={decide}
        />
      ) : null}

      {run ? <AgentOutput run={run} evidence={evidence} /> : null}

      {existingRuns === null ? (
        <Notice tone="alert" title="Could not load this case's agent runs">
          Runs already on this case exist and are not shown. That is a failed load, not
          an empty history.
        </Notice>
      ) : existingRuns.length > 0 ? (
        <RunHistory
          runs={existingRuns}
          decided={decided}
          evidence={evidence}
          activeRunId={run?.run_id ?? null}
          decisionBusy={decisionBusy}
          decisionError={decisionError}
          onDecide={decide}
        />
      ) : phase === "idle" && !run && !error ? (
        <Card>
          <EmptyState
            icon="◈"
            title="No runs yet"
            hint="Give the agent an objective. It will read the case, call analyzers, and cite every result it relies on — or decline if it cannot."
          />
        </Card>
      ) : null}
    </div>
  );
}

/**
 * The human-in-the-loop gate.
 *
 * One component, used both by the live console and by the run history. That is the
 * point: the buttons used to exist only on the live panel, so an action parked for
 * approval became permanently unactionable the moment the operator closed the tab
 * or reloaded — the run sat at `awaiting_approval` forever with no way to answer
 * it. Two copies would drift; one component used twice cannot.
 *
 * It shows the parked action's exact arguments before anything happens, because
 * approving a write is the one irreversible act in this interface and "approve
 * whatever it wanted" is not consent.
 */
function ApprovalGate({
  runId,
  approval,
  busy,
  error,
  onDecide,
}: {
  runId: string;
  approval: PendingApproval;
  busy: boolean;
  error: string | null;
  onDecide: (runId: string, actionId: string, approve: boolean) => void;
}) {
  return (
    <Card className="border-warn-border">
      <CardHeader
        title="Waiting for your decision"
        subtitle="The agent asked to change something in this case. Nothing has happened yet."
      />
      <CardBody className="space-y-3">
        <Notice tone="warn" title={`It wants to run: ${approval.tool}`}>
          {approval.reason || "No reason was given for this action."}
        </Notice>

        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-tertiary">
            Exactly what it will do
          </div>
          <pre className="max-h-48 overflow-auto rounded border border-border-subtle bg-surface-0 p-3 font-mono text-[11px] leading-relaxed">
            {JSON.stringify(approval.args, null, 2)}
          </pre>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button
            variant="success"
            disabled={busy}
            onClick={() => onDecide(runId, approval.action_id, true)}
          >
            {busy ? "Applying…" : "Approve"}
          </Button>
          <Button
            variant="danger"
            disabled={busy}
            onClick={() => onDecide(runId, approval.action_id, false)}
          >
            Reject
          </Button>
        </div>

        {error ? (
          <div role="alert">
            <Notice tone="alert">{error}</Notice>
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}

/**
 * Every run already on this case, newest first.
 *
 * A run is a durable record, so it is listed here and re-rendered on reload. Each
 * row states its outcome in words — "completed", "declined" — rather than relying
 * on a colour, and a declined run is marked as a considered refusal rather than a
 * failure, because the difference matters to whoever reads the case afterwards.
 */
function RunHistory({
  runs,
  evidence,
  activeRunId,
  decided,
  decisionBusy,
  decisionError,
  onDecide,
}: {
  runs: AgentRunRead[];
  evidence: EvidenceRead[];
  activeRunId: string | null;
  /** Runs whose parked action has already been answered this session. */
  decided: ReadonlySet<string>;
  decisionBusy: boolean;
  decisionError: string | null;
  onDecide: (runId: string, actionId: string, approve: boolean) => void;
}) {
  return (
    <Card>
      <CardHeader
        title={`Runs on this case (${runs.length})`}
        subtitle="Newest first. Every run is kept with its citations and tool-call audit."
      />
      <CardBody className="space-y-2">
        {runs.map((r) => {
          const declined = r.status === "declined";
          const failed = r.status === "failed";
          return (
            <details
              key={r.run_id}
              // Auto-expanded when the run is waiting on a person. Found by the
              // browser test: the gate rendered inside a collapsed row, so an
              // operator returning to a case had to go hunting for the question
              // that was waiting on them. A parked action that has to be discovered
              // is an action that goes unanswered.
              open={r.run_id === activeRunId || r.status === "awaiting_approval"}
              className="rounded-md border border-border-subtle"
            >
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2">
                <Badge
                  tone={failed ? "alert" : declined ? "warn" : "calm"}
                  title={declined ? r.refusal : r.error}
                >
                  {r.status}
                </Badge>
                <span className="text-xs font-medium">{r.objective}</span>
                <span className="ml-auto flex items-center gap-2 text-[11px] text-tertiary">
                  {r.claims.length > 0 ? (
                    <span className="text-accent">
                      {r.claims.length} claim{r.claims.length === 1 ? "" : "s"}
                    </span>
                  ) : null}
                  {r.tool_calls.length > 0 ? (
                    <span>{r.tool_calls.length} tool calls</span>
                  ) : null}
                  <span className="font-mono">{r.model}</span>
                  <time dateTime={r.created_at}>{formatDateTime(r.created_at)}</time>
                </span>
              </summary>
              <div className="border-t border-border-subtle px-3 py-2.5">
                {declined && r.refusal ? (
                  <Notice tone="warn" title="Declined to answer">
                    {r.refusal}
                  </Notice>
                ) : null}
                {failed && r.error ? (
                  <Notice tone="alert" title="The run failed">
                    {r.error}
                  </Notice>
                ) : null}

                {/* A parked action stays answerable after a reload. This is the
                    whole point of the HITL gate: the run waits for a person, and
                    the person may not be the one who started it. */}
                {!decided.has(r.run_id) &&
                r.status === "awaiting_approval" &&
                r.pending_action_id ? (
                  <ApprovalGate
                    runId={r.run_id}
                    approval={{
                      action_id: r.pending_action_id,
                      tool: r.pending_tool,
                      args: r.pending_args,
                      reason: "This tool changes case state, so it needs a human.",
                    }}
                    busy={decisionBusy}
                    error={decisionError}
                    onDecide={onDecide}
                  />
                ) : null}

                <AgentOutput run={r} evidence={evidence} />
              </div>
            </details>
          );
        })}
      </CardBody>
    </Card>
  );
}

/**
 * The run's output.
 *
 * The refusal is rendered first and in its own visual register, because "I could
 * not source this" is the most important thing the agent can say. A UI that
 * renders it as an empty report state teaches the operator to skip past it.
 */
function AgentOutput({
  run,
  evidence,
}: {
  run: AgentRunRead;
  evidence: EvidenceRead[];
}) {
  // Declared here rather than in the parent: the auto-scroll target belongs to the
  // component that renders the report, so the ref cannot be stale when the parent
  // re-renders without a run.
  const transcriptRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = transcriptRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [run.report]);

  const filename = (id: string) =>
    evidence.find((e) => e.id === id)?.filename ?? `${id.slice(0, 8)}…`;

  const statusTone: Record<string, "calm" | "warn" | "alert" | "neutral"> = {
    completed: "calm",
    finished: "calm",
    declined: "warn",
    failed: "alert",
  };

  return (
    <Card>
      <CardHeader
        title="Run output"
        subtitle={`${run.status} · step ${run.step.replace(/_/g, " ")}`}
        action={<Badge tone={statusTone[run.status] ?? "neutral"}>{run.status}</Badge>}
      />
      <CardBody className="space-y-4">
        {run.refusal ? (
          <Notice tone="warn" title="The agent declined to answer">
            {run.refusal}
            <span className="mt-1 block opacity-80">
              This is a considered refusal, not a failure. It means the agent could not
              find a source it was willing to cite, and declined to assert something
              unsupported.
            </span>
          </Notice>
        ) : null}

        {run.report ? (
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-tertiary">
              Report
            </h3>
            <div
              ref={transcriptRef}
              className="max-h-96 overflow-auto rounded-md border border-border-subtle bg-surface-0 p-3 text-xs leading-relaxed whitespace-pre-wrap"
            >
              {run.report}
            </div>
          </div>
        ) : null}

        {run.claims?.length ? (
          <div>
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-tertiary">
              Claims and their sources
            </h3>
            <ul className="space-y-3">
              {run.claims.map((claim, i) => (
                <li key={i} className="rounded-md border border-border-subtle p-3">
                  <p className="text-xs leading-relaxed">{claim.text}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {claim.citations?.length ? (
                      claim.citations.map((c, j) => (
                        /* The chip carries the analyzer, the score against its
                           own threshold, and the evidence it came from — because
                           a citation that names a file without saying what was
                           measured leaves the reader to guess at the claim. */
                        <span
                          key={j}
                          title={
                            `${c.analyzer} scored ${c.score.toFixed(4)} against a ` +
                            `threshold of ${c.threshold} · Tier ${c.tier} · ` +
                            `${c.model_version}`
                          }
                          className="inline-flex items-center gap-1 rounded border border-accent-border bg-accent-subtle px-1.5 py-0.5 text-[10px] text-accent"
                        >
                          <span className="font-mono font-medium">{c.analyzer}</span>
                          <span className="tabular-nums">{c.score.toFixed(3)}</span>
                          <span className="opacity-70">{filename(c.evidence_id)}</span>
                        </span>
                      ))
                    ) : (
                      /* A claim with no citation is rendered as an unsupported
                         claim, in the warning colour, not as an assertion. The
                         backend should refuse to produce one; if it ever does,
                         this is the last place it could be mistaken for a fact. */
                      <Badge tone="warn">no citation — not usable as a finding</Badge>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {run.tool_calls?.length ? <ToolCallLog calls={run.tool_calls} /> : null}

        {run.messages?.length ? (
          <details>
            <summary className="cursor-pointer text-xs font-medium">
              Transcript ({run.messages.length} turns)
            </summary>
            <ol className="mt-2 space-y-2">
              {run.messages.map((m, i) => (
                <li
                  key={i}
                  className={cx(
                    "rounded border p-2 text-[11px] leading-relaxed",
                    m.role === "tool"
                      ? "border-info-border bg-info-bg/40"
                      : "border-border-subtle bg-surface-2/40",
                  )}
                >
                  <div className="mb-1 font-mono text-[10px] uppercase tracking-wide text-tertiary">
                    {m.role}
                  </div>
                  <div className="whitespace-pre-wrap">{m.content}</div>
                </li>
              ))}
            </ol>
          </details>
        ) : null}
      </CardBody>
    </Card>
  );
}

/**
 * Tool calls, each collapsible.
 *
 * Open for failures and closed otherwise: a successful `get_case` is noise, and a
 * failed one is the single most useful thing in a transcript when something went
 * wrong. `args_hash` and `result_hash` are shown because those are what make a
 * tool call auditable — the point of the log is that it can be checked later.
 */
/** How a tool call reads. `pending` is not a flavour of `failed`. */
const CALL_STATUS: Record<
  AgentRunRead["tool_calls"][number]["status"],
  { label: string; tone: "calm" | "alert" | "warn" }
> = {
  ok: { label: "ok", tone: "calm" },
  failed: { label: "failed", tone: "alert" },
  // The agent has not done anything wrong -- it is waiting for a person.
  pending: { label: "awaiting approval", tone: "warn" },
};

function ToolCallLog({ calls }: { calls: AgentRunRead["tool_calls"] }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-tertiary">
        Tool calls ({calls.length})
      </h3>
      <ul className="space-y-1.5">
        {calls.map((call, i) => {
          const status = CALL_STATUS[call.status] ?? CALL_STATUS.failed;
          return (
            <li key={i}>
              {/* A failure opens itself; a pending call does not, because the gate
                above it is already the thing that needs attention. */}
              <details
                open={call.status === "failed"}
                className="rounded border border-border-subtle"
              >
                <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5">
                  <span className="font-mono text-xs font-medium">{call.tool}</span>
                  <Badge tone={status.tone}>{status.label}</Badge>
                  <span className="ml-auto font-mono text-[10px] text-tertiary">
                    {call.result_hash ? call.result_hash.slice(0, 10) : "no result yet"}
                  </span>
                </summary>
                <div className="space-y-1.5 border-t border-border-subtle px-2.5 py-2 text-[10px]">
                  {call.error ? (
                    <p
                      className={
                        call.status === "failed"
                          ? "font-mono text-alert"
                          : "font-mono text-warn"
                      }
                    >
                      {call.error}
                    </p>
                  ) : null}
                  <pre className="max-h-40 overflow-auto font-mono leading-relaxed text-tertiary">
                    {JSON.stringify(call.args, null, 1)}
                  </pre>
                  <p className="font-mono text-tertiary">
                    args {call.args_hash.slice(0, 16)}… · result{" "}
                    {call.result_hash ? `${call.result_hash.slice(0, 16)}…` : "not run"}
                  </p>
                </div>
              </details>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
