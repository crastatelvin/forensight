"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Agent mutations: start a run, approve or reject a parked action, resume.
 *
 * All of these change server state, so each goes through the cookie + CSRF pair
 * the backend requires. They return `{ok, error}` rather than throwing, because
 * the console has to render the refusal in place — an agent that hits an error
 * should not lose the transcript it has already streamed.
 */

async function post<T>(path: string, body?: unknown): Promise<T> {
  const jar = await cookies();
  const response = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      cookie: jar.toString(),
      ...(jar.get("csrf_token")
        ? { "x-csrf-token": jar.get("csrf_token")!.value }
        : {}),
    },
    body: JSON.stringify(body ?? {}),
    cache: "no-store",
  });
  const text = await response.text();
  let parsed: unknown = null;
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { detail: text.slice(0, 200) };
    }
  }
  if (!response.ok) {
    const detail = (parsed as { detail?: unknown })?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `Request failed (${response.status})`,
    );
  }
  return parsed as T;
}

/** Begin an investigative run. */
export async function startRunAction(
  caseId: string,
  objective: string,
): Promise<{ ok: true; runId: string } | { ok: false; error: string }> {
  const trimmed = objective.trim();
  if (trimmed.length < 3) {
    return { ok: false, error: "Say what you want investigated." };
  }
  try {
    const run = await post<{ id: string }>(`/agent/cases/${caseId}/runs`, {
      objective: trimmed,
    });
    revalidatePath(`/cases/${caseId}`);
    return { ok: true, runId: run.id };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "The run could not be started.",
    };
  }
}

/**
 * Approve a parked state-changing action and let the agent continue.
 *
 * `revalidatePath` is not optional here and its absence was a real bug: this was the
 * only mutating action in the app that did not call it, so approving left the case
 * page's server render stale. The gate the operator had just answered stayed on
 * screen — with the run already advanced past it in the database — until a manual
 * reload. Answering a question and still being shown that same question is the worst
 * possible outcome for a HITL control: it reads as "it did not work", and the
 * operator's next move is to approve it again.
 *
 * `router.refresh()` on the client was not enough on its own, which is exactly why
 * every other mutation here pairs it with a revalidation.
 */
export async function approveActionAction(
  caseId: string,
  runId: string,
  actionId: string,
  reason = "",
): Promise<{ ok: boolean; error?: string }> {
  try {
    await post(`/agent/runs/${runId}/approve`, { action_id: actionId, reason });
    revalidatePath(`/cases/${caseId}`);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Approval failed.",
    };
  }
}

/** Reject a parked action. The action is discarded, not queued. */
/** Reject a parked action. Revalidates for the same reason as `approve`. */
export async function rejectActionAction(
  caseId: string,
  runId: string,
  actionId: string,
  reason = "",
): Promise<{ ok: boolean; error?: string }> {
  try {
    await post(`/agent/runs/${runId}/reject`, { action_id: actionId, reason });
    revalidatePath(`/cases/${caseId}`);
    return { ok: true };
  } catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : "Rejection failed.",
    };
  }
}
