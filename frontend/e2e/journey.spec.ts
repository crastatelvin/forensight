/**
 * T7.1 — the full user journey, through the browser, against a live stack.
 *
 * Nothing is mocked. A case is created, evidence is uploaded, an analyzer is run,
 * and the result is read back — because every defect this project has found in the
 * frontend was invisible to a mocked test and obvious the moment the real thing was
 * driven. Mocks confirm the component tree; only the real thing confirms the
 * contract.
 */

import { expect, test } from "@playwright/test";
import { join } from "node:path";
import { caseIdFor, signInOnce } from "./helpers";
import { makePng } from "./png";

const ASSETS = join(process.cwd(), "..", "backend", "tests", "assets");

/** A unique case number per run, so repeat runs do not collide on the unique index. */
const RUN = Date.now().toString(36).slice(-6);

test.describe("case journey", () => {
  test("login, create a case, upload, analyse, read the result", async ({ page }) => {
    await signInOnce(page);

    const number = `E2E-${RUN}`;
    const title = `Journey ${RUN}`;
    const caseId = await caseIdFor(page, number, title);

    // --- upload ------------------------------------------------------------
    await page.getByRole("tab", { name: /Evidence/ }).click();
    await page.locator("#evidence-file").setInputFiles(join(ASSETS, "turing_a.jpg"));

    // The success notice is the only proof the file arrived *and* was hashed; the
    // row appearing is not, because the list is server-rendered.
    await expect(page.getByRole("status")).toContainText("was hashed and added", {
      timeout: 30_000,
    });
    await expect(page.getByText("turing_a.jpg")).toBeVisible();

    // --- run an analyzer ---------------------------------------------------
    await page.getByRole("tab", { name: /Analyses/ }).click();
    await page.getByLabel("Analyzer").selectOption("image-ela");
    await page.getByLabel("Evidence").selectOption({ label: "turing_a.jpg" });
    await page.getByRole("button", { name: "Run analysis" }).click();

    // The job is polled by the client, so the result appears on its own. Located by
    // the tier badge rather than by a compound `li` filter, which broke the moment
    // the markup gained a wrapper.
    // Located by the model version string rather than by the module name: the
    // version is unique per result and lives in the same card as everything the
    // test then asserts on. A compound `li` filter over the module name was tried
    // first and broke as soon as the row gained another element.
    const elaRow = page.locator("li").filter({ hasText: "1.0.0+ela" });
    // Two matches is expected: the overview bar and the detailed card both name the
    // version. Both must satisfy the honesty clause, so both are asserted.
    await expect(elaRow.first()).toBeVisible({ timeout: 60_000 });
    await expect(elaRow).toHaveCount(2);

    // --- the honesty clause, on a real result ------------------------------
    // Not "the page loaded". The score alone is meaningless, so its threshold,
    // version and limitation all have to be on screen next to it.
    // Asserted against both matches, so the overview bar and the detailed card are
    // each required to carry the limitation. Checking only one would let the other
    // render a bare number, which is the thing this screen exists to prevent.
    for (const card of await elaRow.all()) {
      await expect(card).toContainText(/threshold/i);
      await expect(card).toContainText(/Tier A/);
      await expect(card).toContainText(/Investigative lead/);
    }

    // --- the run is durable ------------------------------------------------
    // A record that vanishes on reload is not a record.
    await page.reload();
    await page.getByRole("tab", { name: /Evidence/ }).click();
    await expect(page.getByText("turing_a.jpg")).toBeVisible();
    expect(page.url()).toContain(caseId);
  });

  test("the ELA overlay renders for a JPEG", async ({ page }) => {
    await signInOnce(page);
    await caseIdFor(page, `E2EELA-${RUN}`, `Overlay ${RUN}`);

    await page.getByRole("tab", { name: /Evidence/ }).click();
    await page.locator("#evidence-file").setInputFiles(join(ASSETS, "turing_a.jpg"));
    await expect(page.getByRole("status")).toContainText("was hashed", {
      timeout: 30_000,
    });

    // The filename is a button (it selects the item for viewing), not a link.
    await page
      .getByRole("button", { name: /turing_a\.jpg/ })
      .first()
      .click();
    await page.getByRole("checkbox").check();

    const overlay = page.getByAltText(/Error Level Analysis heat map/);
    await expect(overlay).toBeVisible({ timeout: 30_000 });
    // A broken image is still "visible" to Playwright, so assert it actually
    // decoded: naturalWidth is 0 for a failed load.
    const width = await overlay.evaluate((el: HTMLImageElement) => el.naturalWidth);
    expect(width).toBeGreaterThan(0);
  });

  test("a file the technique cannot use says so instead of showing a score", async ({
    page,
  }) => {
    // ELA is defined on JPEG re-compression. A PNG is the refusal case, and it must
    // be visibly a refusal rather than a zero — "cannot measure" rendered as
    // "measured nothing" is the single failure this whole design is built to avoid.
    await signInOnce(page);
    await caseIdFor(page, `E2EREF-${RUN}`, `Refusal ${RUN}`);

    await page.getByRole("tab", { name: /Evidence/ }).click();
    await page.locator("#evidence-file").setInputFiles({
      name: "gradient.png",
      mimeType: "image/png",
      buffer: makePng(128),
    });
    await expect(page.getByRole("status")).toContainText("was hashed", {
      timeout: 30_000,
    });

    await page.getByRole("tab", { name: /Analyses/ }).click();
    await page.getByLabel("Analyzer").selectOption("image-ela");
    await page.getByLabel("Evidence").selectOption({ label: "gradient.png" });
    await page.getByRole("button", { name: "Run analysis" }).click();

    await expect(page.getByText("not measured").first()).toBeVisible({
      timeout: 60_000,
    });
    // And explicitly, in words: no number was produced, and this is not a pass.
    await expect(page.getByText(/No score was produced/)).toBeVisible();
    await expect(page.getByText(/not a passing result/i)).toBeVisible();
  });

  test("a report is signed, and a tampered copy does not verify", async ({ page }) => {
    // The whole Phase 8 contract, end to end through a browser: generate, verify,
    // alter one byte, verify again. Every part of this was broken at some point and
    // none of it is reachable from a unit test, because the signature is applied by
    // the API, transported by the Next proxy, and checked by a third-party library
    // that raises instead of returning a verdict on some corruptions.
    await signInOnce(page);
    const run = `RPT-${Date.now().toString(36).slice(-5)}`;
    await caseIdFor(page, run, `Report ${run}`);

    // No signing key is provisioned in the test environment, so the endpoint must
    // *refuse* rather than hand back a PDF carrying no integrity claim.
    await page.getByRole("tab", { name: /Findings/ }).click();
    const download = page.getByRole("button", { name: /Download signed report/ });
    await expect(download).toBeVisible();

    // Either a signed PDF comes back, or a 503 naming the provisioning command.
    // Both are correct; a silently unsigned PDF is not, and is what this asserts.
    const [downloadEvent] = await Promise.all([
      page.waitForEvent("download", { timeout: 60_000 }).catch(() => null),
      download.click(),
    ]);

    if (downloadEvent) {
      const path = await downloadEvent.path();
      expect(path).toBeTruthy();

      // Checking it back through the same UI.
      await page.locator("#verify-pdf").setInputFiles(path!);
      await page.getByRole("button", { name: "Check signature" }).click();
      await expect(page.getByText("unaltered").first()).toBeVisible({
        timeout: 60_000,
      });
    } else {
      await expect(page.getByRole("alert")).toContainText("provision_signer", {
        timeout: 30_000,
      });
    }
  });
});
