import type { TimelineEventRead } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { Card, CardBody, EmptyState, Notice, Badge } from "@/components/ui";

/**
 * Case timeline.
 *
 * A vertical list with the timestamp as the primary visual element, because the
 * question a timeline answers is "in what order, and how far apart" — a question a
 * card grid actively hides. Seconds are shown; a dispute about which of two files
 * arrived first cannot be settled to the minute.
 */
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
          hint="Events appear as evidence is uploaded, analyses run and findings are recorded."
        />
      </Card>
    );
  }

  const ordered = events
    .slice()
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at));

  return (
    <Card>
      <CardBody>
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
                  <time
                    dateTime={event.occurred_at}
                    className="font-mono text-[11px] text-tertiary"
                  >
                    {formatDateTime(event.occurred_at)}
                  </time>
                  <Badge>{event.kind}</Badge>
                </div>
                <div className="mt-1 text-sm font-medium">{event.label}</div>
                {event.detail ? (
                  <p className="mt-0.5 text-xs leading-relaxed text-secondary">
                    {event.detail}
                  </p>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}
