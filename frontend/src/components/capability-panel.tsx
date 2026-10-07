import type { CapabilitiesResponse, TierMeaningRead } from "@/lib/types";
import { explainModule, explainTier } from "@/lib/explain";
import { Card, CardHeader, CardBody, Notice, Badge } from "@/components/ui";

/**
 * What this deployment can actually analyse.
 *
 * This panel exists because the most damaging failure in a forensic tool is a
 * silent one: an operator believes a check ran, it did not, and the absence of a
 * finding is read as a clean result. So an unavailable analyzer is named, with the
 * reason, rather than omitted from the list — "the COCO model cannot detect
 * firearms" and "these weights are not installed here" are both things someone
 * must be able to see.
 */
export function CapabilityPanel({
  capabilities,
  tiers,
}: {
  capabilities: CapabilitiesResponse | null;
  /** Backend-authored tier wording; see TierMeaningRead. */
  tiers: TierMeaningRead[];
}) {
  if (!capabilities) {
    return (
      <Card>
        <CardHeader title="Analysis capabilities" />
        <CardBody>
          <Notice tone="warn">
            Could not read the capability list from the API. Analyzers may be available
            when this panel says nothing — check the API before concluding that a
            technique was skipped.
          </Notice>
        </CardBody>
      </Card>
    );
  }

  const unavailable = capabilities.capabilities.filter((c) => !c.available);

  return (
    <Card>
      <CardHeader
        title="Analysis capabilities"
        subtitle={`${capabilities.capabilities.filter((c) => c.available).length} of ${capabilities.capabilities.length} analyzers available on this host`}
      />
      <CardBody className="space-y-3">
        {unavailable.length > 0 ? (
          <Notice tone="warn" title="Some analyzers cannot run here">
            <ul className="mt-1 space-y-1">
              {unavailable.map((c) => (
                <li key={c.name}>
                  <span className="font-mono font-medium">{c.name}</span>
                  {!c.reason ? (
                    <> — no reason reported, which is itself worth reporting.</>
                  ) : (
                    <> — {c.reason}.</>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-2">
              An analyzer that cannot run produces no result. That is an absence of
              measurement, not a clean result.
            </p>
          </Notice>
        ) : null}

        <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {capabilities.capabilities.map((c) => {
            const explanation = explainModule(c.name);
            // The module's *own* tier, read from the backend. This used to be a
            // hardcoded `explainTier("A")`, which announced Tier A to screen
            // readers for all six analyzers when five of them are Tier B — the one
            // fact a reader consults this panel for, stated wrongly for most of it.
            const tier = explainTier(c.tier ?? "", tiers);
            return (
              <li
                key={c.name}
                className="rounded-md border border-border-subtle bg-surface-2/40 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <span className="font-mono text-xs font-semibold">{c.name}</span>
                  <Badge tone={c.available ? "calm" : "alert"}>
                    {c.available ? "available" : "unavailable"}
                  </Badge>
                </div>
                <div className="mt-1 font-mono text-[10px] text-tertiary">
                  {c.version}
                </div>
                {explanation ? (
                  <p className="mt-2 text-[11px] leading-relaxed text-secondary">
                    {explanation}
                  </p>
                ) : (
                  <p className="mt-2 text-[11px] leading-relaxed text-tertiary">
                    No plain-language description is recorded for this analyzer.
                  </p>
                )}
                {tier ? <span className="sr-only">{tier.meaning}</span> : null}
              </li>
            );
          })}
        </ul>
      </CardBody>
    </Card>
  );
}
