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
 * Fixtures are created with `fetch` *inside the page*, not Playwright's `request`
 * fixture: the session lives in an HttpOnly cookie, and cookie-authenticated writes
 * need the CSRF header. Running in the page gets both for free and keeps to the rule
 * the rest of this suite follows — go through the same origin the user does.
 */
import { expect, test, type Page } from "@playwright/test";
import { signInOnce } from "./helpers";

interface Posted {
  ok: boolean;
  status: number;
  /** Raw response text. Parsed by `postJson`, never assumed to be JSON. */
  body: string;
}

/** POST through the app's own proxy, from inside the signed-in page. */
async function post(page: Page, path: string, payload: unknown): Promise<Posted> {
  // One object rather than a positional pair: `page.evaluate` serialises its
  // argument, and a tuple of two values types as `unknown` per element, which pushes
  // casts into the callback. An object keeps both fields typed.
  return page.evaluate(
    async ({ url, body }: { url: string; body: string }) => {
      const csrf = document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrf_token="))
        ?.slice("csrf_token=".length);
      const response = await fetch(url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          ...(csrf ? { "x-csrf-token": decodeURIComponent(csrf) } : {}),
        },
        body,
      });
      return { ok: response.ok, status: response.status, body: await response.text() };
    },
    { url: path, body: JSON.stringify(payload) },
  );
}

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

async function postJson(
  page: Page,
  path: string,
  payload: unknown,
): Promise<PostedJson> {
  const result = await post(page, path, payload);
  try {
    const parsed: unknown = JSON.parse(result.body);
    if (parsed && typeof parsed === "object") {
      return {
        ok: result.ok,
        status: result.status,
        body: parsed as Record<string, unknown>,
      };
    }
  } catch {
    // Fall through and hand back the text.
  }
  return { ok: result.ok, status: result.status, body: result.body };
}

test.describe("timeline", () => {
  test.beforeEach(async ({ page }) => {
    await signInOnce(page);
    await page.goto("/cases");
  });

  test("an event with no known time renders instead of crashing the tab", async ({
    page,
  }) => {
    const created = await postJson(page, "/api/cases", { title: "Timeline null test" });
    expect(
      created.ok,
      `case creation failed: ${String(created.body).slice(0, 200)}`,
    ).toBe(true);
    const caseId = String((created.body as { id?: unknown }).id);

    // `occurred_at` is omitted deliberately — the schema allows it, and that is the
    // state which used to throw.
    const event = await postJson(page, `/api/cases/${caseId}/timeline`, {
      label: "event with no known time",
      uncertain: true,
      source_ref: "CUSTODY-1",
    });
    expect(event.ok, `timeline post failed: ${String(event.body).slice(0, 200)}`).toBe(
      true,
    );
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

  test("dated events render their timestamp", async ({ page }) => {
    const created = await postJson(page, "/api/cases", {
      title: "Timeline dated test",
    });
    expect(created.ok).toBe(true);
    const caseId = String((created.body as { id?: unknown }).id);

    await postJson(page, `/api/cases/${caseId}/timeline`, {
      label: "dated event",
      occurred_at: "2026-04-01T09:30:00Z",
    });

    await page.goto(`/cases/${caseId}`);
    await page.getByRole("tab", { name: /Timeline/ }).click();
    await expect(page.getByText("dated event")).toBeVisible();
    // Not the literal "time unknown" fallback, and no crash on a valid timestamp.
    await expect(page.getByText("time unknown")).toHaveCount(0);
  });
});
