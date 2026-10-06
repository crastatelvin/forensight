/**
 * Shared setup for the E2E suite.
 *
 * Signing in goes through the real login form rather than by injecting a cookie,
 * because that is the path a user takes and the one most likely to break silently —
 * a `Set-Cookie` header dropped by a server-side fetch is exactly the failure this
 * project hit, and only driving the form catches it.
 */

import { expect, type Page } from "@playwright/test";

export const CREDENTIALS = {
  email: process.env.FORENSIGHT_EMAIL ?? "smoke@example.com",
  password: process.env.FORENSIGHT_PASSWORD ?? "smoke-test-password-456",
};

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email address").fill(CREDENTIALS.email);
  await page.getByLabel("Password").fill(CREDENTIALS.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Cases" })).toBeVisible();
}

/**
 * Wait for the session to be usable.
 *
 * Exposed separately because the auth rate limiter allows five attempts a minute
 * and a suite that signs in per test will trip it — the failure would be a 429 on
 * the login form, which looks nothing like the bug anyone is hunting.
 */
export async function signInOnce(page: Page): Promise<void> {
  await page.goto("/login");
  if (
    await page
      .getByRole("heading", { name: "Cases" })
      .isVisible()
      .catch(() => false)
  ) {
    return;
  }
  await signIn(page);
}

/** The id of a case matching `number`, created through the UI if absent. */
export async function caseIdFor(
  page: Page,
  number: string,
  title: string,
): Promise<string> {
  await page.goto("/");
  const link = page.getByRole("link", { name: new RegExp(`^${number}\\b`) }).first();
  if (await link.isVisible().catch(() => false)) {
    await link.click();
    await expect(page.getByRole("heading", { name: title })).toBeVisible();
    return page.url().split("/").pop()!;
  }

  await page.getByRole("link", { name: "New case" }).first().click();
  await page.getByLabel(/Case number/).fill(number);
  await page.getByLabel("Title").fill(title);
  await page.getByRole("button", { name: "Create case" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  return page.url().split("/").pop()!;
}
