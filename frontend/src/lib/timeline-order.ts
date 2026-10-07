/**
 * Timeline ordering.
 *
 * Extracted from the component so it can be tested: `npm test` runs `node --test`
 * with no React renderer, and the bug this replaces was a `.localeCompare` on a
 * nullable field — a crash that no type-checker caught, because the hand-written
 * `TimelineEventRead` declared `occurred_at: string` while the API sends `null`.
 *
 * **Newest first, and undated events last.** A timeline is read by asking "what
 * happened, in what order", so the order has to be total and it has to be explainable.
 * The backend sorts ascending with `nullslast`; this mirrors that so the two agree,
 * and inverts it for display because a case is read most-recent-first.
 *
 * Undated events sort last rather than being dropped or floated to the top. Two
 * reasons, and the second is the important one: dropping them would hide real
 * entries from an examiner, and floating them to the top would place an event with
 * no known time above ones that do — an ordering claim the data cannot support.
 * `occurred_at` is nullable precisely because "we do not know when" is a legitimate
 * state, and the record has to show that honestly rather than invent a position.
 */
import type { TimelineEventRead } from "@/lib/types";

export interface OrderedTimelineEvent extends TimelineEventRead {
  /** Where this entry sits in the display order, and why. For the UI to explain. */
  ordering: "dated" | "undated";
}

export function orderTimeline(
  events: readonly TimelineEventRead[],
): OrderedTimelineEvent[] {
  return events
    .map((event) => ({
      ...event,
      ordering: event.occurred_at ? ("dated" as const) : ("undated" as const),
    }))
    .sort((a, b) => {
      // Undated last. Comparing nulls as strings is what threw.
      if (a.ordering === "undated" && b.ordering === "undated") return 0;
      if (a.ordering === "undated") return 1;
      if (b.ordering === "undated") return -1;
      return b.occurred_at!.localeCompare(a.occurred_at!);
    });
}
