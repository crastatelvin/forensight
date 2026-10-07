import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy a signature check.
 *
 * Multipart is forwarded as-is rather than re-encoded: the PDF can be tens of
 * megabytes, and reading it into memory here only to serialise it again would add a
 * copy for no benefit. `duplex` is not needed because nothing is streamed *out*.
 *
 * The size limit is the API's, not this route's. Duplicating a limit in two places
 * is how they disagree, and the API is the one that must not be wrong.
 */
export async function POST(request: Request) {
  const jar = await cookies();

  // A malformed body is rejected here rather than forwarded: the API would answer
  // with a validation error that says nothing about which file was at fault.
  const form = await request.formData().catch(() => null);
  if (!form || !(form.get("pdf") instanceof File)) {
    return Response.json({ detail: "Choose a PDF report to check." }, { status: 400 });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${API_URL}/reports/verify`, {
      method: "POST",
      headers: {
        accept: "application/json",
        cookie: jar.toString(),
        ...(jar.get("csrf_token")
          ? { "x-csrf-token": jar.get("csrf_token")!.value }
          : {}),
      },
      body: form,
    });
  } catch {
    return Response.json(
      { detail: "Cannot reach the ForenSight API." },
      { status: 503 },
    );
  }

  const text = await upstream.text();
  let payload: unknown = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    // An HTML error page from a proxy. Passed through as text rather than pretended
    // to be a verdict, because a reader must never be shown "no problems found" as
    // a result of a proxy returning a login page.
    return Response.json(
      { detail: `The check failed unexpectedly (${upstream.status}).` },
      { status: upstream.status },
    );
  }

  return new Response(JSON.stringify(payload), {
    status: upstream.status,
    headers: { "content-type": "application/json" },
  });
}
