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

/**
 * Park a state-changing agent action, so a browser test has something to answer.
 *
 * Run through the API container rather than mocked, because the arrangement has to
 * go through the same code the system uses: a real case, a real run, and the real
 * `NeedsApproval` branch. What this does *not* do is decide anything — the browser
 * test's subject is what happens when a person answers, and that is untouched by how
 * the action got parked.
 *
 * Skipped rather than failed when the API container is not reachable, and says so.
 * A seed that cannot run is an environment problem, and reporting it as a product
 * failure sends the reader looking in the wrong place.
 */
export async function seedParkedRun(): Promise<{
  case_id: string;
  run_id: string;
  action_id: string;
  tool: string;
}> {
  const { execFileSync } = await import("node:child_process");
  const { join } = await import("node:path");

  const repoRoot = join(process.cwd(), "..");
  let out: string;
  try {
    out = execFileSync(
      "docker",
      [
        "compose",
        "-f",
        join(repoRoot, "docker-compose.yml"),
        "exec",
        "-T",
        "api",
        "uv",
        "run",
        "--no-sync",
        "python",
        "-m",
        "scripts.seed_parked_run",
        "--email",
        process.env.FORENSIGHT_EMAIL ?? "smoke@example.com",
      ],
      { cwd: repoRoot, encoding: "utf8", timeout: 120_000 },
    );
  } catch {
    throw new Error(
      "could not seed a parked run. The API container must be running " +
        "(docker compose up -d), since the seed goes through the real graph.",
    );
  }

  const start = out.indexOf("{");
  if (start === -1) throw new Error(`seed produced no JSON: ${out.slice(0, 300)}`);
  return JSON.parse(out.slice(start)) as {
    case_id: string;
    run_id: string;
    action_id: string;
    tool: string;
  };
}
