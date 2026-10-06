import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy evidence bytes from the API.
 *
 * The browser cannot call the API directly: the access token is an HttpOnly cookie
 * and every request would need it in a header the page cannot read. Proxying
 * server-side also means one CORS policy (none) and one place where an API error
 * becomes a sensible HTTP status.
 *
 * Passes through only the headers that matter — content type, length, disposition
 * — and never copies the API's `Set-Cookie` or hop-by-hop headers.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ evidenceId: string }> },
) {
  const { evidenceId } = await params;
  const inline = request.nextUrl.searchParams.get("disposition") === "inline";

  const jar = await cookies();
  let upstream: Response;
  try {
    upstream = await fetch(
      `${API_URL}/cases/evidence/${encodeURIComponent(evidenceId)}/download` +
        (inline ? "?disposition=inline" : ""),
      {
        headers: {
          accept: "*/*",
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
    let detail = `Evidence unavailable (${upstream.status})`;
    try {
      const parsed = await upstream.json();
      if (typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // Keep the status-based message.
    }
    return Response.json(
      { detail, kind: evidenceFailureKind(upstream.status) },
      { status: upstream.status },
    );
  }

  const contentType =
    upstream.headers.get("content-type") ?? "application/octet-stream";
  const filename = contentDispositionFilename(
    upstream.headers.get("content-disposition"),
  );

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": contentType,
      ...(filename ? { "content-disposition": filename } : {}),
      // Evidence is immutable, so the same bytes are safe to reuse for an hour.
      "cache-control": "private, max-age=3600, immutable",
    },
  });
}

/**
 * Name the failure, so the viewer can say the right thing about it.
 *
 * The distinction is the whole point. `410` means the record exists and its bytes
 * do not — an integrity problem needing escalation. `409` means the bytes are
 * there and do not match the recorded digest — also an integrity problem, and a
 * different one. `404` means there is no such evidence. Collapsing these into
 * "unavailable" would hide exactly the conditions an investigator needs to see.
 */
function evidenceFailureKind(
  status: number,
): "missing_bytes" | "integrity" | "absent" | "unknown" {
  if (status === 410) return "missing_bytes";
  if (status === 409) return "integrity";
  if (status === 404) return "absent";
  return "unknown";
}

/**
 * Recover the original filename from a `Content-Disposition` header.
 *
 * The API builds that header from the stored filename, so an attacker who
 * uploaded a file called `x"; filename="evil.html` could otherwise inject header
 * parameters. Only the quoted `filename=` is kept, and CR/LF is stripped so it
 * cannot split the response.
 */
function contentDispositionFilename(header: string | null): string | null {
  if (!header) return null;
  const match = /filename="([^"]*)"/.exec(header);
  if (!match) return null;
  return `attachment; filename="${match[1].replace(/[\r\n]/g, "")}"`;
}
