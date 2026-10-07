import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy a generated report.
 *
 * A route handler for the same reason as the SSE stream: the access token is an
 * HttpOnly cookie, so the browser cannot authenticate a direct fetch to the API.
 *
 * **The PDF is buffered, deliberately.** Every other proxy here streams, and the
 * instinct is to stream this too. Streaming a PDF would let the browser begin
 * rendering a document that later turned out to be truncated or corrupt — the file
 * would open, look mostly right, and be worthless as a record. A report is read as
 * a whole document or not at all, so it is passed as a whole.
 *
 * Only two response headers are forwarded. `Content-Disposition` carries the
 * filename and `X-Report-SHA256` the document's own digest; forwarding the
 * upstream header set wholesale would pass through `set-cookie` and hop-by-hop
 * headers that do not belong on this origin.
 */
export async function GET(
  _request: Request,
  { params }: { params: Promise<{ caseId: string }> },
) {
  const { caseId } = await params;
  const jar = await cookies();

  let upstream: Response;
  try {
    upstream = await fetch(
      `${API_URL}/reports/cases/${encodeURIComponent(caseId)}.pdf`,
      {
        headers: {
          accept: "application/pdf",
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

  if (!upstream.ok) {
    // A 503 here usually means no signing key is configured. The API's message names
    // the provisioning command, which is the single most useful thing to pass
    // through unchanged rather than replacing with a generic failure.
    let detail = `The report could not be generated (${upstream.status}).`;
    try {
      const parsed = await upstream.json();
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      /* keep the status-based message */
    }
    return Response.json({ detail }, { status: upstream.status });
  }

  const headers = new Headers({
    "content-type": "application/pdf",
    "content-disposition":
      upstream.headers.get("content-disposition") ??
      'attachment; filename="case-report.pdf"',
  });
  const sha = upstream.headers.get("x-report-sha256");
  if (sha) headers.set("x-report-sha256", sha);
  const manifest = upstream.headers.get("x-manifest-sha256");
  if (manifest) headers.set("x-manifest-sha256", manifest);

  return new Response(await upstream.arrayBuffer(), { status: 200, headers });
}
