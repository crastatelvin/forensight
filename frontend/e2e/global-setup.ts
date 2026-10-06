/**
 * Global setup: authenticate once, reuse the session for every test.
 *
 * Signing in per test looks harmless and is not: `/auth/login` allows five attempts
 * a minute, so a suite of seven tests spends the budget and the last two fail on a
 * 429 from the login form — which reads as an auth bug and is not one. It cost two
 * confusing failures before this existed.
 *
 * The session is cookie-only (the access token is HttpOnly, so it cannot be read
 * into a header), which makes `storageState` the right tool: it round-trips exactly
 * what a browser holds, so a test resumes as the signed-in user with no login and
 * no rate-limit budget at all.
 */

import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const STORAGE_STATE = join(HERE, ".auth", "storage-state.json");

const BASE_URL =
  process.env.PLAYWRIGHT_BASE_URL ??
  `http://localhost:${process.env.PLAYWRIGHT_WEB_PORT ?? 3100}`;
const EMAIL = process.env.FORENSIGHT_EMAIL ?? "smoke@example.com";
const PASSWORD = process.env.FORENSIGHT_PASSWORD ?? "smoke-test-password-456";

export default async function globalSetup(): Promise<void> {
  mkdirSync(dirname(STORAGE_STATE), { recursive: true });

  const browser = await chromium.launch();
  try {
    const context = await browser.newContext();
    const page = await context.newPage();

    await page.goto(`${BASE_URL}/login`);
    await page.getByLabel("Email address").fill(EMAIL);
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("heading", { name: "Cases" }).waitFor({ timeout: 30_000 });

    await context.storageState({ path: STORAGE_STATE });
    console.log("auth setup: session saved");
  } finally {
    await browser.close();
  }
}
