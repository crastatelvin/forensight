/**
 * Formatting helpers.
 *
 * Kept together because the two easy mistakes — showing a raw byte count and
 * rendering an ISO timestamp — are the same mistake twice: leaking the storage
 * representation into the interface.
 */

/** Human byte size. Binary units, because that is what operating systems show. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

/**
 * A date, with the time when it matters.
 *
 * Evidence timestamps are the point of a forensic timeline, so seconds are shown —
 * an argument about which of two files arrived first cannot be settled to the
 * minute.
 */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
  }).format(date);
}

/** Coarse "how long ago", for list rows where the exact time is noise. */
export function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.round((Date.now() - then) / 1000);

  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return formatDate(iso);
}

/**
 * A SHA-256, shortened for display but never rewritten.
 *
 * Truncation is for the eye only. Anything that compares, copies or verifies a
 * digest needs the full value, which is why this is a display helper and there is
 * no "normalise hash" function anywhere in the app.
 */
export function shortHash(hash: string): string {
  return hash.length > 16 ? `${hash.slice(0, 12)}…${hash.slice(-6)}` : hash;
}

/** Whether a file is an image the browser can render, and in what. */
export function imageKind(
  contentType: string,
): "image" | "video" | "audio" | "text" | null {
  if (contentType.startsWith("image/")) return "image";
  if (contentType.startsWith("video/")) return "video";
  if (contentType.startsWith("audio/")) return "audio";
  if (
    contentType.startsWith("text/") ||
    contentType === "application/json" ||
    contentType === "application/xml"
  ) {
    return "text";
  }
  return null;
}
