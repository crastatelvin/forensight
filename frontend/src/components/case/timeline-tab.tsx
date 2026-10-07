/**
 * Case timeline.
 *
 * A vertical list with the timestamp as the primary visual element, because the
 * question a timeline answers is "in what order, and how far apart" — a question a
 * card grid actively hides. Seconds are shown; a dispute about which of two files
 * arrived first cannot be settled to the minute.
 *
 * **This component previously rendered three fields the API does not send** — `kind`,
 * `detail`, and a non-null `occurred_at` — because the TypeScript interface declared
 * them. The type-checker was clean and the tab threw on the first real response with
 * a null timestamp. The rendering below matches the wire, and the ordering lives in
 * `orderTimeline` where `node --test` can reach it.
 */
import type { TimelineEventRead } from "@/lib/types";
import { orderTimeline } from "@/lib/timeline-order";
import { formatDateTime } from "@/lib/format";
import { Card, CardBody, EmptyState, Notice, Badge } from "@/components/ui";

export function TimelineTab({ events }: { events: TimelineEventRead[] | null }) {
  if (events === null) {
    return (
      <Notice tone="alert" title="Could not load the timeline">
        A missing timeline is not an empty one. Check the API before concluding nothing
        happened on this case.
      </Notice>
    );
  }

  if (events.length === 0) {
    return (
      <Card>
        <EmptyState
          icon="◷"
          title="No timeline events"
          hint="Events appear as evidence is uploaded, analyses run and findings are recorded. This case has none of those yet."
        />
      </Card>
    );
  }

  const ordered = orderTimeline(events);
  const derived = ordered.filter((e) => e.derived).length;

  return (
    <Card>
      <CardBody>
        {derived > 0 ? (
          <p className="mb-4 text-[11px] leading-relaxed text-tertiary">
            <strong className="font-medium text-secondary">{derived} derived</strong>{" "}
            entries come from this case&apos;s own records — evidence received, evidence
            accessed, analyses completed, findings recorded. Their timestamps are when
            the platform recorded each step, not when it happened in the world. Entries
            without a badge were entered by an examiner.
          </p>
        ) : null}
        <ol className="relative space-y-0">
          {ordered.map((event, i) => (
            <li key={event.id} className="relative flex gap-4 pb-5 last:pb-0">
              {/* The rail is drawn by the last item's `last:` variant rather than
                  by a wrapper with overflow-hidden, which would also clip a focus
                  ring on any control inside an event. */}
              {i < ordered.length - 1 ? (
                <span
                  aria-hidden="true"
                  className="absolute top-5 bottom-0 left-[7px] w-px bg-border-subtle"
                />
              ) : null}

              <span
                aria-hidden="true"
                className="relative mt-1.5 h-[15px] w-[15px] shrink-0 rounded-full border-2 border-accent bg-surface-0"
              />

              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {/*
                    No timestamp is a real state, not a missing value: an event may be
                    recorded with no known time. It says so in words rather than
                    rendering an empty element or — worse — the event's creation time,
                    which would be a different fact presented as the event's time.
                  */}
                  <time
                    dateTime={event.occurred_at ?? undefined}
                    className="font-mono text-[11px] text-tertiary"
                  >
                    {event.occurred_at
                      ? formatDateTime(event.occurred_at)
                      : "time unknown"}
                  </time>
                  {/*
                    `uncertain` is surfaced because the backend made it a first-class
                    field for exactly this reason: an entry the examiner is unsure of
                    must be visibly unsure in the UI, or the uncertainty exists only in
                    the database where nobody reading the case will see it.
                  */}
                  {/*
                    A derived entry is the platform's record of handling — an upload,
                    an access, an analysis finishing — not a conclusion someone drew
                    about the world. It is labelled so nobody reads "evidence received
                    at 14:02" as "the event happened at 14:02".
                  */}
                  {event.derived ? (
                    <Badge tone="info" title="Derived from this case's own records">
                      derived
                    </Badge>
                  ) : null}
                  {event.uncertain ? (
                    <Badge
                      tone="warn"
                      title="The examiner recorded this entry as uncertain"
                    >
                      uncertain
                    </Badge>
                  ) : null}
                  {event.source_ref ? (
                    <Badge title={`Source: ${event.source_ref}`}>
                      {event.source_ref}
                    </Badge>
                  ) : null}
                </div>
                <div className="mt-1 text-sm font-medium">{event.label}</div>
              </div>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}
