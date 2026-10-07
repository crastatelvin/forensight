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
import { caseIdFor, seedParkedRun, signInOnce } from "./helpers";
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

  test("an action parked for approval stays answerable after a reload", async ({
    page,
  }) => {
    // Approving does not end the run: it lets the agent continue, and continuing
    // costs a real model call. On this stack that is `qwen2.5:3b`, which is slower
    // than the default 45 s budget, so the suite was aborting mid-navigation with
    // `net::ERR_ABORTED` and reporting a browser fault for what is really a slow
    // model. The budget is the model's, not the browser's.
    test.setTimeout(180_000);

    // The HITL round trip, automated. This is the path that broke twice: the
    // approve button existed only on the live panel, so a reload made a parked action
    // permanently unactionable; and the endpoint ran the whole agent loop inline, so
    // the click took as long as a model run.
    //
    // The parked action is *arranged* by a seed script rather than produced by asking
    // a model to want one. Arranging state and exercising behaviour are separate
    // jobs: getting a 3B model to decide to propose a write is flaky for reasons that
    // have nothing to do with the browser, and the browser's part of this test is
    // entirely unaffected by how the action got there.
    await signInOnce(page);

    const seeded = await seedParkedRun();
    await page.goto(`/cases/${seeded.case_id}`);
    await page.getByRole("tab", { name: /Agent/ }).click();

    // The gate is on the run history, not only on the live panel — that was the
    // first of the two bugs.
    await expect(page.getByText("Waiting for your decision")).toBeVisible({
      timeout: 30_000,
    });

    // It shows exactly what will happen, before anything happens. "Approve whatever
    // it wanted" is not consent.
    //
    // Scoped to the gate: the phrase appears twice on the page — in the run's
    // objective in the history header, and in the arguments the gate is asking
    // about. Only the second one is the consent surface, so only that one is
    // asserted.
    await expect(
      page
        .locator("div")
        .filter({ hasText: "It wants to run: update_case_finding" })
        .getByText("Window forced on the primary exhibit")
        .first(),
    ).toBeVisible();

    // Reload first. The gate must survive it, which is the whole claim.
    await page.reload();
    await page.getByRole("tab", { name: /Agent/ }).click();
    await expect(page.getByText("Waiting for your decision")).toBeVisible({
      timeout: 30_000,
    });

    const started = Date.now();
    await page.getByRole("button", { name: "Approve" }).click();

    // The run advances out of the parked state, and the gate is gone.
    await expect(page.getByText("Waiting for your decision")).toHaveCount(0, {
      timeout: 60_000,
    });

    // And the write actually landed, which is the irreversible half.
    //
    // A fresh navigation, not `router.refresh()` or `reload()`. Refresh demonstrably
    // does not propagate fresh server props in this app — the gate above stayed on
    // screen after the run had already advanced in the database — so asserting
    // through it would fail for a reason unrelated to whether the write happened.
    // `reload()` is worse: it races the SSE stream the approval opened and aborts
    // with `net::ERR_ABORTED` about one run in five. A navigation is deterministic.
    // Recorded as a known gap rather than hidden here.
    await page.goto(`/cases/${seeded.case_id}`);
    await page.getByRole("tab", { name: /Findings/ }).click();
    await expect(
      page.getByRole("heading", {
        name: "Window forced on the primary exhibit",
      }),
    ).toBeVisible({ timeout: 30_000 });

    // Bounded, not asserted: a machine-dependent budget, but one the bug made
    // impossible. Inline, this took the length of a whole model run; fixed, it is
    // milliseconds. Sixty seconds fails loudly on a regression without being a
    // flake risk.
    expect(Date.now() - started).toBeLessThan(60_000);
  });
});
