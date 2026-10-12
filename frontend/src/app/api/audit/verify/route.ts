import { cookies } from "next/headers";
import { API_URL } from "@/lib/api";

/**
 * Proxy the hash-chain verification, so the case workspace can show a verdict a user
 * watched happen rather than a claim the page asserts.
 *
 * This route was missing. `ChainStatus` has always called `/api/audit/verify`, which
 * 404'd through the Next server, and the `catch` turned that into the flat message
 * "Could not reach the server to verify the chain." — so the button could never report
 * anything but an error, on every case, for every user. The backend endpoint existed and
 * returned `{"valid": true, "entries": 19}` the whole time.
 *
 * Found by clicking the button in the running application, not by a test: the backend
 * suite covers `/cases/audit/verify` directly, and the Playwright suite never clicked
 * this control. Both halves passed while the feature was broken.
 *
 * The browser's cookie is forwarded because the API scopes the chain to what the caller
 * may see; the CSRF header is not needed on a GET, but is sent when present to match the
 * other routes.
 */
export async function GET() {
  const jar = await cookies();

  try {
    const response = await fetch(`${API_URL}/cases/audit/verify`, {
      headers: {
        accept: "application/json",
        cookie: jar.toString(),
        ...(jar.get("csrf_token")
          ? { "x-csrf-token": jar.get("csrf_token")!.value }
          : {}),
      },
      cache: "no-store",
    });
    if (!response.ok) {
      return Response.json(
        { detail: `Chain verification unavailable (${response.status})` },
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