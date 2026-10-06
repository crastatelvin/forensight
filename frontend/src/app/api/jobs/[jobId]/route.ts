import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy one analysis job's status, for the polling loop.
 *
 * A separate route because polling needs to be an unauthenticated-*to-the-browser*
 * `fetch` that returns JSON the client can read the status out of — the full case
 * bundle is too heavy to poll every two seconds.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ jobId: string }> },
) {
  const { jobId } = await params;
  const jar = await cookies();

  try {
    const response = await fetch(
      `${API_URL}/analyses/jobs/${encodeURIComponent(jobId)}`,
      {
        headers: {
          accept: "application/json",
          cookie: jar.toString(),
          ...(jar.get("csrf_token")
            ? { "x-csrf-token": jar.get("csrf_token")!.value }
            : {}),
        },
        cache: "no-store",
      },
    );
    if (!response.ok) {
      return Response.json(
        { detail: `Job status unavailable (${response.status})` },
        { status: response.status },
      );
    }
    return Response.json(await response.json(), {
      headers: { "cache-control": "no-store" },
    });
  } catch {
    return Response.json({ detail: "Cannot reach the API." }, { status: 503 });
  }
}
