/**
 * Pulling coordinates out of analysis results.
 *
 * Kept separate from the panel so it can be reasoned about (and tested) without a
 * DOM: the panel is presentation, this is "what does the data actually say".
 */

import type { AnalysisResultRead, EvidenceRead } from "./types";
import type { GeoPoint } from "@/components/case/geospatial-panel";

/** The shape `image-exif` writes for a coordinate. */
interface GpsPayload {
  latitude: number;
  longitude: number;
  latitude_ref?: string;
  longitude_ref?: string;
  raw_latitude?: string[];
  raw_longitude?: string[];
}

/**
 * Every coordinate the case's results contain, with its evidence file.
 *
 * Narrowing rather than casting: the payload is untyped JSON from six different
 * analyzers, so a coordinate that is not two finite numbers in range is dropped
 * rather than plotted. A marker in the wrong place is worse than no marker — the
 * panel's whole claim is that a plotted point came from the file.
 *
 * `Number.isFinite` is the check that matters, and it is easy to forget: `NaN` and
 * `Infinity` both pass `typeof x === "number"`, and both would land the point at a
 * nonsense position while looking fine.
 */
export function collectCoordinates(
  results: AnalysisResultRead[] | null,
  evidence: EvidenceRead[] | null,
): GeoPoint[] {
  if (!results || !evidence) return [];

  const filename = new Map(evidence.map((e) => [e.id, e.filename]));
  const points: GeoPoint[] = [];

  for (const result of results) {
    if (result.module !== "image-exif") continue;

    const detail = result.payload.detail;
    if (!detail || typeof detail !== "object") continue;
    const gps = (detail as Record<string, unknown>).gps as GpsPayload | undefined;
    if (!gps) continue;

    const { latitude, longitude } = gps;
    if (typeof latitude !== "number" || typeof longitude !== "number") continue;
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
    if (latitude < -90 || latitude > 90) continue;
    if (longitude < -180 || longitude > 180) continue;

    points.push({
      evidenceId: result.evidence_id,
      filename:
        filename.get(result.evidence_id) ?? `${result.evidence_id.slice(0, 8)}…`,
      latitude,
      longitude,
      rawLatitude: gps.raw_latitude ?? [],
      rawLongitude: gps.raw_longitude ?? [],
    });
  }

  return points;
}
