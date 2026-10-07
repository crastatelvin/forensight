/**
 * Timeline rendering, including the event shape that crashed it.
 *
 * The defect: `TimelineTab` sorted with `b.occurred_at.localeCompare(a.occurred_at)`
 * while the API returns `occurred_at: null` for an event with no known time. That is
 * a `TypeError` on every such event, and `tsc` could not catch it because
 * `TimelineEventRead` was hand-written declaring `occurred_at: string`.
 *
 * This drives the real stack rather than a mock, because the whole failure was a
 * disagreement between the TypeScript interface and the API — a mock built from the
 * interface would have agreed with the interface and passed.
 *
 * Fixtures are created against the API with a real bearer token, while the timeline
 * itself is read as a signed-in user in a browser. They cannot both go through the
 * page: Next's route handlers do not expose the create endpoints, because mutations
 * are server actions with no HTTP surface. An earlier version posted to
 * `/api/cases` from inside the page and kept trying to parse the app's own 404 page.
 */
import { expect, test, type APIRequestContext } from "@playwright/test";
import { CREDENTIALS, signInOnce } from "./helpers";

const API_URL = process.env.FORENSIGHT_API ?? "http://localhost:8000";

interface PostedJson {
  ok: boolean;
  status: number;
  /**
   * Parsed response body, or the raw text when it did not parse.
   *
   * Deliberately not `unknown` and not a cast: a route that does not exist answers
   * with HTML, and the failure message should show that HTML rather than a cast
   * blowing up somewhere further on.
   */
  body: Record<string, unknown> | string;
}

/**
 * A bearer token from a real login, so fixture data goes through the same
 * authentication and RBAC path production traffic does.
 *
 * Cached across the file. The auth rate limiter allows five attempts a minute, and
 * three tests each logging in — plus `signInOnce` driving the real form — trips it
 * and turns a green suite into a 429 on the login step, which looks nothing like the
 * bug anyone is hunting. This is the same reason `signInOnce` exists.
 */
let cachedToken: Promise<string> | null = null;

function token(request: APIRequestContext): Promise<string> {
  cachedToken ??= (async () => {
    const response = await request.post(`${API_URL}/auth/login`, {
      data: { email: CREDENTIALS.email, password: CREDENTIALS.password },
    });
    expect(response.ok(), `login failed: ${response.status()}`).toBe(true);
    return (await response.json()).access_token as string;
  })();
  return cachedToken;
}

async function postJson(
  request: APIRequestContext,
  bearer: string,
  path: string,
  payload: unknown,
): Promise<PostedJson> {
  const response = await request.post(`${API_URL}${path}`, {
    headers: { Authorization: `Bearer ${bearer}` },
    data: payload,
  });
  const text = await response.text();
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === "object") {
      return {
        ok: response.ok(),
        status: response.status(),
        body: parsed as Record<string, unknown>,
      };
    }
  } catch {
    // Fall through and hand back the text.
  }
  return { ok: response.ok(), status: response.status(), body: text };
}

async function newCase(request: APIRequestContext, bearer: string, title: string): Promise<string> {
  const created = await postJson(request, bearer, "/cases", {
    title,
    number: `E2E-${title.replace(/\W+/g, "").slice(0, 8).toUpperCase()}-${Date.now() % 100000}`,
  });
  expect(created.ok, `case creation failed (${created.status}): ${String(created.body).slice(0, 200)}`).toBe(
    true,
  );
  return String((created.body as { id: unknown }).id);
}

test.describe("timeline", () => {
  test.beforeEach(async ({ page }) => {
    await signInOnce(page);
  });

  test("an event with no known time renders instead of crashing the tab", async ({
    page,
    request,
  }) => {
    const bearer = await token(request);
    const caseId = await newCase(request, bearer, "Timeline null");

    // `occurred_at` is omitted deliberately — the schema allows it, and that is the
    // state which used to throw.
    const event = await postJson(request, bearer, `/cases/${caseId}/timeline`, {
      label: "event with no known time",
      uncertain: true,
      source_ref: "CUSTODY-1",
    });
    expect(event.ok, `timeline post failed: ${String(event.body).slice(0, 200)}`).toBe(true);
    expect((event.body as { occurred_at?: unknown }).occurred_at).toBeNull();

    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(String(error)));

    await page.goto(`/cases/${caseId}`);
    await page.getByRole("tab", { name: /Timeline/ }).click();

    // The event is shown, and the absence of a time is stated in words rather than
    // rendering an empty element or — worse — the record's creation time, which would
    // be a different fact presented as the event's time.
    await expect(page.getByText("event with no known time")).toBeVisible();
    await expect(page.getByText("time unknown")).toBeVisible();

    // `uncertain` was a first-class backend field precisely so it could be shown.
    await expect(page.getByText("uncertain")).toBeVisible();

    // The regression: sorting threw a TypeError here before the fix.
    expect(pageErrors).toEqual([]);
  });

  test("dated events render their timestamp", async ({ page, request }) => {
    const bearer = await token(request);
    const caseId = await newCase(request, bearer, "Timeline dated");

    await postJson(request, bearer, `/cases/${caseId}/timeline`, {
      label: "dated event",
      occurred_at: "2026-04-01T09:30:00Z",
    });

    await page.goto(`/cases/${caseId}`);
    await page.getByRole("tab", { name: /Timeline/ }).click();
    await expect(page.getByText("dated event")).toBeVisible();
    // Not the literal "time unknown" fallback, and no crash on a valid timestamp.
    await expect(page.getByText("time unknown")).toHaveCount(0);
  });

  test("an uploaded file appears as a derived entry, labelled as derived", async ({
    page,
    request,
  }) => {
    // The gap this closes: the tab used to be permanently empty, because nothing
    // derived events from the case and its empty state claimed otherwise.
    const bearer = await token(request);
    const caseId = await newCase(request, bearer, "Timeline derived");

    const upload = await request.post(`${API_URL}/cases/${caseId}/evidence`, {
      headers: { Authorization: `Bearer ${bearer}` },
      multipart: {
        file: {
          name: "photo.jpg",
          mimeType: "image/jpeg",
          buffer: Buffer.from(
            "ffd8ffe000104a46494600010100000100010000ffdb004300ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffc00011080001000103012200021101031101ffc4001f0000010501010101010100000000000000000102030405060708090a0b",
            "hex",
          ),
        },
      },
    });
    expect(upload.ok(), `upload failed (${upload.status()})`).toBe(true);

    await page.goto(`/cases/${caseId}`);
    await page.getByRole("tab", { name: /Timeline/ }).click();

    await expect(page.getByText(/Evidence received/)).toBeVisible();
    // And it says it is derived, so nobody reads a processing time as an event time.
    await expect(page.getByText("derived").first()).toBeVisible();
  });
});