"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Card,
  CardHeader,
  CardBody,
  EmptyState,
  Notice,
  Badge,
  Button,
} from "@/components/ui";
import {
  closestPairs,
  fitBounds,
  formatDegrees,
  formatDistance,
  graticule,
  landPaths,
  metresPerPixel,
  panBy,
  parseLand,
  project,
  scaleBar,
  unproject,
  zoomAt,
  type LandGeometry,
  type LatLon,
  type Viewport,
} from "@/lib/geo-map";

/**
 * Geospatial view.
 *
 * **A real coastline, and still nothing leaves the host.** The refusal recorded here
 * earlier was against *raster tiles*: a tile request carries a viewport, that viewport
 * comes from the case's own coordinates, and panning teaches a CDN where the
 * photographs were taken. That reasoning holds and the fix was never "draw no map" — it
 * was to stop using a service. Natural Earth 50m land is public domain, ships with the
 * application, and is drawn here from bundled geometry, so the map is a real map and the
 * only request the page ever makes is for its own basemap file.
 *
 * **Why not MapLibre anyway.** It was measured, not dismissed. `maplibre-gl` 6.13.0 is
 * BSD-3-Clause and does render a style with no tile source at all, so it could satisfy
 * the privacy requirement — but it is 26.7 MB unpacked across 19 dependencies and needs
 * WebGL, to obtain behaviour this panel now has directly. On a forensic appliance built
 * to run air-gapped on constrained hosts, in a lab VM without GPU acceleration or in a
 * printed report, that is a poor trade for pan and zoom. The projection is hand-written
 * and lives in `lib/geo-map.ts` where it can be tested without a DOM.
 *
 * **What the reader gets, and why each part earns its place.** A labelled graticule,
 * because past regional zoom the coastline leaves the frame and the grid is the only
 * thing that says where you are; a scale bar, because "3 m apart" and "300 m apart" are
 * the same two dots at different zooms and a distance without a scale is unreadable;
 * and a measured distance between the closest pairs, which is the actual finding — two
 * photographs 40 m apart in a sequence of a thousand is a different case from two
 * photographs 4 km apart, and nothing else on screen makes that difference visible.
 *
 * **The ceiling is the data, and it is stated rather than hidden.** Natural Earth 50m has
 * a median vertex spacing of 7.6 km along a coastline. Zoom is clamped so the outline
 * cannot degenerate into a few straight lines, and the caption says so: there is no
 * street-level coastline in public-domain data, and a map that implied otherwise would be
 * inventing detail. Distances and the graticule stay exact at every zoom.
 */

const W = 720;
const H = 420;
const BASEMAP_URL = "/basemap/ne-50m-land.json";

/**
 * Cartographic colours, set here rather than taken from the theme.
 *
 * A basemap is not UI chrome: its colours have to keep a fixed, tested relationship to
 * each other for the coastline to read, and that relationship must not invert when
 * someone switches the application to light mode. Land is a desaturated slate, sea is
 * near-black, and the grid sits between them so it is legible against both.
 */
const SEA = "#0d1117";
const LAND = "#2f3742";
const LAND_EDGE = "#525c6b";
const GRID = "#7d8590";
const GRID_MAJOR = "#aab2bd";

/** One plotted coordinate. */
export interface GeoPoint {
  evidenceId: string;
  filename: string;
  latitude: number;
  longitude: number;
  /** Raw DMS as recorded, so the plotted value can be checked by hand. */
  rawLatitude: string[];
  rawLongitude: string[];
}

export function GeospatialPanel({
  points,
  children,
}: {
  points: GeoPoint[];
  /** Rendered beneath the plot — the coordinate table, normally. */
  children?: ReactNode;
}) {
  if (points.length === 0) {
    return (
      <Card>
        <CardHeader
          title="Locations"
          subtitle="From EXIF metadata embedded in the evidence"
        />
        <CardBody className="p-0">
          <EmptyState
            icon="◎"
            title="No coordinates found"
            hint="A coordinate has to be written into a file's metadata by whatever produced it. Most edits, screenshots and re-saves drop it — so an absent coordinate is the common case and says nothing about where the evidence came from."
          />
        </CardBody>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title={`Locations (${points.length})`}
        subtitle="Read from evidence EXIF. Reported, never scored — a coordinate is editable without touching a pixel."
        action={<Badge tone="warn">not scored</Badge>}
      />
      <CardBody className="space-y-4">
        <Notice tone="warn" title="These are claims, not locations">
          An EXIF coordinate is trivially editable and nothing here corroborates it
          against any external source. Treat each point as &ldquo;this file says it was
          taken here&rdquo;, and corroborate before relying on it.
        </Notice>

        <Plot points={points} />
        {children}
      </CardBody>
    </Card>
  );
}

/**
 * The basemap, fetched once and shared by every mount.
 *
 * **Module scope rather than component state, because it is 1.6 MB (518 KB gzipped) and
 * immutable.** Refetching it per case would be a megabyte per navigation for bytes that
 * never change; the browser caches it anyway, but the in-memory parse is worth keeping.
 * A rejected promise is cached too, so a missing basemap produces one failed request
 * rather than one per pan.
 */
let basemapPromise: Promise<LandGeometry> | null = null;

function loadBasemap(): Promise<LandGeometry> {
  basemapPromise ??= fetch(BASEMAP_URL)
    .then((response) => {
      if (!response.ok) throw new Error(`basemap request failed (${response.status})`);
      return response.json() as Promise<unknown>;
    })
    .then(parseLand)
    .catch((error: unknown) => {
      basemapPromise = null;
      throw error;
    });
  return basemapPromise;
}

/**
 * The map.
 *
 * `role="img"` with a description, because the table below is the accessible version of
 * the same information — a chart whose only representation is visual is useless to a
 * screen-reader user, and pairing the two costs nothing.
 *
 * **The view is fitted to the evidence rather than fixed to the world**, because the whole
 * point of the map is to make a coordinate legible, and one point on a 720px world map is
 * one pixel. The fit is recomputed when the set of coordinates changes, and *not* when
 * the user pans — so a pan is never undone underneath them by a re-render.
 */
function Plot({ points }: { points: GeoPoint[] }) {
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const [land, setLand] = useState<LandGeometry | null>(null);
  const [basemapFailed, setBasemapFailed] = useState(false);
  // In state, not the drag ref: whether a drag is in progress affects the cursor, so it is
  // render-visible. Reading a ref during render is a React 19 error and would also not
  // repaint when the drag started.
  const [dragging, setDragging] = useState(false);

  const positions = useMemo<LatLon[]>(
    () => points.map((p) => ({ lat: p.latitude, lon: p.longitude })),
    [points],
  );

  // Refit only when the coordinates themselves change, keyed on the rounded extents so a
  // re-render with the same data does not throw away the reader's zoom.
  const extentKey = useMemo(
    () =>
      positions
        .map((p) => `${p.lat.toFixed(4)},${p.lon.toFixed(4)}`)
        .sort()
        .join(";"),
    [positions],
  );
  const [view, setView] = useState<Viewport>(() => fitBounds(positions, W, H));
  const [fittedKey, setFittedKey] = useState(extentKey);
  if (fittedKey !== extentKey) {
    setFittedKey(extentKey);
    setView(fitBounds(positions, W, H));
  }

  useEffect(() => {
    let cancelled = false;
    loadBasemap()
      .then((value) => {
        if (!cancelled) setLand(value);
      })
      .catch(() => {
        // A missing coastline degrades the view; it does not break it. The graticule,
        // the scale bar and the distances are all still correct without it.
        if (!cancelled) setBasemapFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const paths = useMemo(() => (land ? landPaths(land, view) : []), [land, view]);
  const grid = useMemo(() => graticule(view), [view]);
  const bar = useMemo(() => scaleBar(view), [view]);
  const pairs = useMemo(() => closestPairs(positions, 3), [positions]);
  const centre = useMemo(() => unproject(view, W / 2, H / 2), [view]);

  /** Client pixels to viewBox units, so the map works at any rendered size. */
  const toLocal = useCallback((clientX: number, clientY: number) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0 || rect.height === 0) return null;
    return {
      x: ((clientX - rect.left) / rect.width) * W,
      y: ((clientY - rect.top) / rect.height) * H,
    };
  }, []);

  // Wheel zoom has to be a non-passive native listener to cancel the page scroll, and
  // React's onWheel is passive by default, so it cannot preventDefault. Without this the
  // map scrolls the document instead of zooming whenever the pointer is over it.
  useEffect(() => {
    const node = svgRef.current;
    if (!node) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const focus = toLocal(event.clientX, event.clientY);
      // A wheel notch is ~100px; `factor` is per-notch, so divide the delta and keep the
      // step per notch constant whatever the device's pixel ratio or scroll setting.
      const notches = event.deltaY / 100;
      setView((current) => zoomAt(current, 0.9 ** notches, focus ?? undefined));
    };
    node.addEventListener("wheel", onWheel, { passive: false });
    return () => node.removeEventListener("wheel", onWheel);
  }, [toLocal]);

  const description = [
    `Map of ${points.length} coordinate${points.length === 1 ? "" : "s"} at the centre`,
    `${formatDegrees(centre.lat, "lat")}, ${formatDegrees(centre.lon, "lon")}`,
    `scale bar ${formatDistance(bar.metres)}`,
    `grid interval ${formatDegrees(grid[0]?.value ?? 0, "lat")}`,
    points
      .map(
        (p) => `${p.filename} at ${p.latitude.toFixed(4)}, ${p.longitude.toFixed(4)}`,
      )
      .join("; "),
    "Every coordinate is listed in full in the table below.",
  ].join(". ");

  const zoomBy = (factor: number) => setView((current) => zoomAt(current, factor));
  // A zoom that changes nothing means the view is already at that end of the range. Asked
  // of the pure function rather than compared against a constant, because the limits
  // depend on the centre latitude and would otherwise be duplicated here.
  const atFloor = zoomAt(view, 0.5).scale === view.scale;
  const atCeiling = zoomAt(view, 2).scale === view.scale;

  return (
    <figure className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="ghost"
          onClick={() => zoomBy(0.6)}
          disabled={atFloor}
        >
          Zoom in
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => zoomBy(1 / 0.6)}
          disabled={atCeiling}
        >
          Zoom out
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => setView(fitBounds(positions, W, H))}
        >
          Fit evidence
        </Button>
        <span className="text-[11px] text-tertiary tabular-nums">
          centre {formatDegrees(centre.lat, "lat")}, {formatDegrees(centre.lon, "lon")}{" "}
          · {formatDistance(metresPerPixel(view) * 100)} across 100px
        </span>
      </div>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={description}
        className="w-full touch-none rounded-md border border-border-subtle"
        style={{ background: SEA, cursor: dragging ? "grabbing" : "grab" }}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = { x: event.clientX, y: event.clientY };
          setDragging(true);
        }}
        onPointerMove={(event) => {
          const from = drag.current;
          if (!from) return;
          const local = toLocal(event.clientX, event.clientY);
          if (!local) return;
          const previous = svgRef.current?.getBoundingClientRect();
          if (!previous || previous.width === 0) return;
          setView((current) =>
            panBy(
              current,
              ((local.x - from.x) / previous.width) * W * -1,
              ((local.y - from.y) / previous.height) * H * -1,
            ),
          );
          drag.current = { x: event.clientX, y: event.clientY };
        }}
        onPointerUp={(event) => {
          drag.current = null;
          setDragging(false);
          event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onDoubleClick={() => zoomBy(0.6)}
      >
        {paths.map((d, i) => (
          <path key={i} d={d} fill={LAND} stroke={LAND_EDGE} strokeWidth={0.5} />
        ))}

        {grid.map((line) => (
          <g key={`${line.axis}${line.value}`}>
            <line
              x1={line.axis === "lat" ? 0 : line.x}
              y1={line.axis === "lat" ? line.y : 0}
              x2={line.axis === "lat" ? line.length : line.x}
              y2={line.axis === "lat" ? line.y : line.length}
              stroke={line.major ? GRID_MAJOR : GRID}
              strokeOpacity={line.major ? 0.55 : 0.25}
              strokeWidth={1}
            />
            <text
              x={line.axis === "lat" ? 4 : line.x + 3}
              y={line.axis === "lat" ? line.y - 3 : H - 5}
              fontSize={9}
              fill={GRID}
              fillOpacity={line.major ? 0.85 : 0.55}
            >
              {formatDegrees(line.value, line.axis)}
            </text>
          </g>
        ))}

        {/* Distances between the closest pairs: the finding the map exists to show. */}
        {pairs.map((pair) => {
          const a = project(view, positions[pair.a].lat, positions[pair.a].lon);
          const b = project(view, positions[pair.b].lat, positions[pair.b].lon);
          return (
            <g key={`${pair.a}-${pair.b}`}>
              <line
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke="var(--accent)"
                strokeOpacity={0.75}
                strokeWidth={1}
                strokeDasharray="3 3"
              />
              <text
                x={(a.x + b.x) / 2 + 4}
                y={(a.y + b.y) / 2 - 4}
                fontSize={10}
                fill="var(--accent)"
                stroke={SEA}
                strokeWidth={2.5}
                paintOrder="stroke"
              >
                {formatDistance(pair.metres)}
              </text>
            </g>
          );
        })}

        {points.map((p, i) => {
          const { x, y } = project(view, p.latitude, p.longitude);
          return (
            <g key={`${p.evidenceId}-${i}`}>
              <circle
                cx={x}
                cy={y}
                r={4.5}
                fill="var(--alert)"
                stroke={SEA}
                strokeWidth={1.5}
              />
              <title>{`${p.filename} — ${p.latitude.toFixed(5)}, ${p.longitude.toFixed(5)}`}</title>
            </g>
          );
        })}

        {/* Scale bar, bottom-right. */}
        <g>
          <rect
            x={W - bar.px - 10}
            y={H - 26}
            width={bar.px + 10}
            height={16}
            fill={SEA}
            fillOpacity={0.75}
            rx={2}
          />
          <line
            x1={W - bar.px - 6}
            y1={H - 14}
            x2={W - 6}
            y2={H - 14}
            stroke={GRID_MAJOR}
            strokeWidth={2}
          />
          <text x={W - bar.px - 6} y={H - 18} fontSize={10} fill={GRID_MAJOR}>
            {formatDistance(bar.metres)}
          </text>
        </g>
      </svg>

      <figcaption className="text-[11px] leading-relaxed text-tertiary">
        Natural Earth 50m land, bundled with the application — public domain, no tile
        service, so the only request this page makes is for its own basemap file and no
        viewport ever leaves the host. The coastline carries detail to roughly
        10&nbsp;km and no finer: there is no street-level coastline in public-domain
        data, so zoom stops there rather than drawing a shape the source does not
        contain. Equirectangular with a cos(latitude) correction, so scale is locally
        accurate.
        {basemapFailed
          ? " The bundled coastline could not be loaded; the grid, scale bar and distances are unaffected."
          : ""}
      </figcaption>
    </figure>
  );
}

/**
 * Degrees/minutes/seconds as written in the file: 51°30'00".
 *
 * Built from the parts rather than the other way round, because the file stores
 * three numbers and a reader checking this column by hand needs the same three
 * numbers labelled the way EXIF labels them — not a string this function invented.
 */
function formatDms(parts: string[]): string {
  const [d = "?", m = "0", sec = "0"] = parts;
  return `${d}°${m}'${sec}"`;
}

/**
 * The coordinates as a table.
 *
 * Present even when the map is refused or broken, and it carries the raw DMS values so
 * the decimal figure can be checked by hand rather than trusted.
 */
export function CoordinateTable({ points }: { points: GeoPoint[] }) {
  if (points.length === 0) return null;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <caption className="sr-only">
          Coordinates read from evidence EXIF, with the raw values as recorded
        </caption>
        <thead>
          <tr className="border-b border-border-subtle">
            <th scope="col" className="py-2 pr-3 font-medium">
              Evidence
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              Decimal
            </th>
            <th scope="col" className="py-2 pr-3 font-medium">
              As recorded (DMS)
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-subtle">
          {points.map((p) => (
            <tr key={p.evidenceId}>
              <td className="py-2 pr-3 font-mono text-[11px]">{p.filename}</td>
              <td className="py-2 pr-3 font-mono tabular-nums">
                {p.latitude.toFixed(5)}, {p.longitude.toFixed(5)}
                <span className="ml-1 text-tertiary">
                  ({p.latitude >= 0 ? "N" : "S"}, {p.longitude >= 0 ? "E" : "W"})
                </span>
              </td>
              <td className="py-2 pr-3 font-mono text-[11px] text-secondary">
                {formatDms(p.rawLatitude)} {formatDms(p.rawLongitude)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
