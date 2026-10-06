"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import type { EvidenceRead, AgentRunRead, PendingApproval } from "@/lib/types";
import {
  startRunAction,
  approveActionAction,
  rejectActionAction,
} from "@/app/cases/[caseId]/agent-actions";
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
}: {
  caseId: string;
  evidence: EvidenceRead[];
}) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [objective, setObjective] = useState("");
  const [run, setRun] = useState<AgentRunRead | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<string[]>([]);
  const [decisionBusy, setDecisionBusy] = useState(false);
  const [decisionError, setDecisionError] = useState<string | null>(null);
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

  async function decide(actionId: string, approve: boolean) {
    setDecisionBusy(true);
    setDecisionError(null);
    const result = approve
      ? await approveActionAction(run!.id, actionId)
      : await rejectActionAction(run!.id, actionId);
    setDecisionBusy(false);

    if (!result.ok) {
      setDecisionError(result.error ?? "The decision was not applied.");
      return;
    }
    setPending(null);
    // Reconnect with resume=false so the stream continues from the checkpoint the
    // decision just moved, rather than re-running the step that was approved.
    const source = new EventSource(`/api/agent/${run!.id}/stream?resume=0`);
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
    };
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
        <Card className="border-warn-border">
          <CardHeader
            title="Waiting for your decision"
            subtitle="The agent asked to change something in this case. Nothing has happened yet."
          />
          <CardBody className="space-y-3">
            <Notice tone="warn" title={`It wants to run: ${pending.tool}`}>
              {pending.reason || "No reason was given for this action."}
            </Notice>

            <pre className="max-h-48 overflow-auto rounded border border-border-subtle bg-surface-0 p-3 font-mono text-[11px] leading-relaxed">
              {JSON.stringify(pending.args, null, 2)}
            </pre>

            <div className="flex flex-wrap gap-2">
              <Button
                variant="success"
                disabled={decisionBusy}
                onClick={() => decide(pending.action_id, true)}
              >
                {decisionBusy ? "Applying…" : "Approve"}
              </Button>
              <Button
                variant="danger"
                disabled={decisionBusy}
                onClick={() => decide(pending.action_id, false)}
              >
                Reject
              </Button>
            </div>

            {decisionError ? (
              <div role="alert">
                <Notice tone="alert">{decisionError}</Notice>
              </div>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      {run ? (
        <AgentOutput run={run} evidence={evidence} />
      ) : phase === "idle" && !error ? (
        <Card>
          <EmptyState
            icon="◈"
            title="No run yet"
            hint="Give the agent an objective. It will read the case, call analyzers, and cite every result it relies on — or decline if it cannot."
          />
        </Card>
      ) : null}
    </div>
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
                        <span
                          key={j}
                          title={c.quote}
                          className="inline-flex items-center gap-1 rounded border border-accent-border bg-accent-subtle px-1.5 py-0.5 text-[10px] text-accent"
                        >
                          <span className="font-mono font-medium">{c.module}</span>
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
function ToolCallLog({ calls }: { calls: AgentRunRead["tool_calls"] }) {
  return (
    <div>
      <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-tertiary">
        Tool calls ({calls.length})
      </h3>
      <ul className="space-y-1.5">
        {calls.map((call, i) => (
          <li key={i}>
            <details open={!call.ok} className="rounded border border-border-subtle">
              <summary className="flex cursor-pointer items-center gap-2 px-2.5 py-1.5">
                <span className="font-mono text-xs font-medium">{call.tool}</span>
                <Badge tone={call.ok ? "calm" : "alert"}>
                  {call.ok ? "ok" : "failed"}
                </Badge>
                {call.approved_action_id ? (
                  <Badge tone="warn">human-approved</Badge>
                ) : null}
                <span className="ml-auto font-mono text-[10px] text-tertiary">
                  {call.result_hash.slice(0, 10)}
                </span>
              </summary>
              <div className="space-y-1.5 border-t border-border-subtle px-2.5 py-2 text-[10px]">
                {call.error ? (
                  <p className="font-mono text-alert">{call.error}</p>
                ) : null}
                <pre className="max-h-40 overflow-auto font-mono leading-relaxed text-tertiary">
                  {JSON.stringify(call.args, null, 1)}
                </pre>
                <p className="font-mono text-tertiary">
                  args {call.args_hash.slice(0, 16)}… · result{" "}
                  {call.result_hash.slice(0, 16)}…
                </p>
              </div>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
