import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config.
 *
 * Two servers, because the frontend is a server-side proxy for an API whose
 * session lives in an HttpOnly cookie. The tests therefore exercise the real
 * topology rather than mocking it: a browser signs in through the Next server,
 * and the Next server talks to the API with the browser's cookie. Anything less
 * would miss exactly the class of defect this project keeps finding — a cookie not
 * forwarded, a CSRF header not set, a Set-Cookie dropped by a server-side fetch.
 *
 * `reuseExistingServer` so a developer's already-running stack is used rather than
 * fighting it for the port.
 */
const WEB_PORT = Number(process.env.PLAYWRIGHT_WEB_PORT ?? 3100);
const API_URL = process.env.FORENSIGHT_API ?? "http://localhost:8000";

export default defineConfig({
  testDir: "./e2e",
  // `.auth` holds a live session cookie. It is a credential, not a fixture, and is
  // gitignored along with the rest of the Playwright output.
  testIgnore: ["**/global-setup.ts", "**/helpers.ts", "**/png.ts"],
  // Serial, deliberately: every test signs in as the same user, and the auth rate
  // limiter allows five attempts a minute. Parallel runs would fail on the limiter
  // rather than on anything the test is checking.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: `http://localhost:${WEB_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },

  // One signed-in session for the whole run, established once by the global setup.
  // See `e2e/global-setup.ts` for why per-test sign-in does not work here.
  globalSetup: "./e2e/global-setup.ts",

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        storageState: "./e2e/.auth/storage-state.json",
      },
    },
  ],

  webServer: {
    command: "npm run build && npm run start",
    url: `http://localhost:${WEB_PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      FORENSIGHT_API_URL: API_URL,
      // Playwright drives the site over plain http, so the session cookie cannot be
      // `Secure` or the browser drops it and every test looks like an auth failure.
      FORENSIGHT_INSECURE_COOKIES: "1",
    },
  },
});
