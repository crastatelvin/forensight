/**
 * Coordinate extraction guards.
 *
 * Pure functions, so they are tested directly rather than through a rendered page.
 * The cases that matter are the malformed ones: a coordinate that is present but
 * nonsense has to be dropped, because a marker in the wrong place is worse than no
 * marker — the panel's whole claim is that a plotted point came from the file.
 */

import assert from "node:assert/strict";
import test from "node:test";

import { collectCoordinates } from "../src/lib/geo.ts";
import type { AnalysisResultRead, EvidenceRead } from "../src/lib/types.ts";

function result(overrides: Partial<AnalysisResultRead> = {}): AnalysisResultRead {
  return {
    id: "r1",
    job_id: "j1",
    evidence_id: "e1",
    module: "image-exif",
    score: 0,
    threshold: 0.4,
    model_version: "1.0.0",
    tier: "B",
    disclaimer: "",
    payload: { detail: { gps: { latitude: 51.5, longitude: -0.1166667 } } },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function evidence(): EvidenceRead[] {
  return [
    {
      id: "e1",
      case_id: "c1",
      filename: "photo.jpg",
      sha256: "a".repeat(64),
      content_type: "image/jpeg",
      size_bytes: 1024,
      created_at: "2026-01-01T00:00:00Z",
    },
  ];
}

test("a coordinate is collected with the file it came from", () => {
  const points = collectCoordinates([result()], evidence());
  assert.equal(points.length, 1);
  assert.equal(points[0].latitude, 51.5);
  assert.equal(points[0].longitude, -0.1166667);
  // The filename is what makes the point useful, so it must be resolved.
  assert.equal(points[0].filename, "photo.jpg");
});

test("only image-exif contributes coordinates", () => {
  // A different analyzer carrying a `gps` key would otherwise put a mark on the
  // map that no EXIF read produced.
  const points = collectCoordinates(
    [
      result({
        module: "image-ela",
        payload: { detail: { gps: { latitude: 1, longitude: 2 } } },
      }),
    ],
    evidence(),
  );
  assert.deepEqual(points, []);
});

test("a coordinate outside the valid range is dropped, not clamped", () => {
  for (const gps of [
    { latitude: 91, longitude: 0 }, // past the pole
    { latitude: 0, longitude: 181 }, // past the date line
    { latitude: -91, longitude: 0 },
  ]) {
    const points = collectCoordinates(
      [result({ payload: { detail: { gps } } })],
      evidence(),
    );
    assert.deepEqual(
      points,
      [],
      `plotted an impossible coordinate: ${JSON.stringify(gps)}`,
    );
  }
});

test("non-finite numbers are dropped", () => {
  // `typeof NaN === "number"`, so a check that only tests the type would let these
  // through and plot them at a nonsense position while looking perfectly fine.
  for (const value of [
    Number.NaN,
    Number.POSITIVE_INFINITY,
    Number.NEGATIVE_INFINITY,
  ]) {
    const points = collectCoordinates(
      [result({ payload: { detail: { gps: { latitude: value, longitude: 0 } } } })],
      evidence(),
    );
    assert.deepEqual(points, [], `plotted ${value}`);
  }
});

test("a missing or wrongly-typed coordinate is dropped", () => {
  const cases = [
    { detail: {} },
    { detail: { gps: null } },
    { detail: { gps: {} } },
    { detail: { gps: { latitude: "51.5", longitude: 0 } } },
    { detail: { gps: { latitude: 51.5 } } }, // no longitude
  ];
  for (const payload of cases) {
    const points = collectCoordinates([result({ payload })], evidence());
    assert.deepEqual(points, [], `plotted from ${JSON.stringify(payload)}`);
  }
});

test("a null section contributes nothing rather than throwing", () => {
  assert.deepEqual(collectCoordinates(null, evidence()), []);
  assert.deepEqual(collectCoordinates([result()], null), []);
});

test("the raw DMS components travel with the point", () => {
  // They are what makes the plotted decimal checkable by hand.
  const points = collectCoordinates(
    [
      result({
        payload: {
          detail: {
            gps: {
              latitude: 51.5,
              longitude: -0.1166667,
              raw_latitude: ["51", "30", "0"],
              raw_longitude: ["0", "7", "0"],
            },
          },
        },
      }),
    ],
    evidence(),
  );
  assert.deepEqual(points[0].rawLatitude, ["51", "30", "0"]);
  assert.deepEqual(points[0].rawLongitude, ["0", "7", "0"]);
});

test("an unknown evidence id still yields a point", () => {
  // The coordinate is real and recorded; the filename is a convenience. Dropping
  // the point would hide a coordinate that genuinely exists.
  const points = collectCoordinates([result({ evidence_id: "gone" })], evidence());
  assert.equal(points.length, 1);
  assert.match(points[0].filename, /^gone|^[0-9a-f]{8}/);
});
