import { cookies } from "next/headers";
import { NextRequest } from "next/server";
import { API_URL } from "@/lib/api";

/**
 * Proxy the agent's SSE stream.
 *
 * A route handler rather than a direct browser `EventSource` because the session
 * cookie is HttpOnly: the browser cannot add an `Authorization` header to an
 * `EventSource`, so pointing it straight at the API would be an unauthenticated
 * 401. Proxying also lets this route re-frame the events for the client.
 *
 * The body is piped rather than buffered. Buffering would defeat the entire point
 * — the client would receive the whole run at once, after it had already finished,
 * and the console would look like it had never streamed anything.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  const resume = request.nextUrl.searchParams.get("resume") !== "0";

  const jar = await cookies();
  let upstream: Response;
  try {
    upstream = await fetch(
      `${API_URL}/agent/runs/${encodeURIComponent(runId)}/stream?resume=${resume}`,
      {
        headers: {
          accept: "text/event-stream",
          cookie: jar.toString(),
          ...(jar.get("csrf_token")
            ? { "x-csrf-token": jar.get("csrf_token")!.value }
            : {}),
        },
        cache: "no-store",
      },
    );
  } catch {
    return Response.json(
      { detail: "Cannot reach the ForenSight API." },
      { status: 503 },
    );
  }

  if (!upstream.ok || !upstream.body) {
    let detail = `The run could not be streamed (${upstream.status})`;
    try {
      const parsed = await upstream.json();
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // Keep the status-based message.
    }
    return Response.json({ detail }, { status: upstream.status });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream",
      // Nginx buffers `text/event-stream` by default, which turns a live stream
      // into a batch delivered at the end. Both headers are required to stop it.
      "cache-control": "no-cache, no-transform",
      "x-accel-buffering": "no",
      connection: "keep-alive",
    },
  });
}
