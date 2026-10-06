import { NextRequest } from "next/server";
import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy the ELA overlay PNG from the API.
 *
 * Same reason as the download route: the session cookie is HttpOnly, so the
 * browser cannot authenticate this request itself.
 *
 * 422 is forwarded rather than flattened to 500, and that distinction is the whole
 * point of this route existing separately. A 422 means "ELA cannot apply to this
 * file — it is a PNG, or too small", which is information; a 500 or an opaque
 * failure would make a technique's inapplicability look like a broken feature,
 * which is exactly how "the check ran and found nothing" gets mistaken for a
 * result.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ evidenceId: string }> },
) {
  const { evidenceId } = await params;

  const jar = await cookies();
  let upstream: Response;
  try {
    upstream = await fetch(
      `${API_URL}/cases/evidence/${encodeURIComponent(evidenceId)}/ela.png`,
      {
        headers: {
          accept: "image/png",
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
    let detail = `ELA overlay unavailable (${upstream.status})`;
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
      "content-type": "image/png",
      "cache-control": "private, max-age=300",
    },
  });
}
