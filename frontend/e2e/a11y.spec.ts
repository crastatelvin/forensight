/**
 * T7.3 — WCAG 2.1 AA, measured rather than asserted.
 *
 * The design tokens in `globals.css` were chosen to clear 4.5:1 for body text and
 * 3:1 for large text and UI boundaries, and that choice was written down as if it
 * were a fact. It was not a fact — it was arithmetic nobody re-checked, and the
 * natural failure of a dark mode is one token nobody looked at. axe-core measures
 * the rendered result, which is the only version of the claim that means anything.
 *
 * Scanned in both themes, because a palette that passes in light mode says nothing
 * about the same tokens on a dark surface — and this app's dark mode is a second
 * set of hand-picked values rather than an inversion.
 */

import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { caseIdFor, signInOnce } from "./helpers";

/** Violations that axe reports but that are wrong for this app, with reasons. */
const ACCEPTABLE: Record<string, string> = {
  // The skip link is intentionally off-screen until focused. It is in the
  // accessibility tree and reachable by keyboard, which is what it is for.
  "skip-link": "n/a — not in use",
};

async function scan(page: Page): Promise<Awaited<ReturnType<typeof run>>> {
  return run(page);
}

async function run(page: Page) {
  return new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
}

function describe(results: Awaited<ReturnType<typeof run>>): string {
  return results.violations
    .map(
      (v) =>
        `${v.id} (${v.impact}): ${v.help}\n` +
        v.nodes
          .slice(0, 3)
          .map(
            (n) =>
              `    ${n.target.join(" ")}\n      ${n.failureSummary?.split("\n")[1] ?? ""}`,
          )
          .join("\n"),
    )
    .join("\n\n");
}

test.describe("accessibility", () => {
  test.beforeEach(async ({ page }) => {
    await signInOnce(page);
  });

  test("the login page has no WCAG A/AA violations", async ({ page }) => {
    await page.goto("/login");
    const results = await scan(page);
    expect(describe(results) || "clean").toBe("clean");
  });

  test("the case list has no WCAG A/AA violations", async ({ page }) => {
    await page.goto("/");
    const results = await scan(page);
    expect(describe(results) || "clean").toBe("clean");
  });

  for (const theme of ["light", "dark"] as const) {
    test(`the case workspace has no WCAG A/AA violations in ${theme} mode`, async ({
      page,
    }) => {
      await page.evaluate((t) => {
        document.documentElement.classList.toggle("dark", t === "dark");
        localStorage.setItem("forensight-theme", t);
      }, theme);

      const run2 = `A11Y-${theme}-${Date.now().toString(36).slice(-5)}`;
      await caseIdFor(page, run2, `A11y ${theme}`);
      await page.getByRole("tab", { name: /Evidence/ }).click();

      const results = await scan(page);
      const real = results.violations.filter((v) => !ACCEPTABLE[v.id]);
      expect(describe({ ...results, violations: real }) || "clean").toBe("clean");
    });
  }

  test("the whole journey is operable by keyboard alone", async ({ page }) => {
    // Signed out first, deliberately: this test is about signing in, and the
    // shared session would redirect /login away before there was a form to drive.
    await page.context().clearCookies();
    await page.goto("/login");

    // Tab to the first field and type. If focus order is wrong the email lands
    // somewhere else and the assertion below catches it.
    // Focus order is asserted, not assumed by counting tabs. The first version
    // pressed Tab three times and typed, so if the order differed the email landed
    // in the wrong field and the test failed on "Cases not found" — which says
    // nothing about focus order.
    //
    // Tab order is cyclic and Chromium restores focus across navigations, so where
    // the sequence starts depends on the previous test. Tabbing until the skip
    // link is reached tests the real property — that it is the first stop — without
    // depending on where focus happened to begin.
    const skipLink = page.getByRole("link", { name: "Skip to main content" });
    const email = page.getByLabel("Email address");
    const password = page.getByLabel("Password");
    const submit = page.getByRole("button", { name: "Sign in" });
    const themeToggle = page.getByRole("button", { name: /Switch to/ });

    for (let i = 0; i < 8; i++) {
      await page.keyboard.press("Tab");
      if (await skipLink.evaluate((el) => el === document.activeElement)) break;
    }
    await expect(skipLink).toBeFocused({ timeout: 3_000 });

    // From there the order is deterministic, and is asserted element by element.
    await page.keyboard.press("Tab");
    await expect(themeToggle).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(email).toBeFocused();
    await page.keyboard.type(CREDENTIALS_EMAIL);

    await page.keyboard.press("Tab");
    await expect(password).toBeFocused();
    await page.keyboard.type(CREDENTIALS_PASSWORD);

    await page.keyboard.press("Tab");
    await expect(submit).toBeFocused();

    // Submit without touching the mouse.
    await page.keyboard.press("Enter");

    await expect(page.getByRole("heading", { name: "Cases" })).toBeVisible();

    // The skip link must actually work, or it is decoration: tab to it, activate,
    // and land on the main region.
    await page.goto("/");
    // Pressed on the body rather than the page: in headless Chromium the first Tab
    // after a navigation can be swallowed by the browser chrome, which makes the
    // skip link look like it is not the first tab stop when it is.
    await page.locator("body").press("Tab");
    await expect(
      page.getByRole("link", { name: "Skip to main content" }),
    ).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeVisible();
  });

  test("tab strips move with the arrow keys and only one tab is tabbable", async ({
    page,
  }) => {
    // A tab strip only exists on a case, so this needs one. `page.goto("/")` would
    // find nothing and fail on a locator rather than on the behaviour under test.
    const run = `TABS-${Date.now().toString(36).slice(-5)}`;
    await caseIdFor(page, run, `Tabs ${run}`);

    const evidenceTab = page.getByRole("tab", { name: /Evidence/ });

    await evidenceTab.focus();
    await expect(evidenceTab).toHaveAttribute("aria-selected", "true");

    // Roving tabindex: the selected tab is the only one in the tab order.
    await page.keyboard.press("ArrowRight");
    const analyses = page.getByRole("tab", { name: /Analyses/ });
    await expect(analyses).toBeFocused();
    await expect(analyses).toHaveAttribute("aria-selected", "true");
    await expect(evidenceTab).toHaveAttribute("tabindex", "-1");

    await page.keyboard.press("End");
    await expect(page.getByRole("tab", { name: /Audit/ })).toBeFocused();
    await page.keyboard.press("Home");
    await expect(evidenceTab).toBeFocused();
  });

  test("the theme toggle persists across a reload in both directions", async ({
    page,
  }) => {
    await page.goto("/");
    const toggle = page.getByRole("button", { name: /Switch to/ });

    const start = await page.evaluate(() =>
      document.documentElement.classList.contains("dark") ? "dark" : "light",
    );
    const wanted = start === "dark" ? "light" : "dark";

    await toggle.click();
    await expect(page.locator("html")).toHaveClass(
      new RegExp(wanted === "dark" ? "\\bdark\\b" : "^(?!.*\\bdark\\b).*"),
    );

    await page.reload();
    const after = await page.evaluate(() =>
      document.documentElement.classList.contains("dark") ? "dark" : "light",
    );
    expect(after).toBe(wanted);
  });
});

const CREDENTIALS_EMAIL = process.env.FORENSIGHT_EMAIL ?? "smoke@example.com";
const CREDENTIALS_PASSWORD =
  process.env.FORENSIGHT_PASSWORD ?? "smoke-test-password-456";
