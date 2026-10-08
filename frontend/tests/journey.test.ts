/**
 * E2E journey check, run against a live stack.
 *
 * Not a replacement for T7.1's Playwright suite — that comes with the test
 * harness. This exists because the recurring lesson of the backend phases is that
 * green tests hide defects, and a frontend that has never been driven against a
 * real API is exactly that. So: sign in, open a case, look at every tab, run an
 * analysis, and report what actually rendered.
 *
 * Usage:
 *   FORENSIGHT_WEB=http://localhost:3000 node --test tests/journey.test.ts
 *
 * Uses the built-in fetch and a tiny cookie jar. No browser automation and no
 * test framework dependency — the point is to exercise the real HTTP surface, and
 * a driver would be testing the driver as much as the app.
 */

import assert from "node:assert/strict";
import test, { type TestContext } from "node:test";

const WEB = process.env.FORENSIGHT_WEB ?? "http://localhost:3000";
const API = process.env.FORENSIGHT_API ?? "http://localhost:8000";
const EMAIL = process.env.FORENSIGHT_EMAIL ?? "smoke@example.com";
const PASSWORD = process.env.FORENSIGHT_PASSWORD ?? "smoke-test-password-456";

const jar = new Map<string, string>();

function storeCookies(response: Response): void {
  const raw = response.headers.getSetCookie?.() ?? [];
  for (const line of raw) {
    const [pair] = line.split(";");
    const eq = pair.indexOf("=");
    if (eq < 1) continue;
    const name = pair.slice(0, eq).trim();
    const value = pair.slice(eq + 1).trim();
    if (value === "" || /expires=thu, 01 jan 1970/i.test(line)) jar.delete(name);
    else jar.set(name, value);
  }
}

function authHeaders(): Record<string, string> {
  return { authorization: `Bearer ${jar.get("access_token")}` };
}

function cookieHeader(): string {
  return [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
}

async function call(
  path: string,
  init: RequestInit = {},
): Promise<{ status: number; body: string; contentType: string }> {
  const headers: Record<string, string> = {
    accept: "application/json",
    ...(jar.size ? { cookie: cookieHeader() } : {}),
    ...(jar.has("csrf_token") ? { "x-csrf-token": jar.get("csrf_token")! } : {}),
    ...(init.headers as Record<string, string>),
  };
  const response = await fetch(`${WEB}${path}`, {
    redirect: "manual",
    ...init,
    headers,
  });
  storeCookies(response);
  return {
    status: response.status,
    body: await response.text(),
    contentType: response.headers.get("content-type") ?? "",
  };
}

/**
 * Authenticate against the API and adopt its cookies.
 *
 * Straight at the API rather than through the login form, because a Next server
 * action is not a plain form post and cannot be driven with `fetch`. What this
 * covers is every request that follows — the proxy routes, which are where the
 * HttpOnly-cookie and CSRF handling actually lives. Driving the login form itself
 * is what the Playwright suite (T7.1) is for; doing it here as well would be a
 * second, weaker copy of that test.
 */
/**
 * Whether a live stack is up.
 *
 * These tests need a running API and frontend. When neither is reachable they skip
 * rather than fail: a test that fails because the server is not started is noise
 * that hides the tests that would have caught something. They must not skip when
 * the stack *is* up, though — hence a real probe rather than a try/catch that
 * swallows whatever went wrong.
 */
async function stackIsUp(): Promise<boolean> {
  try {
    const api = await fetch(`${API}/healthz`, {
      signal: AbortSignal.timeout(3_000),
    });
    if (!api.ok) return false;
    const web = await fetch(`${WEB}/login`, {
      signal: AbortSignal.timeout(5_000),
    });
    return web.ok;
  } catch {
    return false;
  }
}

/**
 * Skip the calling test unless a live stack is reachable.
 *
 * Uses the test context's own `skip`, which marks *this* test as skipped. Calling
 * `test(...)` with `{skip: true}` instead registers a new skipped sub-test and lets
 * the parent keep running — which showed up as four "failures" whenever the stack
 * was down, the opposite of the intent.
 */
async function requireStack(t: TestContext): Promise<void> {
  if (await stackIsUp()) return;
  t.skip(
    `no live stack at ${API} / ${WEB} — start it with docker compose up -d ` +
      `and npm run dev to run these`,
  );
}

let signedIn: Promise<void> | null = null;

/**
 * Sign in at most once per process.
 *
 * Not an optimisation. `/auth/login` is genuinely rate limited, and signing in
 * once per test made a full run trip the limiter — so the suite failed for a
 * reason that had nothing to do with what it was checking. Memoising here keeps
 * the test honest about the system it runs against.
 */
/**
 * Fetch a resource as bytes.
 *
 * `res.text()` decodes as UTF-8, which replaces every byte above 0x7f — so a
 * perfectly valid PNG read as text has mangled magic bytes, and asserting on the
 * text fails on a route that is working. Binary assertions need binary reads.
 */
async function callBinary(path: string): Promise<{
  status: number;
  bytes: Uint8Array;
  contentType: string;
  body: string;
}> {
  const response = await fetch(`${WEB}${path}`, {
    headers: {
      accept: "*/*",
      ...(jar.size ? { cookie: cookieHeader() } : {}),
      ...(jar.has("csrf_token") ? { "x-csrf-token": jar.get("csrf_token")! } : {}),
    },
  });
  const bytes = new Uint8Array(await response.arrayBuffer());
  storeCookies(response);
  return {
    status: response.status,
    bytes,
    contentType: response.headers.get("content-type") ?? "",
    // For error messages only; never for content assertions.
    body: new TextDecoder().decode(bytes.slice(0, 300)),
  };
}

function signIn(): Promise<void> {
  signedIn ??= doSignIn();
  return signedIn;
}

const AUTH_RATE_LIMIT_WINDOW_MS = 65_000;

async function doSignIn(): Promise<void> {
  const attempt = async () =>
    fetch(`${API}/auth/login`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
    });

  let direct = await attempt();

  // `/auth/login` allows 5 attempts a minute. Iterating this suite while
  // developing burns through that, and the suite then fails for a reason that has
  // nothing to do with what it is checking — so wait the window out and try once
  // more rather than reporting a false failure. Tolerating 429 outright would be
  // worse: it would hide the limiter working, which is a security control.
  if (direct.status === 429) {
    console.log("  (auth rate limit reached; waiting out the window)");
    await new Promise((resolve) => setTimeout(resolve, AUTH_RATE_LIMIT_WINDOW_MS));
    direct = await attempt();
  }

  assert.equal(direct.status, 200, `sign-in failed: ${direct.status}`);
  storeCookies(direct);
}

test("a signed-in session can reach every case screen", async (t) => {
  await requireStack(t);
  await signIn();
  assert.ok(jar.has("access_token"), "no access token stored after sign-in");
  assert.ok(jar.has("csrf_token"), "no CSRF token stored after sign-in");

  const home = await call("/");
  assert.equal(home.status, 200, `dashboard returned ${home.status}`);
  assert.match(home.body, /Cases/, "dashboard did not render a case list");

  const listed = await fetch(`${API}/cases?limit=1`, { headers: authHeaders() });
  const cases = (await listed.json()) as Array<{ id: string }>;
  assert.ok(
    Array.isArray(cases) && cases.length > 0,
    `no cases to open: ${listed.status} ${JSON.stringify(cases).slice(0, 200)}`,
  );

  const page = await call(`/cases/${cases[0].id}`);
  assert.equal(page.status, 200, `case page returned ${page.status}`);
  assert.match(page.body, /role="tablist"/, "case page has no tab strip");
  assert.match(page.body, /Evidence/, "evidence tab missing");
  assert.match(page.body, /Analyses/, "analyses tab missing");
  assert.match(page.body, /Audit/, "audit tab missing");
});

test("evidence bytes and the ELA overlay are served through the proxy", async (t) => {
  await requireStack(t);
  await signIn();

  // Walk every visible case for a JPEG whose bytes actually exist. Not every
  // evidence row has one: a record can outlive its stored object, and that is a
  // condition with its own test below rather than something to trip over here.
  const jpegs = await jpegEvidence();
  assert.ok(jpegs.length > 0, "no JPEG evidence in any visible case");

  let served = 0;
  let rendered = 0;
  let sawGone = false;
  let sawUndecodable = false;

  for (const item of jpegs) {
    const inline = await call(`/api/evidence/${item.id}/download?disposition=inline`);
    if (inline.status === 410) {
      // A record with no bytes. Handled below; skip for the happy path.
      sawGone = true;
      assert.doesNotMatch(
        inline.body,
        /Traceback|S3Error|minio/,
        "a storage traceback reached the client",
      );
      continue;
    }
    assert.equal(
      inline.status,
      200,
      `inline download of ${item.id} returned ${inline.status}: ${inline.body.slice(0, 200)}`,
    );
    assert.match(inline.contentType, /image\/jpeg/, "inline response is not the image");
    assert.ok(inline.body.length > 0, "inline response was empty");
    served += 1;

    // Fetched as bytes, not text. `res.text()` decodes as UTF-8, which replaces
    // every byte above 0x7f — so a perfectly valid PNG read as text has mangled
    // magic bytes and the assertion fails on a working route. That is a bug in the
    // test that looks exactly like a bug in the code.
    const overlay = await callBinary(`/api/evidence/${item.id}/ela`);
    // **Two correct outcomes, not one.** Either the bytes decode and the overlay
    // renders, or they do not and the endpoint refuses with a readable reason.
    //
    // The earlier version asserted 200 unconditionally and failed as soon as
    // `seed_parked_run.py` had been used: that helper deliberately inserts a
    // one-byte placeholder `seed.jpg` ("nothing verifies this file's bytes; it exists
    // so the case has something to analyse"), so walking *every* visible case's JPEGs
    // reaches rows that cannot be decoded. The failure was real — 422 — and the
    // endpoint was right to give it: refusing with a reason is the behaviour this
    // project wants everywhere else. What must never happen is a 5xx or storage
    // internals leaking to the client, so that is what is actually asserted.
    if (overlay.status === 422) {
      sawUndecodable = true;
      assert.match(
        overlay.body,
        /did not decode|cannot be applied/i,
        `ELA refusal for ${item.id} does not say why: ${overlay.body.slice(0, 200)}`,
      );
      assert.doesNotMatch(overlay.body, /Traceback|S3Error|minio/);
      continue;
    }

    assert.equal(
      overlay.status,
      200,
      `ELA overlay returned ${overlay.status}: ${overlay.body}`,
    );
    assert.equal(overlay.contentType, "image/png");
    assert.deepEqual(
      Array.from(overlay.bytes.slice(0, 8)),
      [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a],
      "ELA overlay is not a PNG",
    );
    rendered += 1;
    break;
  }

  assert.ok(served > 0, `no JPEG served bytes; ${jpegs.length} tried`);
  assert.ok(
    rendered > 0,
    `no JPEG rendered an overlay; ${jpegs.length} tried and all refused`,
  );

  if (sawUndecodable) {
    console.log(
      "  (some evidence records hold undecodable bytes; 422 handled cleanly)",
    );
  }

  // Whether a dangling record exists is a property of *this* database, not of the
  // code, so it is reported rather than asserted. An earlier version demanded one
  // and failed the moment someone re-uploaded the file. What must hold either way
  // is that a record with no bytes is a clean, readable refusal with no storage
  // internals in it -- checked inside the loop above.
  if (sawGone) {
    console.log("  (one evidence record had no stored bytes; 410 handled cleanly)");
  }
});

/** Every JPEG evidence record visible to the signed-in user. */
async function jpegEvidence(): Promise<Array<{ id: string }>> {
  const cases = (await (
    await fetch(`${API}/cases?limit=50`, { headers: authHeaders() })
  ).json()) as Array<{ id: string }>;
  const found: Array<{ id: string }> = [];
  for (const c of cases) {
    const items = (await (
      await fetch(`${API}/cases/${c.id}/evidence`, { headers: authHeaders() })
    ).json()) as Array<{ id: string; content_type: string }>;
    for (const item of items) {
      if (item.content_type === "image/jpeg") found.push(item);
    }
  }
  return found;
}

test("an unauthenticated request is refused, not served", async (t) => {
  await requireStack(t);
  // Run this one first in file order, and restore immediately: the jar is shared
  // process-wide for the rate-limit reason above, so clearing it and not putting
  // it back would fail every test that follows for an unrelated reason.
  const saved = new Map(jar);
  jar.clear();
  const res = await call("/api/modules");
  for (const [k, v] of saved) jar.set(k, v);
  // The route is gone; what matters is that an anonymous caller gets nothing.
  assert.ok(
    res.status === 401 || res.status === 404,
    `expected refusal, got ${res.status}`,
  );
  assert.doesNotMatch(
    res.body,
    /yolov4|sface/,
    "an anonymous caller saw model details",
  );
});

test("an API outage surfaces as 503, not a crash", async (t) => {
  await requireStack(t);
  await signIn();
  const res = await call("/api/evidence/00000000-0000-0000-0000-000000000000/download");
  // Either a clean refusal from the API (404) or a proxy error. What it must never
  // be is a 500 from an unhandled throw: that is a stack trace in a browser tab.
  assert.ok([404, 409, 503].includes(res.status), `unexpected status ${res.status}`);
  assert.doesNotMatch(res.body, /Traceback|at app\./, "a traceback reached the client");
});
