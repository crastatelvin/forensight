/**
 * Turning a raw analyzer result into something a person can act on.
 *
 * This is the honesty clause, and it lives here as pure functions rather than being
 * scattered across components, for one reason: the failure mode it prevents is
 * *silent*. Nothing errors when a result is misread. A score of 0.008 from ELA on
 * a JPEG and a score of 0.008 from ELA on a PNG — where the technique cannot apply
 * at all — are the same number, and a UI that renders scores uniformly will show
 * them identically. That is the bug this module exists to make impossible.
 *
 * Three rules, enforced here:
 *
 *  1. **Cannot-measure is never a score.** A `not_applicable` job has no result
 *     row at all, so there is nothing to render as zero. `explainJob` returns a
 *     "not measured" outcome rather than a number.
 *  2. **Every number carries its threshold and version.** A score without them is
 *     uninterpretable: 0.8 against a 0.25 threshold and 0.8 against a 0.9 threshold
 *     are opposite conclusions.
 *  3. **The tier says how far a conclusion can be pushed.** Tier A is a published,
 *     benchmarked method; Tier B is a useful signal that is not proof of anything
 *     on its own. That is not a formality — it is the difference between "this is
 *     edited" and "this looks edited, worth a look".
 */

import type { AnalysisJobRead, AnalysisResultRead, ModuleRead } from "./types";

/** How much weight a technique's output can carry. */
export type Tier = "A" | "B" | "C";

/** What actually happened, from the UI's point of view. */
export type Outcome =
  | { kind: "measured"; result: AnalysisResultRead }
  | { kind: "not_measured"; job: AnalysisJobRead; reason: string }
  | { kind: "failed"; job: AnalysisJobRead; reason: string }
  | { kind: "pending"; job: AnalysisJobRead };

/**
 * What a score means, in a sentence a person can weigh.
 *
 * The comparison is against the module's own threshold, not a universal scale,
 * because there is no universal scale — entropy is in bits per byte and face
 * similarity is a cosine. Presenting both on one axis would be the single most
 * misleading thing this UI could do.
 */
export function explainScore(
  score: number,
  threshold: number,
): { verdict: string; tone: "alert" | "warn" | "calm"; detail: string } {
  if (score >= threshold) {
    return {
      verdict: "above threshold",
      tone: "alert",
      detail: `Measured ${score.toFixed(4)}, at or above this module's threshold of ${threshold}. Worth an investigator's attention — see what the technique can and cannot support before concluding anything.`,
    };
  }
  // Between a half-threshold and the threshold, the result is not clean but it is
  // also not a flag. Rendering that as an unqualified "below threshold" would
  // hide a borderline value, which is the same class of error as overstating it.
  if (score >= threshold / 2) {
    return {
      verdict: "near threshold",
      tone: "warn",
      detail: `Measured ${score.toFixed(4)}, within a factor of two of this module's threshold of ${threshold}. Not a flag, but not a clean pass either.`,
    };
  }
  return {
    verdict: "below threshold",
    tone: "calm",
    detail: `Measured ${score.toFixed(4)}, comfortably under this module's threshold of ${threshold}.`,
  };
}

/** One line on what a tier permits. Used in the tier badge tooltip. */
export function explainTier(tier: string): { label: string; meaning: string } {
  switch (tier) {
    case "A":
      return {
        label: "Tier A — investigative lead",
        meaning:
          "A published, independently benchmarked method. A result above threshold is a legitimate lead. It is still not proof: no forensic technique is.",
      };
    case "B":
      return {
        label: "Tier B — corroborating signal",
        meaning:
          "A real signal, but not a determination on its own. Use it to support or weaken a hypothesis formed from other evidence — never as the sole basis for a finding.",
      };
    case "C":
      return {
        label: "Tier C — unvalidated",
        meaning:
          "Experimental. Included for research value only. Must not appear as a finding in a report.",
      };
    default:
      return {
        label: `Tier ${tier}`,
        meaning: "Trust level not declared by this module.",
      };
  }
}

/**
 * Fold a job and its (possibly absent) result into one outcome.
 *
 * Written to be total: there is no input for which it returns "show a score" when
 * no measurement was made. A caller that wants a number has to have a result row.
 */
export function explainJob(
  job: AnalysisJobRead,
  result: AnalysisResultRead | undefined,
): Outcome {
  if (job.status === "not_applicable") {
    return {
      kind: "not_measured",
      job,
      reason:
        job.error ||
        "This technique does not apply to this input, so nothing was measured. " +
          "This is not a pass — it is an absence of measurement, and no other " +
          "technique can stand in for it.",
    };
  }
  if (job.status === "failed") {
    return {
      kind: "failed",
      job,
      reason: job.error || "The analysis did not complete.",
    };
  }
  if (job.status === "done" && result) {
    return { kind: "measured", result };
  }
  if (job.status === "done" && !result) {
    // Defensive: the backend writes the row with the result. If they ever
    // disagree, saying "done, no number" is honest; inventing a zero is not.
    return {
      kind: "not_measured",
      job,
      reason:
        "The job completed but no result was recorded. This is a system fault, " +
        "not a measurement.",
    };
  }
  return { kind: "pending", job };
}

/**
 * Per-module plain-language descriptions.
 *
 * Used for the "what does this measure?" popover. Kept next to the rendering code
 * rather than served from the backend because it is explanatory copy, not data —
 * and because a module whose explanation is missing here should be visibly
 * un-explained rather than silently given generic text.
 */
const MODULE_EXPLANATIONS: Record<string, string> = {
  "byte-entropy":
    "Shannon entropy over the byte histogram, in bits per byte. A high value means the file's bytes are close to uniformly distributed, which is what compressed or encrypted data looks like. A low value means repeated structure. Says nothing about *what* the data is.",
  "image-ela":
    "Error Level Analysis: re-compress the image at a known quality and measure where the difference from the original concentrates. Regions that were edited after the original compression carry a different residual. Sensitive to the codec itself, so the version string includes the exact libjpeg build.",
  "image-exif":
    "Reads EXIF metadata and scores how internally consistent it is — timestamps that disagree, a GPS tag where the time zone does not fit, editing software recorded in the file. GPS coordinates and software names are reported but deliberately not scored, because a single model cannot tell a forged tag from a genuine one.",
  "image-prnu":
    "Photo Response Non-Uniformity: every camera sensor leaves a fixed pattern in its noise. Correlating that pattern between two frames answers whether they came from the same camera. It says nothing about whether the same person appears in both.",
  "object-detect":
    "Runs a COCO-trained detector and reports the highest confidence among watchlist classes. COCO has no firearm class, so this cannot detect a weapon — that is a gap in the training data, not a threshold that could be tuned.",
  "face-compare":
    "One-to-one face verification: detects a face in each image and compares the two embeddings' cosine similarity. Verification only — it answers 'are these the same person' for a pair you already chose, and never searches a gallery to identify a face. The threshold is the vendor's published value and has not been calibrated for this deployment.",
};

/** What a module measures, or `null` if we have not written it. */
export function explainModule(name: string): string | null {
  return MODULE_EXPLANATIONS[name] ?? null;
}

/**
 * The analyzer's own detail body.
 *
 * Every module nests its findings under `payload.detail` and the envelope keys
 * (`tier`, `score`, `disclaimer`, `model_version`) sit alongside. Reading the top
 * level for module facts silently finds nothing — which is how a calibration
 * warning goes missing without anything failing.
 */
export function detailOf(payload: Record<string, unknown>): Record<string, unknown> {
  const detail = payload.detail;
  return detail && typeof detail === "object" && !Array.isArray(detail)
    ? (detail as Record<string, unknown>)
    : {};
}

/**
 * Flags that must be visible wherever a module's score is shown.
 *
 * These are facts the analyzer recorded about itself that change how far the
 * number can be pushed. The calibration flag is the important one: a threshold
 * nobody validated here is a vendor's claim, and hiding that would make the
 * number look better-founded than it is — which is the specific failure this
 * whole project is built to avoid.
 */
export function calibrationFlags(payload: Record<string, unknown>): string[] {
  const detail = detailOf(payload);
  const flags: string[] = [];

  const calibration = detail.calibration as
    | {
        calibrated_on_this_deployment?: boolean;
        threshold_source?: string;
        note?: string;
      }
    | undefined;
  if (calibration?.calibrated_on_this_deployment === false) {
    flags.push(
      `The threshold for this module is the model vendor's published value${
        calibration.threshold_source ? ` (${calibration.threshold_source})` : ""
      }, not one calibrated against this deployment. Treat it as a relative ` +
        `comparison between two images rather than an absolute decision boundary.` +
        (calibration.note ? ` ${calibration.note}` : ""),
    );
  }

  if (detail.is_zero_meaningful === false) {
    flags.push(
      "A score of zero here does not mean 'nothing was found' — it means the signal does not apply to this input. Check the analyzer's limitations before reading it as a clean result.",
    );
  }

  const idempotency = payload.idempotency as { hit?: boolean } | undefined;
  if (idempotency?.hit) {
    flags.push(
      "Served from a previous identical run — same inputs, same model version. This is not a fresh analysis.",
    );
  }

  if (detail.reference_is_single_frame === true) {
    flags.push(
      "Compared against a single reference frame. One frame yields a noise residual, not a camera fingerprint, so this correlation is weaker evidence than a multi-frame comparison would be.",
    );
  }

  return flags;
}

/** Whether a module is available, and why not if it is not. */
export function moduleAvailability(modules: ModuleRead[]): Map<string, ModuleRead> {
  return new Map(modules.map((m) => [m.name, m]));
}
