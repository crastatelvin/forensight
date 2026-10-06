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

/** `FindingRead` */
export interface FindingRead {
  id: string;
  case_id: string;
  title: string;
  body: string;
  status: string;
  created_by: string;
  created_at: string;
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
 * A citation the agent attached to a claim, from the stored checkpoint.
 *
 * This is the honesty mechanism: a claim without a resolvable citation is not
 * rendered as an assertion. See `Citation` in `app/agent/types.py`.
 */
export interface CitationRead {
  evidence_id: string;
  result_id: string;
  module: string;
  locator: string;
  quote: string;
}

/** A claim and the results it rests on. */
export interface ClaimRead {
  text: string;
  citations: CitationRead[];
}

/**
 * `RunRead` from the agent router, plus the checkpoint fields the console renders.
 *
 * The SSE stream's `start` and `done` events both carry this shape, so the console
 * renders identically whether it connected mid-run or watched it from the start.
 */
export interface AgentRunRead {
  id: string;
  case_id: string;
  status: string;
  step: string;
  objective: string;
  report: string;
  refusal: string;
  error: string;
  pending: {
    action_id: string;
    tool: string;
    args: Record<string, unknown>;
    reason: string;
    requested_at: string;
  } | null;
  claims: ClaimRead[];
  tool_calls: ToolCallRead[];
  citations: CitationRead[];
  messages: Array<{ role: string; content: string }>;
}

/** An action parked for human approval, from the `awaiting_approval` SSE event. */
export interface PendingApproval {
  action_id: string;
  tool: string;
  args: Record<string, unknown>;
  reason: string;
}
