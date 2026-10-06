import type { AnalysisResultRead } from "@/lib/types";
import { explainScore, explainTier, calibrationFlags } from "@/lib/explain";
import { Card, CardHeader, CardBody, EmptyState, Badge } from "@/components/ui";

/**
 * Where every analyzer landed, relative to its own threshold.
 *
 * **Not Recharts.** The obvious choice for this is a charting library, and it is the
 * wrong one here for a specific reason: the reader of a forensic result is a person
 * deciding whether something needs investigating, so the number and its threshold
 * have to be readable as *text* by a screen reader, selectable, and comparable
 * across analyzers whose scales differ by orders of magnitude. Recharts renders SVG
 * that a screen reader reads as an unlabelled graphic, which would mean shipping an
 * accessible table alongside it anyway — the same information twice, with the
 * second copy second-hand.
 *
 * Each row is therefore a real DOM bar whose fill is a `div`, with the score and the
 * threshold both in text. It reads correctly with CSS disabled, works with a
 * keyboard, and can be copied into a report.
 *
 * **When to add a charting library:** when there is a genuine time series worth
 * plotting — score against analysis time across a case, with dozens of points and
 * a readable x-axis. That is a different chart, and it is not this one.
 */
export function ResultsOverview({ results }: { results: AnalysisResultRead[] | null }) {
  if (results === null) {
    return (
      <Card>
        <CardHeader title="Where the analyzers landed" />
        <CardBody>
          <EmptyState
            icon="◌"
            title="Could not load results"
            hint="This is a failed load, not an absence of results. Do not conclude that nothing was analysed."
          />
        </CardBody>
      </Card>
    );
  }

  if (results.length === 0) {
    return (
      <Card>
        <CardHeader title="Where the analyzers landed" />
        <CardBody className="p-0">
          <EmptyState
            icon="◌"
            title="No results yet"
            hint="Run an analyzer from the Analyses tab. Each one scores on its own scale, so the bars below are comparable only within a row."
          />
        </CardBody>
      </Card>
    );
  }

  const flagged = results.filter(
    (r) => explainScore(r.score, r.threshold).tone !== "calm",
  ).length;

  return (
    <Card>
      <CardHeader
        title={`Where the analyzers landed (${results.length})`}
        subtitle={
          flagged > 0
            ? `${flagged} at or near their threshold. Bars are comparable within a row only — each analyzer has its own scale.`
            : "All results below their thresholds. Bars are comparable within a row only — each analyzer has its own scale."
        }
      />
      <CardBody>
        <ul className="space-y-3">
          {results.map((r) => (
            <ResultBar key={r.id} result={r} />
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

/**
 * One analyzer's bar.
 *
 * The scale is per-analyzer and deliberately so. Entropy is in bits per byte and
 * face similarity is a cosine; putting them on one axis would be the single most
 * misleading thing this screen could do, so each bar is normalised against its own
 * threshold and says so.
 */
function ResultBar({ result }: { result: AnalysisResultRead }) {
  const score = explainScore(result.score, result.threshold);
  const tier = explainTier(result.tier);
  const flags = calibrationFlags(result.payload);

  // Width is measured against a per-analyzer ceiling rather than a shared maximum.
  // `headroom` is what a result needs to be clearly "at threshold" on any scale:
  // twice the threshold for the additive scales, and a floor so a near-zero
  // threshold (PRNU's 0.2) does not produce a full-width bar for a middling result.
  const ceiling = Math.max(result.threshold * 2, result.score * 1.15, 1e-6);
  const widthPct = Math.min(100, (result.score / ceiling) * 100);
  const thresholdPct = Math.min(100, (result.threshold / ceiling) * 100);

  const barTone =
    score.tone === "alert"
      ? "var(--alert)"
      : score.tone === "warn"
        ? "var(--warn)"
        : "var(--calm)";

  return (
    <li>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-semibold">{result.module}</span>
          <Badge tone={result.tier === "A" ? "accent" : "info"} title={tier.meaning}>
            Tier {result.tier}
          </Badge>
          <Badge
            tone={
              score.tone === "alert" ? "alert" : score.tone === "warn" ? "warn" : "calm"
            }
          >
            {score.verdict}
          </Badge>
          {flags.length > 0 ? (
            <span
              title={flags.join("\n\n")}
              className="cursor-help text-[11px] font-medium text-warn"
            >
              {flags.length} caveat{flags.length === 1 ? "" : "s"}
            </span>
          ) : null}
        </span>

        {/* The numbers in text, which is the point. The bar is decoration on top of
            a statement that already reads correctly without it. */}
        <span className="font-mono text-xs tabular-nums text-secondary">
          <span className="text-sm font-semibold text-primary">
            {result.score.toFixed(4)}
          </span>
          {" / "}
          {result.threshold}
        </span>
      </div>

      <div
        className="relative mt-1.5 h-2.5 overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`${result.module}: measured ${result.score.toFixed(4)} against a threshold of ${result.threshold}. ${score.verdict}.`}
      >
        <div
          className="h-full rounded-full transition-[width]"
          style={{ width: `${widthPct}%`, backgroundColor: barTone }}
        />
        {/* The threshold, marked on the same axis so "how far past" is visible. */}
        <div
          className="absolute top-0 h-full w-0.5 bg-primary"
          style={{ left: `${thresholdPct}%` }}
          aria-hidden="true"
        />
      </div>

      <p className="mt-1 text-[11px] text-tertiary">
        Bar scaled to {ceiling.toPrecision(3)} — this analyzer only. Tick mark is the
        threshold. <span className="font-mono">{result.model_version}</span>
      </p>
    </li>
  );
}
