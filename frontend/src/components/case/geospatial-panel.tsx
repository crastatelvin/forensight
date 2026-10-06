import type { ReactNode } from "react";
import { Card, CardHeader, CardBody, EmptyState, Notice, Badge } from "@/components/ui";

/**
 * Geospatial view.
 *
 * **No basemap, and that is deliberate rather than a gap.** The obvious
 * implementation is MapLibre GL with raster tiles, and it is the wrong choice for
 * this system: a tile request carries the viewport, which here is derived from the
 * evidence. Pan or zoom far enough and the tile CDN learns where the photographs
 * were taken — before anyone has decided that is acceptable to disclose. In an
 * air-gapped or restricted deployment it also just renders a blank canvas.
 *
 * So this renders the coordinates themselves on an equirectangular projection with
 * a graticule, and says plainly that no external service was contacted. A point
 * plotted from a coordinate is the actual claim; the map underneath it is
 * decoration, and decoration is not worth a privacy leak.
 *
 * **When to add MapLibre:** if a deployment supplies its own tile source and accepts
 * the disclosure — an internal server, a licensed offline basemap, or an explicit
 * opt-in — the projection here is the layer it would replace. That is a
 * configuration and rendering change, not a data-model change, because the
 * coordinates are already in decimal degrees.
 */

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

/**
 * Equirectangular projection to SVG viewBox coordinates.
 *
 * Linear in both axes, which is what makes it honest: a degree of longitude is the
 * same distance everywhere on it. Web Mercator would look more familiar and
 * stretches area near the poles, so two photographs the same ground distance apart
 * appear further apart the further north they are. For comparing positions that is
 * the wrong distortion.
 */
function project(
  lat: number,
  lon: number,
  width: number,
  height: number,
): { x: number; y: number } {
  return {
    x: ((lon + 180) / 360) * width,
    y: ((90 - lat) / 180) * height,
  };
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
 * The plot itself.
 *
 * A `<svg>` with `role="img"` and a description, because the table below is the
 * accessible version of the same information — a chart whose only representation is
 * visual is useless to a screen-reader user, and pairing the two costs nothing.
 *
 * Multiple points at the same coordinate are offset slightly rather than stacked
 * invisibly, because two photographs from one spot is a normal and interesting
 * fact, not something to hide under a single dot.
 */
function Plot({ points }: { points: GeoPoint[] }) {
  const W = 640;
  const H = 320;
  const placed = new Map<string, { x: number; y: number; n: number }>();

  const marks = points.map((p) => {
    const { x, y } = project(p.latitude, p.longitude, W, H);
    // Bucket by rounded position so coincident points can be nudged apart.
    const key = `${Math.round(x)},${Math.round(y)}`;
    const existing = placed.get(key);
    if (existing) {
      existing.n += 1;
      const nudge = existing.n * 7;
      return { ...p, x: x + nudge, y: y - nudge, group: existing.n };
    }
    placed.set(key, { x, y, n: 0 });
    return { ...p, x, y, group: 0 };
  });

  const description = `${marks.length} coordinate${marks.length === 1 ? "" : "s"} plotted on an equirectangular grid: ${marks
    .map((m) => `${m.filename} at ${m.latitude.toFixed(4)}, ${m.longitude.toFixed(4)}`)
    .join("; ")}. Listed in full in the table below.`;

  return (
    <figure className="space-y-2">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={description}
        className="w-full rounded-md border border-border-subtle bg-surface-inset"
      >
        {/* Graticule every 30°. Labelled, because an unlabelled grid is decoration. */}
        {[-60, -30, 0, 30, 60].map((lat) => {
          const { y } = project(lat, 0, W, H);
          return (
            <g key={`lat${lat}`}>
              <line
                x1={0}
                y1={y}
                x2={W}
                y2={y}
                stroke="currentColor"
                strokeOpacity={lat === 0 ? 0.25 : 0.12}
                strokeWidth={lat === 0 ? 1.5 : 1}
                vectorEffect="non-scaling-stroke"
              />
              <text x={4} y={y - 3} fontSize={9} fill="currentColor" fillOpacity={0.5}>
                {lat === 0 ? "0° equator" : `${Math.abs(lat)}°${lat > 0 ? "N" : "S"}`}
              </text>
            </g>
          );
        })}
        {[-120, -60, 0, 60, 120].map((lon) => {
          const { x } = project(0, lon, W, H);
          return (
            <g key={`lon${lon}`}>
              <line
                x1={x}
                y1={0}
                x2={x}
                y2={H}
                stroke="currentColor"
                strokeOpacity={lon === 0 ? 0.25 : 0.12}
                strokeWidth={lon === 0 ? 1.5 : 1}
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={x + 3}
                y={H - 4}
                fontSize={9}
                fill="currentColor"
                fillOpacity={0.5}
              >
                {lon === 0 ? "0°" : `${Math.abs(lon)}°${lon > 0 ? "E" : "W"}`}
              </text>
            </g>
          );
        })}

        {marks.map((m, i) => (
          <g key={`${m.evidenceId}-${i}`}>
            <circle
              cx={m.x}
              cy={m.y}
              r={5}
              fill="var(--alert)"
              fillOpacity={0.75}
              stroke="var(--surface-0)"
              strokeWidth={1.5}
            />
            {m.group > 0 ? (
              <text
                x={m.x + 8}
                y={m.y + 3}
                fontSize={9}
                fill="currentColor"
                fillOpacity={0.75}
              >
                ×{m.group + 1}
              </text>
            ) : null}
          </g>
        ))}
      </svg>

      <figcaption className="text-[11px] leading-relaxed text-tertiary">
        Equirectangular projection on a 30° graticule, with no basemap. No external tile
        or map service was contacted — a tile request would carry the viewport, and the
        viewport here comes from the evidence.
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
  return `${d}\u00b0${m}'${sec}"`;
}

/**
 * The coordinates as a table.
 *
 * Not optional and not a fallback: this is the accessible rendering of the plot,
 * and it carries the raw DMS values so the decimal figure can be checked by hand
 * rather than trusted.
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
