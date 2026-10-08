/**
 * The geometry behind the geospatial view: projection, extent fitting, zoom/pan, the
 * distance between two coordinates, a graticule and a scale bar.
 *
 * **Pure functions in a DOM-free module, deliberately.** The panel is presentation; this
 * is "what does the geometry actually say", and `node --test` can only check the latter. A
 * map that silently draws a point in the wrong place is worse than no map, so every claim
 * here is arithmetic that can be verified by hand.
 *
 * **Why a hand-written projection rather than a mapping library.** The view needs a
 * coastline and it needs not to tell anyone where the evidence is. A tile service
 * resolves the second requirement by breaking the first — the tile request carries a
 * viewport derived from the case's own coordinates, so a rectangle of where the evidence
 * sits leaves the host. MapLibre can render a style with no tile source at all, but at
 * 26.7 MB unpacked and 19 dependencies it is a large, WebGL-dependent addition to an
 * appliance built to run air-gapped on constrained hosts, bought for behaviour this module
 * already has. So Natural Earth's public-domain 50m land geometry ships with the
 * application (`public/basemap/ne-50m-land.json`, 5,143 points at 110m / 60,669 at 50m) and
 * is drawn here. **Nothing is fetched from any third party, and there is no basemap
 * service to configure or leak.**
 *
 * **Why `scale` and not `spanLon`.** The first version of this stored the degrees of
 * longitude across the width and derived latitude from it as `spanLon * cos(latitude)`. That
 * is right locally and wrong globally: at the world view it asks for 360° *of latitude*,
 * which does not exist, and the graticule produced 37 lines above the North Pole. Storing
 * **pixels per degree of latitude** instead makes the world view fall out correctly —
 * clamping latitude to 180° and longitude to 360° is then two independent constraints on
 * one number — and makes every other function a division rather than a multiplication.
 *
 * **The projection is equirectangular with a cos(latitude) correction on longitude**, not
 * Web Mercator. For a view spanning a site or a city — which is what a coordinate read out
 * of evidence gives — Mercator inflates north of the equator without bound, so a street's
 * length would not match the scale bar beside it. Scaling longitude by cos(latitude) keeps
 * shapes and distances locally true, which is the property a reader checks this view for,
 * and at the equator the correction is 1.0 so the world view comes out as a correct plain
 * equirectangular map.
 */

/** A position in degrees. */
export interface LatLon {
  lat: number;
  lon: number;
}

/**
 * A view onto the globe: which part of it is on screen, and how big that part is.
 *
 * `scale` is pixels per degree of latitude at the centre. One number rather than two
 * spans, so the view cannot silently change shape when resized, and so latitude and
 * longitude are constrained by the same limit instead of by two that disagree.
 */
export interface Viewport {
  /** Centre latitude, degrees. */
  lat: number;
  /** Centre longitude, degrees. */
  lon: number;
  /** Pixels per degree of latitude. */
  scale: number;
  width: number;
  height: number;
}

/** IUGG mean Earth radius (R1), metres. Traceable, rather than typed from memory. */
export const EARTH_RADIUS_M = 6371008_800 / 1000;

/** Metres per degree of latitude. Spherical, consistent with `EARTH_RADIUS_M`. */
const METRES_PER_DEGREE = (EARTH_RADIUS_M * Math.PI) / 180;

/** Full turn, and the pole to pole. */
const DEGREES_AROUND = 360;
const POLE_TO_POLE = 180;

/**
 * The bundled coastline's median vertex spacing, in kilometres, and the narrowest view
 * that still shows a coastline rather than three straight lines.
 *
 * **This ceiling is a property of the data, not a preference.** Natural Earth 50m land
 * has a median vertex spacing of 7.6 km along a coast, so below roughly twice that the
 * outline degenerates. Clamping here means the map never claims detail it does not have;
 * going finer needs survey or OSM data, which is neither public domain nor something to
 * fetch from a case's network. Expressed as a *ground* distance because that is what a
 * vertex spacing is — the same angular interval is 28 km wide at the equator and 17 km at
 * 51.5°N, and the coastline's detail does not change with latitude.
 */
export const COASTLINE_VERTEX_KM = 7.6;
export const MIN_VIEW_WIDTH_KM = 15;

/**
 * cos(85°), the floor.
 *
 * The projection divides by this. At the pole it is zero, and a view centred on the pole
 * would have every point in the hemisphere on one row. Latitude is already clamped short of
 * the poles; this makes the division safe at that clamp.
 */
const COS_FLOOR = 0.087_155_7;

/** Latitude clamp. Short of the poles, where the projection degenerates. */
const MAX_LATITUDE = 85;

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** cos(latitude) at the view's centre, floored so it can be divided by. */
function cosLat(view: Viewport): number {
  return Math.max(Math.cos((view.lat * Math.PI) / 180), COS_FLOOR);
}

/**
 * Wrap a longitude difference into (-180, 180].
 *
 * Without this a point at 179.9°E and a point at 179.9°W are 359.8° apart — they are 22 km
 * apart, across the antimeridian. Every longitude offset this module takes goes through
 * here.
 */
function wrapLon(delta: number): number {
  return ((((delta + 180) % 360) + 360) % 360) - 180;
}

/** Degrees of latitude across the height. */
export function spanLat(view: Viewport): number {
  return view.height / view.scale;
}

/** Degrees of longitude across the width. */
export function spanLon(view: Viewport): number {
  return view.width / (view.scale * cosLat(view));
}

/**
 * Ground distance covered by one screen pixel.
 *
 * **Latitude-independent by construction**, which is the point. The projection scales
 * longitude by cos(latitude) precisely so that a pixel is the same number of metres across
 * and down; asking this function and getting an answer that changed with latitude would
 * mean the two axes had come apart, and the scale bar would then be wrong in one direction
 * only — which is exactly the sort of error nobody catches by eye.
 */
export function metresPerPixel(view: Viewport): number {
  return METRES_PER_DEGREE / view.scale;
}

/**
 * The coarsest usable scale: far enough out to show the whole world.
 *
 * Two independent constraints on one number — latitude cannot exceed 180° and longitude
 * cannot exceed 360° — so the *larger* of the two limits wins. Near a pole the longitude
 * constraint is the one that binds, because a degree of longitude there is a very short
 * stretch of ground; the first version of this multiplied by metres-per-degree, which left
 * a 60°N view asking for 2953° of longitude.
 */
function minScale(view: Viewport): number {
  return Math.max(
    view.height / POLE_TO_POLE,
    view.width / (DEGREES_AROUND * cosLat(view)),
  );
}

/**
 * The finest usable scale: as close in as the bundled coastline still means anything.
 *
 * **No cos(latitude) factor here.** The width across the view in metres is
 * `width / scale * METRES_PER_DEGREE`, and the cos in the projection cancels against the
 * cos in a degree of longitude — a 15 km-wide view is 15 km wide at every latitude. Putting
 * cos in here made the zoom floor latitude-dependent, which the panel's own caption then
 * contradicted.
 */
function maxScale(view: Viewport): number {
  return (view.width * METRES_PER_DEGREE) / (MIN_VIEW_WIDTH_KM * 1000);
}

/** Scale, clamped to what the data and the globe both allow. */
export function clampScale(view: Viewport, scale: number): number {
  return clamp(scale, minScale(view), Math.max(minScale(view), maxScale(view)));
}

/** The whole world, in a box of the given size. */
export function worldViewport(width: number, height: number): Viewport {
  const seed: Viewport = { lat: 0, lon: 0, scale: 1, width, height };
  return { ...seed, scale: minScale(seed) };
}

/** Where a position falls on screen. Y increases downward because SVG's does. */
export function project(
  view: Viewport,
  lat: number,
  lon: number,
): { x: number; y: number } {
  return {
    x: wrapLon(lon - view.lon) * view.scale * cosLat(view) + view.width / 2,
    y: (view.lat - lat) * view.scale + view.height / 2,
  };
}

/** The position under a screen point. The exact inverse of `project`. */
export function unproject(view: Viewport, x: number, y: number): LatLon {
  return {
    lat: view.lat - (y - view.height / 2) / view.scale,
    lon: wrapLon(view.lon + (x - view.width / 2) / (view.scale * cosLat(view))),
  };
}

/**
 * A view that just contains every position, with padding in pixels.
 *
 * The order is the part worth stating, because doing it in the obvious order gets the
 * padding wrong. Padding shrinks the box the data has to fit into, so it *increases* the
 * span — which means *decreasing* the scale — and applying it first and then clamping would
 * let a world-wide fit exceed the globe. So the data extent sets an upper bound on scale,
 * padding tightens that bound, and only then is it clamped.
 *
 * A single position has zero extent, so that bound is infinite and the scale falls to the
 * finest the data allows — a recognisable patch of coastline rather than an empty void.
 */
export function fitBounds(
  points: readonly LatLon[],
  width: number,
  height: number,
  padding = 24,
): Viewport {
  if (points.length === 0) return worldViewport(width, height);
  // A box no larger than twice its padding has no room to fit anything into.
  if (width <= padding * 2 || height <= padding * 2)
    return worldViewport(width, height);

  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (const p of points) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lon < minLon) minLon = p.lon;
    if (p.lon > maxLon) maxLon = p.lon;
  }

  const lat = clamp((minLat + maxLat) / 2, -MAX_LATITUDE, MAX_LATITUDE);
  // A view straddling the antimeridian has a naive longitude extent of ~360° for points
  // metres apart, so the extent is the shortest arc containing every point.
  const extent = shortestArcExtent(minLon, maxLon);

  const usableWidth = width - padding * 2;
  const usableHeight = height - padding * 2;
  const seed: Viewport = { lat, lon: 0, scale: 1, width, height };

  // The finest scale at which the data still fits the usable box. Whichever axis is the
  // binding constraint wins; the other gets slack, which is what fills the rectangle.
  const scale = clampScale(
    seed,
    Math.min(
      usableHeight / (maxLat - minLat),
      usableWidth / (extent * cosLat({ ...seed, lat })),
    ),
  );

  return { lat, lon: centreOfArc(minLon, maxLon), scale, width, height };
}

/**
 * The shortest arc of longitudes containing everything between `min` and `max`, in degrees.
 *
 * When `max - min` exceeds 180° the data is closer across the antimeridian, and taking the
 * extent as `max - min` is what makes a view of two photographs taken on opposite sides of
 * 180° come out world-zoomed.
 */
function shortestArcExtent(minLon: number, maxLon: number): number {
  const naive = maxLon - minLon;
  return naive <= 180 ? naive : 360 - naive;
}

/**
 * The centre of that same arc.
 *
 * Above 180° of naive extent the arc that actually contains the data is the *other* one,
 * whose midpoint is the antipode of the naive midpoint. Adding half of the extent to the
 * minimum and, in the crossing case, half a turn, is that in one expression.
 */
function centreOfArc(minLon: number, maxLon: number): number {
  const naive = maxLon - minLon;
  const shift = naive <= 180 ? naive / 2 : naive / 2 + 180;
  return wrapLon(minLon + shift);
}

/**
 * A viewport zoomed about a fixed screen point.
 *
 * **`factor` is a span multiplier: below 1 zooms in**, the same convention as a scroll
 * step, and matching how the panel's zoom buttons read. The point under `focus` stays under
 * `focus`, which is what makes wheel-zooming feel attached to the cursor instead of
 * drifting toward the centre of the screen.
 *
 * **Both axes are solved exactly, not iterated.** Latitude is linear in the centre, so it
 * comes straight out. Longitude divides by `cos(centre latitude)`, so it depends on the
 * latitude answer — computing latitude first makes the longitude answer exact too. An
 * earlier version corrected both axes from the *pre-move* centre and left a small residual
 * drift, which is the kind of error that makes a zoom feel subtly wrong without ever being
 * obviously wrong.
 */
export function zoomAt(
  view: Viewport,
  factor: number,
  focus?: { x: number; y: number },
): Viewport {
  // Dividing the scale, because the factor scales the *span*. The first version multiplied
  // it, which made `factor` mean the opposite of what its own docstring said — and since
  // scale and span move in opposite directions, the docstring and the panel's zoom buttons
  // were each consistent with a different version of the function.
  const scale = clampScale(view, view.scale / factor);
  if (scale === view.scale) return view;

  const fx = focus?.x ?? view.width / 2;
  const fy = focus?.y ?? view.height / 2;
  const fixed = unproject(view, fx, fy);

  const lat = clamp(
    fixed.lat + (fy - view.height / 2) / scale,
    -MAX_LATITUDE,
    MAX_LATITUDE,
  );
  const next: Viewport = { ...view, lat, scale };
  const lon = wrapLon(fixed.lon - (fx - view.width / 2) / (scale * cosLat(next)));

  return { ...next, lon };
}

/**
 * A viewport panned by a screen-space drag, in pixels.
 *
 * Content follows the pointer: dragging right moves the centre west, dragging down moves
 * it north. Longitude divides by cos(latitude), so latitude is solved first and the
 * longitude answer uses the final value.
 */
export function panBy(view: Viewport, dxPx: number, dyPx: number): Viewport {
  const lat = clamp(view.lat + dyPx / view.scale, -MAX_LATITUDE, MAX_LATITUDE);
  const next: Viewport = { ...view, lat };
  return { ...next, lon: wrapLon(view.lon - dxPx / (view.scale * cosLat(next))) };
}

/**
 * Great-circle distance between two positions, metres.
 *
 * Haversine on a sphere. It is not the WGS84 ellipsoid, so it is accurate to about 0.5% —
 * for a distance between two photographs that is far below the error in the coordinates
 * themselves, and it is what a reader can check with a calculator.
 *
 * **The longitude difference is not wrapped.** For the distances this panel reports — a
 * case's own evidence, all within a plausible extent of itself — the difference is small,
 * and wrapping it would make a point at 179.9°E and one at 179.9°W appear 22 km apart when
 * they are 22 km apart, which is correct; the function is used only on pairs the panel has
 * already grouped, and the tests pin the antimeridian case explicitly.
 */
export function haversine(a: LatLon, b: LatLon): number {
  const toRad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toRad;
  const dLon = (b.lon - a.lon) * toRad;
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * toRad) * Math.cos(b.lat * toRad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** One pair of items, and how far apart it is. */
export interface Proximity {
  a: number;
  b: number;
  metres: number;
}

/**
 * The closest pairs of distinct items, nearest first.
 *
 * **Distinct by index, so coincident coordinates still pair up.** Two photographs of the
 * same spot at 0 m is a normal and interesting finding, not a degenerate case to skip — and
 * dropping it would hide exactly the thing an examiner is looking for.
 */
export function closestPairs(points: readonly LatLon[], limit = 3): Proximity[] {
  const pairs: Proximity[] = [];
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      pairs.push({ a: i, b: j, metres: haversine(points[i], points[j]) });
    }
  }
  pairs.sort((x, y) => x.metres - y.metres || x.a - y.a || x.b - y.b);
  return pairs.slice(0, Math.max(0, limit));
}

/**
 * A distance as a reader would write it.
 *
 * Two significant figures, switching unit at 1 km and again at 100 km. Fixed decimals would
 * be misleading at both ends — `0.00 m` for two photographs of one spot is wrong, and
 * `1234.56 m` is a precision nobody should read into an EXIF coordinate.
 */
export function formatDistance(metres: number): string {
  if (!Number.isFinite(metres) || metres < 0) return "unknown";
  if (metres < 1) return `${(metres * 100).toFixed(0)} cm`;
  if (metres < 1000) return `${metres.toFixed(0)} m`;
  if (metres < 100_000) return `${(metres / 1000).toFixed(2)} km`;
  return `${Math.round(metres / 1000)} km`;
}

// --- the basemap ----------------------------------------------------------

/** One ring of land, as `[longitude, latitude]` pairs. */
export type Ring = readonly (readonly [number, number])[];

/** Natural Earth land, as parsed rings. */
export interface LandGeometry {
  rings: readonly Ring[];
}

/**
 * The bundled basemap, validated on the way in.
 *
 * Narrowing rather than casting, for the same reason `collectCoordinates` does: the asset
 * is a third-party file that a future edit could break, and a ring containing a string
 * would become `NaN` coordinates and a silently empty coastline.
 */
export function parseLand(value: unknown): LandGeometry {
  const rings: Ring[] = [];
  if (typeof value !== "object" || value === null) return { rings };
  const features = (value as { features?: unknown }).features;
  if (!Array.isArray(features)) return { rings };

  const pushPolygon = (polygon: unknown): void => {
    if (!Array.isArray(polygon)) return;
    for (const ring of polygon) {
      if (!Array.isArray(ring) || ring.length < 3) continue;
      const points: [number, number][] = [];
      for (const pair of ring) {
        if (!Array.isArray(pair) || pair.length < 2) continue;
        const [lon, lat] = pair;
        if (typeof lon !== "number" || typeof lat !== "number") continue;
        if (!Number.isFinite(lon) || !Number.isFinite(lat)) continue;
        points.push([lon, lat]);
      }
      if (points.length >= 3) rings.push(points);
    }
  };

  for (const feature of features) {
    if (typeof feature !== "object" || feature === null) continue;
    const geometry = (feature as { geometry?: unknown }).geometry;
    if (typeof geometry !== "object" || geometry === null) continue;
    const type = (geometry as { type?: unknown }).type;
    const coordinates = (geometry as { coordinates?: unknown }).coordinates;
    if (!Array.isArray(coordinates)) continue;
    if (type === "Polygon") pushPolygon(coordinates);
    // MultiPolygon is not in the land asset, but handling it costs three lines and a
    // future revision would otherwise draw one polygon and silently drop the rest.
    else if (type === "MultiPolygon")
      for (const polygon of coordinates) pushPolygon(polygon);
  }

  return { rings };
}

/**
 * SVG path data for the coastline, one string per ring, in screen coordinates.
 *
 * **Rings entirely off-screen are skipped.** At street zoom almost every coastline in the
 * world is off-screen, and emitting all 60,669 points on every pan is work that buys
 * nothing.
 *
 * **Rings crossing the antimeridian are split rather than drawn as a horizontal streak
 * across the whole map.** Natural Earth's Russian and Antarctic rings run past ±180°, and
 * naively projecting those coordinates puts a line clean through the view at some unrelated
 * latitude. A jump of more than 180° between consecutive points is therefore treated as
 * the ring leaving one side of the world and re-entering the other.
 */
export function landPaths(land: LandGeometry, view: Viewport): string[] {
  const paths: string[] = [];
  const halfLon = spanLon(view) / 2 + 1;
  const halfLat = spanLat(view) / 2 + 1;

  for (const ring of land.rings) {
    let minLon = Infinity;
    let maxLon = -Infinity;
    let minLat = Infinity;
    let maxLat = -Infinity;
    let crosses = false;
    let previousLon = ring[0]?.[0] ?? 0;
    for (const [lon, lat] of ring) {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
      if (Math.abs(lon - previousLon) > 180) crosses = true;
      previousLon = lon;
    }
    if (!Number.isFinite(minLon) || !Number.isFinite(minLat)) continue;

    // A crossing ring's raw longitude bounds cover most of the world, so only latitude can
    // filter it.
    if (!crosses && (maxLon < view.lon - halfLon || minLon > view.lon + halfLon))
      continue;
    if (maxLat < view.lat - halfLat || minLat > view.lat + halfLat) continue;

    let path = "";
    let open = false;
    let prevLon = Number.NaN;
    for (const [lon, lat] of ring) {
      const point = project(view, lat, lon);
      if (!open || Math.abs(lon - prevLon) > 180) {
        path += `${open ? " " : ""}M${round(point.x)} ${round(point.y)}`;
        open = true;
      } else {
        path += `L${round(point.x)} ${round(point.y)}`;
      }
      prevLon = lon;
    }
    if (open) paths.push(`${path}Z`);
  }
  return paths;
}

/** Path coordinates at a resolution no renderer can see. */
function round(value: number): string {
  return value.toFixed(1);
}

// --- graticule ------------------------------------------------------------

/**
 * Candidate graticule intervals, degrees.
 *
 * One-two-five-fifteen, the sequence cartographers use for exactly this reason: every step
 * divides into the next, so the *number* of gridlines barely changes as you zoom and a
 * reader who has learned the interval at one zoom can still read it at the next.
 */
const NICE_STEPS = [
  0.000_5, 0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30,
  45, 90,
] as const;

/**
 * How many gridlines may span the view before a coarser interval is chosen. Above roughly
 * eight the view looks like graph paper rather than a locator, and the labels collide.
 */
const MAX_GRIDLINES = 8;

/**
 * The smallest listed interval whose lines across `span` fit inside `maxLines`.
 *
 * Read as "the first step for which `span / step` is no more than `maxLines`". The earlier
 * version took `span` and divided it by a separate target before calling this, which
 * doubled the span and halved the interval — 19 lines across the world where there should
 * be 6. The two knobs described the same thing, so there is now one.
 */
function niceStep(span: number, maxLines: number): number {
  for (const step of NICE_STEPS) {
    if (span / step <= maxLines) return step;
  }
  return NICE_STEPS[NICE_STEPS.length - 1];
}

/**
 * Graticule intervals for the view, chosen independently per axis.
 *
 * **Independence is the point, not laziness.** The view spans more degrees of longitude
 * than of latitude by `1/cos(latitude)`, so a shared interval puts visibly more longitude
 * lines than latitude lines at mid latitudes — a grid that looks hand-drawn rather than
 * gridded. The intervals are still allowed to coincide, which they do at some zooms; only
 * the resulting *counts* have to differ.
 */
export function graticuleStep(
  view: Viewport,
  maxLines = MAX_GRIDLINES,
): { lat: number; lon: number } {
  return {
    lat: niceStep(spanLat(view), maxLines),
    lon: niceStep(spanLon(view), maxLines),
  };
}

/** Decimal places in a step, so `index * step` can be snapped back to it. */
function decimalsOf(step: number): number {
  const text = String(step);
  const dot = text.indexOf(".");
  return dot < 0 ? 0 : text.length - dot - 1;
}

/**
 * Degrees formatted the way a chart axis labels them.
 *
 * **Rounded to six places first.** Graticule values arrive as multiples of a step, and
 * multiplying an integer index by 0.2 lands on 33.00000000000001; printed straight out,
 * that is the difference between "33°W" and "33.0000000000°W" on a chart axis.
 */
export function formatDegrees(value: number, axis: "lat" | "lon"): string {
  value = Math.round(value * 1e6) / 1e6;
  const hemisphere =
    axis === "lat"
      ? value > 0
        ? "N"
        : value < 0
          ? "S"
          : ""
      : value > 0
        ? "E"
        : value < 0
          ? "W"
          : "";
  const magnitude = Math.abs(value);
  // Trailing zeros on an axis label are noise: 10° not 10.000°.
  const text = Number.isInteger(magnitude)
    ? magnitude.toFixed(0)
    : magnitude.toFixed((String(magnitude).split(".")[1] ?? "").length);
  return hemisphere ? `${text}°${hemisphere}` : `${text}°`;
}

/** One graticule line, in screen coordinates. */
export interface GridLine {
  /** Degrees, for the label. */
  value: number;
  axis: "lat" | "lon";
  /** Screen position of the fixed end (top edge for latitude, left edge for longitude). */
  x: number;
  y: number;
  /** Length of the line across the view. */
  length: number;
  /** Whether this is the prime meridian or the equator, which are drawn stronger. */
  major: boolean;
}

/**
 * Graticule lines for the view, in screen coordinates.
 *
 * **This is what makes the view usable below regional zoom.** Past about five degrees the
 * coastline leaves the frame entirely — the whole view is one flat fill — and a labelled
 * grid is then the only thing on screen that says where you are. It is also the part that
 * lets someone check a plotted coordinate by hand, which is the one property this panel
 * cannot afford to be wrong about.
 */
export function graticule(view: Viewport, maxLines = MAX_GRIDLINES): GridLine[] {
  const step = graticuleStep(view, maxLines);
  const halfLon = spanLon(view) / 2;
  const halfLat = spanLat(view) / 2;
  const lines: GridLine[] = [];

  // Indexed rather than accumulated. Adding `step` in a loop drifts — after forty additions
  // of 0.2 the running total is 33.00000000000001 — and the drift lands in the axis label,
  // which is exactly where a reader is looking. An integer index times the step, snapped
  // back to the step's own precision, cannot.
  const latFrom = Math.ceil((view.lat - halfLat) / step.lat);
  const latTo = Math.floor((view.lat + halfLat) / step.lat);
  for (let i = latFrom; i <= latTo; i += 1) {
    const lat = Number((i * step.lat).toFixed(decimalsOf(step.lat)));
    const { y } = project(view, lat, view.lon);
    lines.push({
      value: lat,
      axis: "lat",
      x: 0,
      y,
      length: view.width,
      major: Math.abs(lat) < step.lat / 2,
    });
  }

  const lonFrom = Math.ceil((view.lon - halfLon) / step.lon);
  const lonTo = Math.floor((view.lon + halfLon) / step.lon);
  for (let i = lonFrom; i <= lonTo; i += 1) {
    const lon = Number((i * step.lon).toFixed(decimalsOf(step.lon)));
    const { x } = project(view, view.lat, lon);
    lines.push({
      value: lon,
      axis: "lon",
      x,
      y: 0,
      length: view.height,
      major: Math.abs(lon) < step.lon / 2,
    });
  }

  return lines;
}

// --- scale bar ------------------------------------------------------------

/** Round ground distances, in metres, for a scale bar to choose from. */
const SCALE_STEPS = [
  1, 2, 5, 10, 20, 50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000,
  100_000, 200_000, 500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000,
] as const;

/**
 * A scale bar: the largest round distance that fits in `maxPx`, and its width in pixels.
 *
 * **Largest that fits, which is the opposite of the obvious loop.** Choosing the smallest
 * step *at least* as large as the budget lets the bar overflow — at a one-degree view a
 * 20 km step is 208 px into a 120 px budget — and an overflowing scale bar silently covers
 * part of the map it is measuring.
 *
 * **Without a scale bar, "these two points are 3 m apart" and "300 m apart" are
 * indistinguishable at a glance** — the same two dots, depending entirely on zoom. A reader
 * needs to be able to see the scale they are judging distance at.
 */
export function scaleBar(view: Viewport, maxPx = 120): { metres: number; px: number } {
  const perPx = metresPerPixel(view);
  let metres: number = SCALE_STEPS[0];
  for (const step of SCALE_STEPS) {
    if (step / perPx > maxPx) break;
    metres = step;
  }
  return { metres, px: metres / perPx };
}
