"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { API_URL, ApiError, apiUpload } from "@/lib/api";

/** JSON mutation against the API, returning a message instead of throwing. */
async function mutate<T>(
  path: string,
  init: { method: string; body?: unknown },
): Promise<{ ok: true; data: T } | { ok: false; error: string }> {
  const jar = await cookies();
  try {
    const data = await fetch(`${API_URL}${path}`, {
      method: init.method,
      headers: {
        "content-type": "application/json",
        cookie: jar.toString(),
        ...(jar.get("csrf_token")
          ? { "x-csrf-token": jar.get("csrf_token")!.value }
          : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
      cache: "no-store",
    });
    const text = await data.text();
    if (!data.ok) {
      let detail = `Request failed (${data.status})`;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed.detail === "string") detail = parsed.detail;
        else if (Array.isArray(parsed.detail) && parsed.detail[0]?.msg) {
          detail = parsed.detail[0].msg;
        }
      } catch {
        // Non-JSON error body; the status-based message above is fine.
      }
      return { ok: false, error: detail };
    }
    return { ok: true, data: (text ? JSON.parse(text) : null) as T };
  } catch {
    return { ok: false, error: "Cannot reach the server." };
  }
}

/** Create a case. */
export async function createCaseAction(
  _prev: { error: string } | null,
  formData: FormData,
): Promise<{ error: string } | null> {
  const number = String(formData.get("number") ?? "").trim();
  const title = String(formData.get("title") ?? "").trim();

  if (!number || !title) {
    return { error: "A case number and a title are both required." };
  }

  const result = await mutate<{ id: string }>("/cases", {
    method: "POST",
    body: {
      number,
      title,
      description: String(formData.get("description") ?? "").trim(),
      priority: String(formData.get("priority") ?? "normal"),
    },
  });

  if (!result.ok) {
    // The backend returns 409 with the constraint name for a duplicate number.
    // Naming that here is the difference between "fix the number" and
    // "something went wrong".
    return {
      error:
        result.error.includes("unique") || result.error.includes("number")
          ? `Case number "${number}" already exists. Case numbers must be unique.`
          : result.error,
    };
  }

  revalidatePath("/");
  redirect(`/cases/${result.data.id}`);
}

/** Upload evidence. Returns a message so the drop zone can stay put on failure. */
export async function uploadEvidenceAction(
  caseId: string,
  formData: FormData,
): Promise<{ ok: boolean; error?: string; filename?: string }> {
  const file = formData.get("file");
  if (!(file instanceof File) || file.size === 0) {
    return { ok: false, error: "Choose a file to upload." };
  }

  const form = new FormData();
  form.append("file", file);

  try {
    await apiUpload(`/cases/${caseId}/evidence`, form);
  } catch (error) {
    return {
      ok: false,
      error:
        error instanceof ApiError
          ? error.message
          : "Upload failed. Check that the server is running.",
    };
  }

  revalidatePath(`/cases/${caseId}`);
  return { ok: true, filename: file.name };
}

/** Queue an analysis for one piece of evidence. */
export async function runAnalysisAction(
  caseId: string,
  input: { module: string; evidenceId: string; params?: Record<string, unknown> },
): Promise<{ ok: boolean; error?: string; jobId?: string; cached?: boolean }> {
  const result = await mutate<{ id: string; cached?: boolean }>(
    `/cases/${caseId}/analyses`,
    {
      method: "POST",
      body: {
        module: input.module,
        evidence_id: input.evidenceId,
        params: input.params ?? {},
      },
    },
  );
  if (!result.ok) return { ok: false, error: result.error };
  revalidatePath(`/cases/${caseId}`);
  return { ok: true, jobId: result.data.id, cached: result.data.cached };
}

/** Move a case through its workflow. */
export async function transitionCaseAction(
  caseId: string,
  to: string,
): Promise<{ ok: boolean; error?: string }> {
  const result = await mutate(`/cases/${caseId}/transition`, {
    method: "POST",
    body: { to },
  });
  revalidatePath(`/cases/${caseId}`);
  return result.ok ? { ok: true } : { ok: false, error: result.error };
}

/** Add a finding. */
/**
 * Create a finding.
 *
 * Citations go in the `citations` field, not into the body text. They used to be
 * pasted into the body as a hand-built "Based on:" block, which duplicated the
 * citation table and could disagree with it — the body would assert a basis the
 * citations did not support, and a report would then print both. The API refuses a
 * citation it cannot resolve (422), so a wrong reference now fails loudly instead of
 * being flattened into prose.
 */
export async function createFindingAction(
  caseId: string,
  input: { title: string; body: string; citations?: string[] },
): Promise<{ ok: boolean; error?: string }> {
  if (!input.title.trim()) return { ok: false, error: "A finding needs a title." };
  const result = await mutate(`/cases/${caseId}/findings`, {
    method: "POST",
    body: {
      title: input.title.trim(),
      body: input.body,
      citations: input.citations ?? [],
    },
  });
  revalidatePath(`/cases/${caseId}`);
  return result.ok ? { ok: true } : { ok: false, error: result.error };
}
