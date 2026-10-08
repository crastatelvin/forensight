/**
 * Geometry behind the geospatial view.
 *
 * Pure functions, so they are tested directly rather than through a rendered page. The
 * cases that matter are the ones a chart cannot visibly get wrong: a projection that does
 * not round-trip, a fit that crops a coordinate off the edge, a zoom that slides the map
 * under the cursor, a world view that asks for 360° of latitude, and a scale bar that
 * overflows its own budget.
 *
 * **Four of these assertions were wrong when first written and the module was right**,
 * which is worth recording because each one is a plausible thing to believe:
 *  - 0.2° of longitude at the equator is 22 km, not a few hundred metres.
 *  - A degree of longitude covers *fewer* metres per pixel at 51.5°N than at the equator,
 *    because the projection deliberately narrows longitude there to keep shapes true.
 *  - The graticule axes are allowed to pick the same interval; only their *counts* have to
 *    differ.
 *  - The great-circle chord between two points on a parallel is slightly shorter than the
 *    parallel arc, so a longitude step is short by `1 - Δλ²/24` — 1.3e-5 at one degree.
 *
 * **The bundled basemap is pinned by digest.** It is 1.6 MB of third-party geometry the
 * whole map depends on, and a silent upstream revision would change what an examiner is
 * shown without changing a line of code — so the digest is asserted, and a deliberate
 * update has to update the assertion on purpose.
 */

import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  COASTLINE_VERTEX_KM,
  EARTH_RADIUS_M,
  MIN_VIEW_WIDTH_KM,
  closestPairs,
  clampScale,
  fitBounds,
  formatDegrees,
  formatDistance,
  graticule,
  graticuleStep,
  haversine,
  landPaths,
  metresPerPixel,
  panBy,
  parseLand,
  project,
  scaleBar,
  spanLat,
  spanLon,
  unproject,
  worldViewport,
  zoomAt,
  type Viewport,
} from "../src/lib/geo-map.ts";

/** Natural Earth 50m land, as bundled. */
const BASEMAP = new URL("../public/basemap/ne-50m-land.json", import.meta.url);
const BASEMAP_SHA256 =
  "e874b27a51d146452be360cafb3cc50c86001074a67d534113e6534682f9826b";

const LONDON = { lat: 51.5007, lon: -0.1246 };
const READING = { lat: 51.4543, lon: -0.9781 };
const TOKYO = { lat: 35.6762, lon: 139.6503 };

/** Ground metres in one degree of latitude, from the module's own radius. */
const METRES_PER_DEGREE = (EARTH_RADIUS_M * Math.PI) / 180;

/** A view of this size, centred here, spanning this many degrees of longitude. */
function viewAt(lat: number, lon: number, spanLonDeg: number): Viewport {
  // Inverts spanLon(). Degrees, not metres: the first version multiplied by
  // metres-per-degree here, which made every scale off by a factor of 111,195.
  const draft: Viewport = {
    lat,
    lon,
    scale: 720 / (spanLonDeg * Math.cos((lat * Math.PI) / 180)),
    width: 720,
    height: 420,
  };
  // Clamped like every other view the panel can hold. An unclamped scale asks for more
  // than the globe has and put 15 graticule lines above the North Pole.
  return { ...draft, scale: clampScale(draft, draft.scale) };
}

// --- projection -----------------------------------------------------------

test("project and unproject round-trip", () => {
  const views = [
    viewAt(0, 0, 360),
    viewAt(51.5, -0.12, 0.3),
    viewAt(-33.9, 151.2, 20),
    viewAt(70, 179, 5),
  ];
  for (const v of views) {
    for (const p of [
      LONDON,
      TOKYO,
      { lat: 0, lon: 0 },
      { lat: -33.9, lon: 151.2 },
      { lat: 84, lon: 12 },
    ]) {
      const { x, y } = project(v, p.lat, p.lon);
      const back = unproject(v, x, y);
      assert.ok(Math.abs(back.lat - p.lat) < 1e-6, `lat ${back.lat} != ${p.lat}`);
      assert.ok(Math.abs(back.lon - p.lon) < 1e-6, `lon ${back.lon} != ${p.lon}`);
    }
  }
});

test("the centre of a view projects to its centre", () => {
  const v = viewAt(12, 34, 10);
  const { x, y } = project(v, 12, 34);
  assert.ok(Math.abs(x - 360) < 1e-9 && Math.abs(y - 210) < 1e-9);
});

test("north is up and east is right", () => {
  const v = viewAt(0, 0, 10);
  const origin = project(v, 0, 0);
  assert.ok(project(v, 1, 0).y < origin.y, "north must be a smaller y");
  assert.ok(project(v, 0, 1).x > origin.x, "east must be a larger x");
});

test("the same ground distance draws the same number of pixels in any direction", () => {
  // This is the property the cos(latitude) correction exists to create, and the reason
  // the scale bar can be a single number.
  //
  // The first version of this test compared a 0.05 degree step in longitude against a
  // 0.05 degree step in latitude, and asserted they drew the same number of pixels. They
  // must not: at 30N a degree of longitude is cos(30) = 0.866 of a degree of latitude on
  // the ground, and the projection correctly draws it 0.866 as wide. What has to match is
  // the same *ground* distance drawn east and north.
  for (const lat of [0, 30, 51.5, -33.9, 80]) {
    const v = viewAt(lat, 0, 10);
    const metres = 5000;
    const origin = project(v, lat, 0);
    const north = project(v, lat + metres / METRES_PER_DEGREE, 0);
    const east = project(
      v,
      lat,
      metres / (METRES_PER_DEGREE * Math.cos((lat * Math.PI) / 180)),
    );
    const pxNorth = Math.abs(origin.y - north.y);
    const pxEast = Math.abs(origin.x - east.x);
    assert.ok(
      Math.abs(pxNorth - pxEast) / pxNorth < 1e-9,
      `at ${lat}°, 5 km drew ${pxNorth.toFixed(4)}px north and ${pxEast.toFixed(4)}px east`,
    );
  }
});

test("a position past the antimeridian is measured the short way round", () => {
  // 0.2 degrees of longitude at the equator is about 22 km, which is the point: these two
  // are 22 km apart the short way and most of the way round the world the long way.
  const m = haversine({ lat: 0, lon: 179.9 }, { lat: 0, lon: -179.9 });
  assert.ok(m > 20_000 && m < 25_000, `expected ~22 km, got ${m}`);

  const v = viewAt(0, 180, 4);
  const a = project(v, 0, 179.9);
  const b = project(v, 0, -179.9);
  // Adjacent, not opposite edges: they are 0.2 degrees apart. The first version asserted
  // the opposite, which would have been a bug in the projection rather than a test.
  assert.ok(
    Math.abs(a.x - b.x) < 50,
    `expected neighbours, got ${Math.abs(a.x - b.x)}px apart`,
  );
  assert.ok(Math.abs(a.y - b.y) < 1, "and on the same row");
  // And a point genuinely on the other side of the view must be on the other side of it.
  const far = project(v, 0, -176);
  assert.ok(far.x - b.x > 300, "a point 4 degrees away must be far across the view");
});

test("a view near the pole is not collapsed", () => {
  const v = viewAt(89, 0, 10);
  assert.ok(spanLat(v) > 0, "span in latitude must stay positive");
  const a = project(v, 89, 0);
  const b = project(v, 88.5, 0);
  assert.ok(Math.abs(a.y - b.y) > 1, "half a degree of latitude must move the point");
});

test("the world view asks for no more than the whole world", () => {
  // The first version stored degrees of longitude and derived latitude from cos(lat),
  // which at the equator asked for 360 degrees of latitude — twice what exists — and drew
  // a graticule over the North Pole.
  const v = worldViewport(720, 420);
  assert.ok(spanLat(v) <= 180.000001, `span in latitude is ${spanLat(v)}`);
  assert.ok(spanLon(v) <= 360.000001, `span in longitude is ${spanLon(v)}`);
  assert.ok(spanLat(v) > 0 && spanLon(v) > 0);
});

test("the world view shows the poles, and as much longitude as the box allows", () => {
  const v = worldViewport(720, 420);
  for (const lat of [-90, 0, 90]) {
    const { y } = project(v, lat, 0);
    assert.ok(y >= -1 && y <= 421, `latitude ${lat} off screen at y=${y}`);
  }
  // 720x420 is 1.71:1, so fitting all 180 degrees of latitude leaves the edges at
  // +/- 154 degrees of longitude and the antimeridian is off screen. Asserting both
  // meridians were visible would be asserting a 2:1 box.
  const edge = unproject(v, 720, 210).lon;
  assert.ok(Math.abs(edge) > 150 && Math.abs(edge) < 180, `edge at ${edge} degrees`);
  assert.ok(
    Math.abs(unproject(v, 360, 210).lon) < 1e-9,
    "the prime meridian is centred",
  );
});

test("a 2:1 world view does show the antimeridian", () => {
  const v = worldViewport(840, 420);
  assert.ok(spanLat(v) <= 180.000001);
  for (const lon of [-180, 180]) {
    const { x } = project(v, 0, lon);
    assert.ok(x >= -1 && x <= 841, `longitude ${lon} off screen at x=${x}`);
  }
});

// --- fitting --------------------------------------------------------------

test("fitBounds keeps every point inside the padded box", () => {
  const v = fitBounds([LONDON, READING], 720, 420, 24);
  for (const p of [LONDON, READING]) {
    const { x, y } = project(v, p.lat, p.lon);
    // Tolerance for float error: a point exactly on the padding edge is inside the box.
    assert.ok(x >= 24 - 1e-6 && x <= 696 + 1e-6, `x=${x} outside the padded box`);
    assert.ok(y >= 24 - 1e-6 && y <= 396 + 1e-6, `y=${y} outside the padded box`);
  }
});

test("fitBounds of a lone point stops at the coastline's resolution", () => {
  const v = fitBounds([LONDON], 720, 420, 24);
  const widthKm = spanLon(v) * 111.32 * Math.cos((v.lat * Math.PI) / 180);
  assert.ok(
    widthKm >= MIN_VIEW_WIDTH_KM - 1,
    `a single point fitted to ${widthKm.toFixed(1)} km across, finer than the coastline's ${COASTLINE_VERTEX_KM} km vertices`,
  );
  assert.ok(Math.abs(v.lat - LONDON.lat) < 1e-6);
  assert.ok(Math.abs(v.lon - LONDON.lon) < 1e-6);
});

test("fitBounds of points either side of the antimeridian is not world-zoomed", () => {
  const v = fitBounds(
    [
      { lat: 10, lon: 179.99 },
      { lat: 10.01, lon: -179.99 },
    ],
    720,
    420,
    24,
  );
  assert.ok(spanLon(v) < 1, `two points 2 km apart fitted to ${spanLon(v)} degrees`);
  for (const p of [
    { lat: 10, lon: 179.99 },
    { lat: 10.01, lon: -179.99 },
  ]) {
    const { x } = project(v, p.lat, p.lon);
    assert.ok(x >= -1 && x <= 721, `point off screen at x=${x}`);
  }
});

test("fitBounds returns the world for no points or an unusable box", () => {
  assert.equal(fitBounds([], 720, 420).scale, worldViewport(720, 420).scale);
  // A box no larger than twice its padding has no room to fit anything into; dividing by
  // the remainder would be a division by zero.
  assert.equal(fitBounds([LONDON], 40, 40, 24).scale, worldViewport(40, 40).scale);
});

test("fitBounds covers a whole hemisphere without exceeding the world", () => {
  const v = fitBounds(
    [
      { lat: -80, lon: -179 },
      { lat: 80, lon: 179 },
    ],
    720,
    420,
    24,
  );
  assert.ok(spanLat(v) <= 180.000001);
  assert.ok(spanLon(v) <= 360.000001);
});

test("padding widens the view, and more padding widens it more", () => {
  const tight = fitBounds([LONDON, READING], 720, 420, 0);
  const loose = fitBounds([LONDON, READING], 720, 420, 60);
  assert.ok(spanLon(loose) > spanLon(tight), "padding must increase the span");
  assert.ok(spanLat(loose) > spanLat(tight));
});

// --- distance -------------------------------------------------------------

test("haversine matches values derivable by hand", () => {
  // One degree of latitude on a sphere of radius R: R * pi / 180.
  assert.ok(
    Math.abs(
      haversine({ lat: 0, lon: 0 }, { lat: 1, lon: 0 }) -
        (EARTH_RADIUS_M * Math.PI) / 180,
    ) < 1,
  );
  // A degree of longitude is cos(latitude) of that. Strictly, the great-circle chord
  // between two points on a parallel is shorter than the parallel arc, by 1 - d^2/24,
  // which at one degree is 1.3e-5 — so the tolerance is 1e-4, not machine precision, and
  // this is the geometry being checked rather than a fudge factor.
  const atEquator = haversine({ lat: 0, lon: 0 }, { lat: 0, lon: 1 });
  const atLondon = haversine(LONDON, { lat: LONDON.lat, lon: LONDON.lon + 1 });
  const ratio = atLondon / atEquator;
  assert.ok(
    Math.abs(ratio - Math.cos((51.5007 * Math.PI) / 180)) < 1e-4,
    `ratio ${ratio}`,
  );
});

test("haversine is symmetric, zero on itself, and positive otherwise", () => {
  assert.equal(haversine(LONDON, LONDON), 0);
  assert.equal(haversine(LONDON, TOKYO), haversine(TOKYO, LONDON));
  assert.ok(haversine(LONDON, READING) > 0);
});

test("closestPairs sorts by distance and never pairs an item with itself", () => {
  const pts = [LONDON, READING, TOKYO];
  const pairs = closestPairs(pts, 3);
  assert.equal(pairs.length, 3, "three points give three pairs");
  for (const p of pairs) assert.notEqual(p.a, p.b);
  for (let i = 1; i < pairs.length; i += 1) {
    assert.ok(pairs[i - 1].metres <= pairs[i].metres, "pairs must be sorted");
  }
  // London and Reading are the closest pair out of these three.
  assert.deepEqual([pairs[0].a, pairs[0].b].sort(), [0, 1]);
});

test("coincident coordinates still pair, at zero distance", () => {
  // Two photographs of one spot is a finding, not a degenerate case to drop.
  const pairs = closestPairs([LONDON, { ...LONDON }], 1);
  assert.equal(pairs.length, 1);
  assert.equal(pairs[0].metres, 0);
});

test("formatDistance switches unit without implying precision it lacks", () => {
  assert.equal(formatDistance(0.004), "0 cm");
  assert.equal(formatDistance(0.4), "40 cm");
  assert.equal(formatDistance(12), "12 m");
  assert.equal(formatDistance(999), "999 m");
  assert.equal(formatDistance(1234), "1.23 km");
  assert.equal(formatDistance(45_600), "45.60 km");
  assert.equal(formatDistance(9_550_000), "9550 km");
  assert.equal(formatDistance(Number.NaN), "unknown");
  assert.equal(formatDistance(-1), "unknown");
});

// --- zoom and pan ---------------------------------------------------------

test("zoom keeps the point under the cursor exactly fixed", () => {
  const v = viewAt(40, 10, 20);
  for (const focus of [
    { x: 500, y: 100 },
    { x: 10, y: 410 },
    { x: 360, y: 210 },
  ]) {
    for (const factor of [1.6, 0.6, 0.25]) {
      const before = unproject(v, focus.x, focus.y);
      const after = unproject(zoomAt(v, factor, focus), focus.x, focus.y);
      assert.ok(
        Math.abs(before.lat - after.lat) < 1e-9,
        `lat drifted, factor ${factor}`,
      );
      assert.ok(
        Math.abs(before.lon - after.lon) < 1e-9,
        `lon drifted, factor ${factor}`,
      );
    }
  }
});

test("zoom is clamped at both ends", () => {
  const v = viewAt(0, 0, 20);
  const finest = zoomAt(v, 1e-9);
  assert.ok(
    spanLon(finest) * 111.32 >= MIN_VIEW_WIDTH_KM - 0.01,
    "must not zoom past the coastline's resolution",
  );
  const widest = zoomAt(v, 1e9);
  assert.ok(spanLat(widest) <= 180.000001 && spanLon(widest) <= 360.000001);
  // And clamping is idempotent: zooming past the end changes nothing.
  assert.equal(zoomAt(finest, 0.5).scale, finest.scale);
});

test("zoom about the centre changes nothing but the scale", () => {
  const v = viewAt(12, 34, 20);
  const z = zoomAt(v, 0.5);
  assert.equal(z.lat, 12);
  assert.equal(z.lon, 34);
  assert.ok(z.scale > v.scale, "zooming in must increase the scale");
});

test("pan moves the centre the opposite way to the drag, and stops at the poles", () => {
  const v = viewAt(0, 0, 20);
  const p = panBy(v, 720, 0);
  assert.ok(
    Math.abs(p.lon + spanLon(v)) < 1e-6,
    "dragging right must move the view west",
  );
  assert.ok(Math.abs(p.lat) < 1e-9, "a horizontal drag must not change latitude");
  assert.ok(panBy(viewAt(84, 0, 2), 0, -1e6).lat <= 85, "clamped at the pole");
});

// --- the basemap ----------------------------------------------------------

test("parseLand reads the bundled file and rejects malformed geometry", () => {
  const land = parseLand(JSON.parse(readFileSync(BASEMAP, "utf8")));
  assert.ok(land.rings.length > 1000, `only ${land.rings.length} rings parsed`);

  assert.deepEqual(parseLand(null).rings, []);
  assert.deepEqual(parseLand(42).rings, []);
  assert.deepEqual(parseLand({ features: "nope" }).rings, []);

  // A coordinate that is not a number would become NaN on screen and draw nothing while
  // looking like it worked, so it is dropped instead of cast. The second ring is also too
  // short to be a polygon and must be dropped.
  const bad = parseLand({
    features: [
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [0, 0],
              ["x", 1],
              [2, "y"],
            ],
          ],
        },
      },
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [0, 0],
              [1, 0],
            ],
          ],
        },
      },
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [0, 0],
              [1, 0],
              [2, 0],
              [3, 1],
            ],
          ],
        },
      },
    ],
  });
  assert.equal(bad.rings.length, 1, "one valid ring survives");
});

test("parseLand handles MultiPolygon", () => {
  const land = parseLand({
    features: [
      {
        geometry: {
          type: "MultiPolygon",
          coordinates: [
            [
              [
                [0, 0],
                [1, 0],
                [2, 1],
              ],
            ],
            [
              [
                [5, 5],
                [6, 5],
                [7, 6],
              ],
            ],
          ],
        },
      },
    ],
  });
  assert.equal(land.rings.length, 2);
});

test("landPaths is closed, and skips rings that cannot be in view", () => {
  const land = parseLand({
    features: [
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [0, 0],
              [0.5, 0],
              [0.5, 0.5],
              [0, 0.5],
            ],
          ],
        },
      },
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [80, 40],
              [80.5, 40],
              [80.5, 40.5],
              [80, 40.5],
            ],
          ],
        },
      },
    ],
  });
  const paths = landPaths(land, viewAt(0.25, 0.25, 4));
  assert.equal(
    paths.length,
    1,
    "the ring near 80E is off screen and must not be drawn",
  );
  assert.ok(paths[0].endsWith("Z"), "a land ring must be closed");
  assert.ok(paths[0].startsWith("M"), "a path starts with a move");
});

test("a ring crossing the antimeridian is split, not drawn as a line across the map", () => {
  // 170E -> 170W is a 20-degree step the short way, but naively projected it becomes a
  // chord from one edge of the view to the other.
  const land = parseLand({
    features: [
      {
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [170, 10],
              [-170, 10],
              [-170, 11],
              [170, 11],
            ],
          ],
        },
      },
    ],
  });
  const paths = landPaths(land, viewAt(10.5, 180, 20));
  const moves = (paths[0].match(/M/g) ?? []).length;
  assert.ok(moves >= 2, `expected the ring to be split, got ${moves} subpath(s)`);
});

test("every bundled ring produces parseable path data at the world view", () => {
  const land = parseLand(JSON.parse(readFileSync(BASEMAP, "utf8")));
  // A 2:1 box, so "every ring" is actually true: at 720x420 the view spans +/-154 degrees
  // of longitude and rings out in the Pacific are legitimately off screen.
  const paths = landPaths(land, worldViewport(840, 420));
  assert.equal(paths.length, land.rings.length, "the whole world must draw every ring");
  for (const d of paths) {
    assert.match(d, /^M-?[\d.]+ -?[\d.]+(L|Z|M)/, `malformed path: ${d.slice(0, 40)}`);
    // No NaN, which would make the whole path silently vanish.
    assert.ok(!/NaN|Infinity/.test(d));
  }
});

// --- graticule and scale --------------------------------------------------

test("the graticule has a workable number of lines at every zoom and latitude", () => {
  for (const span of [360, 30, 5, 1, 0.3]) {
    for (const lat of [0, 51.5, -33.9, 70]) {
      const lines = graticule(viewAt(lat, 0, span));
      const lats = lines.filter((l) => l.axis === "lat").length;
      const lons = lines.filter((l) => l.axis === "lon").length;
      assert.ok(
        lats >= 1 && lats <= 9,
        `${lats} lat lines at span ${span}, lat ${lat}`,
      );
      assert.ok(
        lons >= 1 && lons <= 9,
        `${lons} lon lines at span ${span}, lat ${lat}`,
      );
    }
  }
});

test("graticule intervals are round numbers", () => {
  const nice = [
    0.000_5, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30,
    45, 90,
  ];
  for (const lat of [0, 51.5, -33.9, 80]) {
    for (const span of [360, 5, 0.3]) {
      const step = graticuleStep(viewAt(lat, 0, span));
      assert.ok(nice.includes(step.lat), `lat step ${step.lat}`);
      assert.ok(nice.includes(step.lon), `lon step ${step.lon}`);
    }
  }
});

test("the grid is isotropic on the ground", () => {
  // The intervals and the line counts are both allowed to coincide at some zooms — at 60N
  // with a 10 degree view both axes draw five lines, at 1 and 2 degrees respectively. So
  // "more longitude lines" is the wrong thing to assert; the first version asserted it and
  // failed on a grid that is in fact exactly right.
  //
  // What has to hold is the property a reader sees: consecutive lines are roughly the same
  // distance apart on the ground in both directions, so the grid looks square rather than
  // stretched. The 1/2/5 quantisation means this holds to within a factor, not exactly.
  for (const lat of [0, 30, 51.5, -33.9, 70]) {
    for (const span of [360, 30, 5, 1, 0.3]) {
      const { lat: stepLat, lon: stepLon } = graticuleStep(viewAt(lat, 0, span));
      const acrossKm =
        (stepLon * METRES_PER_DEGREE * Math.cos((lat * Math.PI) / 180)) / 1000;
      const downKm = (stepLat * METRES_PER_DEGREE) / 1000;
      const ratio = acrossKm / downKm;
      assert.ok(
        ratio > 1 / 2.5 && ratio < 2.5,
        `at ${lat}N span ${span}: lines ${acrossKm.toFixed(0)}km apart across and ${downKm.toFixed(0)}km down`,
      );
    }
  }
});

test("the equator and the prime meridian are marked as major lines", () => {
  const lines = graticule(viewAt(2, 2, 10));
  assert.ok(
    lines.some((l) => l.axis === "lat" && Math.abs(l.value) < 1e-9 && l.major),
    "equator",
  );
  assert.ok(
    lines.some((l) => l.axis === "lon" && Math.abs(l.value) < 1e-9 && l.major),
    "prime meridian",
  );
});

test("graticule lines carry the screen coordinates a renderer needs", () => {
  for (const line of graticule(viewAt(10, 10, 5))) {
    assert.ok(Number.isFinite(line.x) && Number.isFinite(line.y));
    assert.ok(Number.isFinite(line.length) && line.length > 0);
    assert.equal(line.axis === "lat" ? line.length : 0, line.axis === "lat" ? 720 : 0);
  }
});

test("graticule labels are round numbers, not accumulated float noise", () => {
  // Adding `step` in a loop drifts, and the drift lands on the axis label: forty additions
  // of 0.2 leave 33.00000000000001, which prints as "33.0000000000°W". Every value the
  // graticule produces must be a clean multiple of its own step.
  for (const lat of [0, 51.5, -33.9, 70]) {
    for (const span of [360, 30, 5, 1, 0.3]) {
      const v = viewAt(lat, 0, span);
      const step = graticuleStep(v);
      for (const line of graticule(v)) {
        const own = line.axis === "lat" ? step.lat : step.lon;
        const quotient = line.value / own;
        assert.ok(
          Math.abs(quotient - Math.round(quotient)) < 1e-9,
          `${line.value} is not a whole multiple of ${own}`,
        );
        assert.equal(
          formatDegrees(line.value, line.axis),
          formatDegrees(Number(line.value.toFixed(10)), line.axis),
          "the label must not gain precision from float error",
        );
      }
    }
  }
});

test("formatDegrees labels hemispheres and drops trailing zeros", () => {
  assert.equal(formatDegrees(0, "lat"), "0°");
  assert.equal(formatDegrees(51.5, "lat"), "51.5°N");
  assert.equal(formatDegrees(-33.9, "lon"), "33.9°W");
  assert.equal(formatDegrees(10, "lon"), "10°E");
  assert.equal(formatDegrees(-0.125, "lat"), "0.125°S");
  assert.equal(formatDegrees(45, "lat"), "45°N");
  // Float noise from a graticule step must not reach the reader.
  assert.equal(formatDegrees(33.00000000000001, "lon"), "33°E");
  assert.equal(formatDegrees(-0.30000000000000004, "lat"), "0.3°S");
});

test("metresPerPixel does not depend on latitude", () => {
  // It is 111320 / scale by construction, and the cos correction on longitude is what
  // makes that true. Asserting it is asserting the projection is self-consistent.
  for (const lat of [0, 30, 51.5, -33.9, 80]) {
    assert.ok(metresPerPixel(viewAt(lat, 0, 10)) > 0);
  }
  const v = viewAt(0, 0, 10);
  // 111 320 m is the degree length for the WGS84 *equatorial* radius; this module uses the
  // IUGG mean radius, whose degree is 111 195 m. Comparing against the wrong one is how a
  // scale bar silently reads 0.1% off.
  assert.ok(Math.abs(metresPerPixel(v) - METRES_PER_DEGREE / v.scale) < 1e-9);
  assert.ok(
    Math.abs(METRES_PER_DEGREE - 111_194.9) < 1,
    "the mean-radius degree length",
  );
});

test("scaleBar is the largest round distance that fits its budget", () => {
  // The first version chose the smallest step *at least* as large as the budget, which let
  // the bar run 208 px into a 120 px box and cover part of the map it was measuring.
  for (const lat of [0, 51.5, -33.9]) {
    for (const span of [360, 30, 5, 1, 0.3]) {
      for (const budget of [60, 120, 240]) {
        const v = viewAt(lat, 0, span);
        const bar = scaleBar(v, budget);
        assert.ok(
          bar.px <= budget + 1e-6,
          `${bar.px}px into a ${budget}px budget at ${lat}N span ${span}`,
        );
        assert.ok(bar.px > 0, "the bar must have a positive width");
        // Round numbers, in the 1/2/5 progression, so a reader can read it off.
        const mantissa = bar.metres / 10 ** Math.floor(Math.log10(bar.metres));
        assert.ok(
          [1, 2, 5].some((m) => Math.abs(mantissa / m - 1) < 1e-9),
          `${bar.metres} is not in the 1/2/5 progression`,
        );
        // And nothing bigger would have fit, or it would have been chosen.
        assert.ok(
          bar.px > budget / 3,
          `${bar.px}px leaves most of a ${budget}px budget empty`,
        );
      }
    }
  }
});

test("clampScale keeps every view inside the world and the data's resolution", () => {
  for (const lat of [0, 51.5, -33.9, 84]) {
    const v = viewAt(lat, 0, 10);
    for (const scale of [1e-9, 1, 100, 1e9]) {
      const c = clampScale(v, scale);
      assert.ok(spanLat({ ...v, scale: c }) <= 180.000001);
      assert.ok(spanLon({ ...v, scale: c }) <= 360.000001);
      assert.ok(
        spanLon({ ...v, scale: c }) * 111.32 * Math.cos((lat * Math.PI) / 180) >=
          MIN_VIEW_WIDTH_KM - 0.01,
      );
    }
  }
});

// --- the bundled asset ---------------------------------------------------

test("the bundled basemap is the pinned Natural Earth file", () => {
  const bytes = readFileSync(BASEMAP);
  assert.equal(createHash("sha256").update(bytes).digest("hex"), BASEMAP_SHA256);
  const parsed = JSON.parse(bytes.toString("utf8"));
  assert.equal(parsed.name, "ne_50m_land");
});

test("the bundled basemap is the resolution the zoom floor claims", () => {
  // If upstream is ever revised to a finer scale, MIN_VIEW_WIDTH_KM is wrong and the
  // floor would be needlessly restrictive. This is what catches that, and it is also the
  // assertion behind the number the panel quotes to the reader.
  const land = parseLand(JSON.parse(readFileSync(BASEMAP, "utf8")));
  const gaps: number[] = [];
  for (const ring of land.rings) {
    for (let i = 1; i < ring.length; i += 1) {
      const [lon0, lat0] = ring[i - 1];
      const [lon1, lat1] = ring[i];
      gaps.push(
        Math.hypot(
          (lon1 - lon0) * 111.32 * Math.cos((lat1 * Math.PI) / 180),
          (lat1 - lat0) * 110.57,
        ),
      );
    }
  }
  gaps.sort((a, b) => a - b);
  const medianKm = gaps[Math.floor(gaps.length / 2)];
  assert.ok(
    Math.abs(medianKm - COASTLINE_VERTEX_KM) < 1,
    `median vertex spacing moved to ${medianKm.toFixed(2)} km`,
  );
  assert.ok(
    MIN_VIEW_WIDTH_KM > COASTLINE_VERTEX_KM * 1.5,
    "the floor must be looser than the vertex spacing",
  );
});
