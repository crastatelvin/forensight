/**
 * Timeline ordering — FI-1 groundwork.
 *
 * The defect this pins: `TimelineTab` sorted with
 * `b.occurred_at.localeCompare(a.occurred_at)` while the API sends
 * `occurred_at: null` for an event with no known time. That is a `TypeError` on
 * every such event, and it shipped because `TimelineEventRead` was hand-written with
 * `occurred_at: string` — a type-checker told the component the field was always
 * present, which is exactly as wrong as the bug it was supposed to prevent.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { orderTimeline } from "../src/lib/timeline-order.ts";
import type { TimelineEventRead } from "../src/lib/types.ts";

/** A wire-shaped event. Defaults are the *nullable* ones, deliberately. */
function event(
  id: string,
  occurred: string | null,
  extra: Partial<TimelineEventRead> = {},
) {
  return {
    id,
    case_id: "c1",
    occurred_at: occurred,
    label: `event ${id}`,
    source_ref: "",
    uncertain: false,
    created_at: "2026-01-01T00:00:00Z",
    ...extra,
  };
}

test("an event with no known time does not throw", () => {
  // The regression itself. Sorting the mixed list used to raise a TypeError on the
  // null. An assertion is the only way this stays fixed, since tsc agrees with the
  // component's (wrong) assumption that the field is a string.
  const ordered = orderTimeline([event("a", "2026-03-01T10:00:00Z"), event("b", null)]);
  assert.equal(ordered.length, 2);
});

test("an all-undated timeline orders stably rather than arbitrarily", () => {
  // Every entry lacking a time is a case this has to survive: a case assembled
  // entirely from custody records with no EXIF, for instance.
  const ordered = orderTimeline([event("a", null), event("b", null), event("c", null)]);
  assert.deepEqual(
    ordered.map((e) => e.id),
    ["a", "b", "c"],
  );
});

test("dated events are newest first", () => {
  const ordered = orderTimeline([
    event("old", "2026-01-01T00:00:00Z"),
    event("new", "2026-06-01T00:00:00Z"),
    event("mid", "2026-03-01T00:00:00Z"),
  ]);
  assert.deepEqual(
    ordered.map((e) => e.id),
    ["new", "mid", "old"],
  );
});

test("undated events sort last, not first", () => {
  // Floating an event with no known time above dated ones would assert an ordering
  // the data cannot support — it would claim to be the most recent thing on the case.
  const ordered = orderTimeline([
    event("undated", null),
    event("dated", "2026-01-01T00:00:00Z"),
  ]);
  assert.deepEqual(
    ordered.map((e) => e.id),
    ["dated", "undated"],
  );
});

test("each event is labelled with why it sits where it does", () => {
  // So the UI can explain the position rather than leaving the examiner to guess
  // whether "time unknown" is an error or a fact about the case.
  const ordered = orderTimeline([event("a", null), event("b", "2026-01-01T00:00:00Z")]);
  assert.deepEqual(
    ordered.map((e) => [e.id, e.ordering]),
    [
      ["b", "dated"],
      ["a", "undated"],
    ],
  );
});

test("the dated events are exactly the reverse of ascending order", () => {
  // The backend lists with `order_by(occurred_at.asc().nullslast())`, so the display
  // order must be that list reversed. Only the *dated* subsequence can be compared
  // this way: undated events are last in both, and reversing the whole display list
  // moves them to the front, which is why an earlier version of this test asserted
  // the wrong thing and looked like a bug in the ordering.
  const events = [
    event("a", "2026-02-02T00:00:00Z"),
    event("b", null),
    event("c", "2026-01-01T00:00:00Z"),
    event("d", "2026-05-05T00:00:00Z"),
  ];
  const dated = orderTimeline(events)
    .filter((e) => e.ordering === "dated")
    .map((e) => e.id);
  assert.deepEqual(dated, ["d", "a", "c"]);
  // And the undated entry is still present and still last — comparing only the dated
  // subsequence must not become a way to drop it.
  const display = orderTimeline(events);
  assert.equal(display.at(-1)?.id, "b");
});

test("ordering does not mutate its input", () => {
  // `sort` mutates. The prop belongs to the page's bundle, and a tab switch must not
  // reorder what the next render sees.
  const events = [
    event("a", "2026-01-01T00:00:00Z"),
    event("b", "2026-06-01T00:00:00Z"),
  ];
  const before = events.map((e) => e.id);
  orderTimeline(events);
  assert.deepEqual(
    events.map((e) => e.id),
    before,
  );
});

test("uncertainty and provenance survive ordering", () => {
  // Both fields the tab renders. A sort that rebuilt the objects from a subset would
  // quietly drop them, and an entry whose certainty is hidden is worse than absent.
  const ordered = orderTimeline([
    event("a", "2026-01-01T00:00:00Z", { uncertain: true, source_ref: "CUSTODY-14" }),
  ]);
  assert.equal(ordered[0].uncertain, true);
  assert.equal(ordered[0].source_ref, "CUSTODY-14");
});

test("an empty timeline stays empty", () => {
  assert.deepEqual(orderTimeline([]), []);
});
