/**
 * Types mirroring the backend's OpenAPI schemas.
 *
 * Hand-written rather than generated from `/openapi.json`. A generator would add
 * an `openapi-typescript` dev dependency, a generation step, and a check that the
 * checked-in file matches the live schema — all to produce the same seven shapes
 * below. They are written against `components.schemas` and each carries the schema
 * it mirrors, so a drift shows up as a type error at the call site.
 *
 * What this file deliberately does *not* model: `payload` on an analysis result.
 * Every analyzer ships a different body there (see `src/lib/explain.ts`), so it is
 * `Record<string, unknown>` and each module narrows it itself. Pretending to a
 * single union would be a lie the compiler cannot catch.
 */

export type Role = "admin" | "officer" | "expert" | "reviewer";

export interface UserRead {
  id: string;
  email: string;
  role: Role;
  is_active: boolean;
}

/** `CaseRead` */
export interface CaseRead {
  id: string;
  number: string;
  title: string;
  description: string;
  status: string;
  owner_id: string;
  assigned_to: string | null;
  priority: string;
  due_at: string | null;
  created_at: string;
  updated_at: string;
  /**
   * The status values this case may legally move to.
   *
   * Served by the API rather than recomputed here. The workflow state machine has
   * exactly one owner, and a client copy of it would offer a move the API then
   * rejects with 409 — which a reviewer reads as a broken case rather than a stale
   * button. Empty means terminal, which is a real answer and not a missing one.
   */
  allowed_transitions: string[];
}

/** `EvidenceRead` */
export interface EvidenceRead {
  id: string;
  case_id: string;
  filename: string;
  sha256: string;
  content_type: string;
  size_bytes: number;
  created_at: string;
}

/** `CustodyEntry` */
export interface CustodyEntry {
  id: string;
  action: string;
  actor_id: string | null;
  occurred_at: string;
  detail?: string;
}

/** `AnalysisResultRead` */
export interface AnalysisResultRead {
  id: string;
  job_id: string;
  evidence_id: string;
  module: string;
  score: number;
  threshold: number;
  model_version: string;
  tier: string;
  disclaimer: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/**
 * `AnalysisJobRead`, narrowed to the fields the UI needs.
 *
 * `status` is the set that matters: `not_applicable` is a first-class terminal
 * state meaning the technique could not be measured, and it must never be rendered
 * as a score of zero. See `src/lib/explain.ts`.
 */
export interface AnalysisJobRead {
  id: string;
  case_id: string;
  evidence_id: string;
  module: string;
  status: "queued" | "running" | "done" | "failed" | "not_applicable" | string;
  error: string;
  attempts: number;
  created_at: string;
}

/** `ModuleRead` */
export interface ModuleRead {
  name: string;
  version: string;
  tier: string;
  threshold: number;
  requires_gpu: boolean;
  accepts: string[];
  model_key: string | null;
  available: boolean;
  unavailable_reason: string;
}

/**
 * One analyzer result a finding rests on.
 *
 * A snapshot, not a live join: the analyzer's identity and its refusal state are
 * recorded when the finding is written, so a report generated after the analysis has
 * been re-run still describes the result the finding was actually drawn from.
 */
export interface FindingCitationRead {
  result_id: string;
  evidence_id: string;
  module: string;
  score: number;
  threshold: number;
  tier: string;
  model_version: string;
  not_applicable: boolean;
  cited_at: string;
}

/** `FindingRead` */
export interface FindingRead {
  id: string;
  case_id: string;
  title: string;
  body: string;
  status: string;
  created_by: string;
  created_at: string;
  /** Present so sourcing is visible wherever a finding appears, not only in a PDF. */
  citations: FindingCitationRead[];
  /** Mirrors the API's own property; a finding with none is a bare assertion. */
  is_sourced: boolean;
}

/** `TimelineEventRead` */
export interface TimelineEventRead {
  id: string;
  case_id: string;
  occurred_at: string;
  kind: string;
  label: string;
  detail: string;
}

/** `EntityRead` */
export interface EntityRead {
  id: string;
  case_id: string;
  kind: string;
  label: string;
  attributes: Record<string, unknown>;
}

/** `AuditEntryRead` */
export interface AuditEntryRead {
  id: string;
  actor_id: string | null;
  action: string;
  target: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/** `GET /capabilities` */
export interface CapabilityRead {
  name: string;
  available: boolean;
  requires_gpu: boolean;
  version: string;
  reason?: string;
  /** The tier this module's results carry. Absent on an older backend. */
  tier?: string;
}

export interface TierMeaningRead {
  tier: string;
  label: string;
  meaning: string;
}

export interface CapabilitiesResponse {
  gpu_available: boolean;
  capabilities: CapabilityRead[];
}

/** `GET /agent/tools` */
export interface AgentToolRead {
  name: string;
  description: string;
  state_changing: boolean;
}

/** `ToolCallRead` */
export interface ToolCallRead {
  tool: string;
  args: Record<string, unknown>;
  args_hash: string;
  result_hash: string;
  ok: boolean;
  error: string;
  approved_action_id: string;
  created_at: string;
}

/**
 * A citation the agent attached to a claim.
 *
 * Mirrors `Citation.as_dict` on the backend — deliberately delegated there rather
 * than hand-written in two places. These fields were originally invented as
 * `module`/`locator`/`quote`, none of which exist, so a client typed against the
 * invention rather than the contract and would have rendered nothing.
 */
export interface CitationRead {
  result_id: string;
  evidence_id: string;
  analyzer: string;
  score: number;
  threshold: number;
  model_version: string;
  tier: string;
}

/** A claim and the results it rests on. */
export interface ClaimRead {
  text: string;
  citations: CitationRead[];
}

/**
 * One tool call as recorded in the run's checkpoint state.
 *
 * `status` is three-valued on purpose. `ok` is a boolean because a call's outcome
 * lives on its result, but a call whose action is parked for approval has produced
 * no result at all — and collapsing that into `ok: false` renders an action still
 * waiting for a decision as a *failed* one. That is a false statement about the
 * audit trail, which is the one thing in this interface that must not be
 * approximate.
 */
export interface AgentToolCallRead {
  tool: string;
  call_id: string;
  args: Record<string, unknown>;
  args_hash: string;
  status: "ok" | "failed" | "pending";
  ok: boolean;
  error: string;
  result_hash: string;
  /** Set when this call is the parked action awaiting a human decision. */
  approved_action_id: string;
}

/**
 * An agent run, as the API serves it.
 *
 * The identifier is `run_id`, not `id` — the run payload is flat and predates the
 * REST convention, so the frontend follows it rather than the frontend wishing it
 * were different. Note also that the pending action is three flat fields
 * (`pending_action_id`, `pending_tool`, `pending_args`) rather than a nested
 * object, and `claims`/`citations`/`tool_calls`/`messages` were absent entirely
 * until Phase 7, which meant the agent's provenance was invisible to every client.
 */
export interface AgentRunRead {
  run_id: string;
  case_id: string;
  thread_id: string;
  status: string;
  step: string;
  objective: string;
  report: string;
  refusal: string;
  error: string;
  model: string;
  pending_action_id: string;
  pending_tool: string;
  pending_args: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  claims: ClaimRead[];
  citations: CitationRead[];
  tool_calls: AgentToolCallRead[];
  messages: Array<{ role: string; content: string }>;
}

/** An action parked for human approval, from the `awaiting_approval` SSE event. */
export interface PendingApproval {
  action_id: string;
  tool: string;
  args: Record<string, unknown>;
  reason: string;
}
