"use client";

import { useState, useEffect, useId } from "react";
import type { EvidenceRead, AnalysisResultRead } from "@/lib/types";
import { formatBytes, formatDateTime, imageKind, shortHash } from "@/lib/format";
import {
  Button,
  Card,
  CardHeader,
  CardBody,
  Notice,
  Badge,
  DefList,
  cx,
} from "@/components/ui";

interface Unavailable {
  status: number;
  detail: string;
  kind: "missing_bytes" | "integrity" | "absent" | "unknown";
}

/**
 * Evidence viewer.
 *
 * Renders whatever the evidence actually is. The point is that the viewer never
 * guesses: an unsupported format shows a refusal with the real MIME type rather
 * than a broken `<img>`, and a file with no EXIF says so instead of an empty
 * metadata panel that looks like nothing was captured.
 *
 * The ELA overlay is opt-in and comes with a caption explaining what it is, because
 * a bright region is meaningless to someone who does not know that ELA reads
 * compression history — and worse, the overlay invites a conclusion the technique
 * cannot support.
 */
export function EvidenceViewer({
  evidence,
  reference,
  results,
  onClear,
}: {
  evidence: EvidenceRead;
  reference: EvidenceRead | null;
  results: AnalysisResultRead[];
  onClear: () => void;
}) {
  const [showEla, setShowEla] = useState(false);
  const [elaError, setElaError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(false);
  const [unavailable, setUnavailable] = useState<Unavailable | null>(null);
  const elaId = useId();

  const kind = imageKind(evidence.content_type);
  const elaResult = results.find((r) => r.module === "image-ela");

  // Probe availability once per evidence item, before rendering any media.
  //
  // Without this, a record whose stored bytes are missing renders as a broken
  // image icon — which reads as "this file is a bad image", not "this file is
  // gone from storage". In a forensic tool those are completely different findings,
  // and the second one is an integrity incident. A `Range: bytes=0-0` request is
  // the cheapest way to ask: one byte, and any error status comes back intact.
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/evidence/${evidence.id}/download?disposition=inline`, {
      headers: { range: "bytes=0-0" },
    })
      .then(async (response) => {
        if (response.ok) return;
        const body = (await response.json().catch(() => null)) as {
          detail?: string;
          kind?: Unavailable["kind"];
        } | null;
        if (cancelled) return;
        setUnavailable({
          status: response.status,
          detail:
            body?.detail ?? `The evidence could not be served (${response.status}).`,
          kind: body?.kind ?? "unknown",
        });
      })
      .catch(() => {
        if (!cancelled) {
          setUnavailable({
            status: 503,
            detail: "Could not reach the server to fetch this evidence.",
            kind: "unknown",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [evidence.id]);

  const src = `/api/evidence/${evidence.id}/download?disposition=inline`;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={evidence.filename}
          subtitle={`${evidence.content_type} · ${formatBytes(evidence.size_bytes)}`}
          action={
            <Button size="sm" variant="ghost" onClick={onClear}>
              Close viewer
            </Button>
          }
        />

        <CardBody className="space-y-4">
          {unavailable ? (
            <UnavailableNotice failure={unavailable} evidence={evidence} />
          ) : null}

          <div
            className={cx(
              "grid gap-3",
              reference ? "lg:grid-cols-2" : "grid-cols-1",
              unavailable && "hidden",
            )}
          >
            <MediaPane
              label={reference ? "A — primary" : undefined}
              evidence={evidence}
              kind={kind}
              src={src}
              zoom={zoom}
              onZoom={() => setZoom((v) => !v)}
            />
            {reference ? (
              <MediaPane
                label="B — reference"
                evidence={reference}
                kind={imageKind(reference.content_type)}
                src={`/api/evidence/${reference.id}/download?disposition=inline`}
              />
            ) : null}
          </div>

          {kind === "image" && !unavailable ? (
            <div className="rounded-md border border-border-subtle bg-surface-2/40 p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-xs font-medium">
                  <input
                    type="checkbox"
                    checked={showEla}
                    onChange={(e) => {
                      setShowEla(e.target.checked);
                      setElaError(null);
                    }}
                    className="h-3.5 w-3.5 accent-[var(--accent)]"
                  />
                  Overlay ELA error field
                </label>
                <a
                  href={src}
                  className="text-[11px] text-accent underline underline-offset-2"
                  target="_blank"
                  rel="noreferrer"
                >
                  Open original
                </a>
              </div>

              {showEla ? (
                <div className="mt-3 space-y-2">
                  {elaError ? (
                    <Notice tone="warn" title="ELA cannot be shown for this file">
                      {elaError}
                    </Notice>
                  ) : null}

                  {!elaError ? (
                    <div className="overflow-hidden rounded border border-border-default bg-black">
                      {/* `onError` on the img rather than a fetch: the browser
                          already has the error and this avoids a second request
                          just to learn something the img element knows. */}
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        id={elaId}
                        src={`/api/evidence/${evidence.id}/ela`}
                        alt="Error Level Analysis heat map: brighter regions carry a larger difference between the original and a re-encode of the same image."
                        className="max-h-96 w-full object-contain"
                        onError={() =>
                          setElaError(
                            "The overlay could not be produced. That usually means " +
                              "the file is not a JPEG — ELA is defined on JPEG " +
                              "re-compression, so there is no honest overlay to draw.",
                          )
                        }
                      />
                    </div>
                  ) : null}

                  <p className="text-[11px] leading-relaxed text-secondary">
                    Brighter regions differ more from a re-encode of this same image.
                    Regions edited <em>after</em> the original compression carry a
                    different residual, so edited areas tend to brighten — but so do
                    high-detail edges, so a bright region is a place to look, not a
                    conclusion. Run the analyzer for the score.
                  </p>

                  {elaResult ? (
                    <p className="text-[11px] text-tertiary">
                      Scored {elaResult.score.toFixed(4)} against a threshold of{" "}
                      {elaResult.threshold} · {elaResult.model_version}
                    </p>
                  ) : (
                    <Notice tone="info">
                      No ELA analysis has been run on this file. The overlay is the raw
                      error field, not a finding.
                    </Notice>
                  )}
                </div>
              ) : null}
            </div>
          ) : null}

          <DefList
            columns={3}
            items={[
              {
                term: "Filename",
                value: <span className="font-mono">{evidence.filename}</span>,
              },
              { term: "Type", value: evidence.content_type },
              { term: "Size", value: formatBytes(evidence.size_bytes) },
              { term: "Uploaded", value: formatDateTime(evidence.created_at) },
              {
                term: "SHA-256",
                value: (
                  <span className="font-mono text-[11px]" title={evidence.sha256}>
                    {evidence.sha256}
                  </span>
                ),
              },
              {
                term: "Evidence ID",
                value: (
                  <span className="font-mono text-[11px]">
                    {shortHash(evidence.id)}
                  </span>
                ),
              },
            ]}
          />

          {results.length > 0 ? (
            <div>
              <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-tertiary">
                Analysis on this file
              </h3>
              <ul className="space-y-1.5">
                {results.map((r) => (
                  <li
                    key={r.id}
                    className="flex flex-wrap items-center gap-2 rounded border border-border-subtle bg-surface-2/40 px-2.5 py-1.5"
                  >
                    <span className="font-mono text-xs font-medium">{r.module}</span>
                    <span className="text-xs text-secondary">{r.score.toFixed(4)}</span>
                    <Badge tone={r.tier === "A" ? "accent" : "info"}>
                      Tier {r.tier}
                    </Badge>
                    <span className="ml-auto font-mono text-[10px] text-tertiary">
                      {r.model_version}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * One media pane.
 *
 * `kind` decides what is rendered, and each branch states what it is showing.
 * An unsupported type falls back to a download prompt naming the real MIME type —
 * which is more useful than a failed embed, and honest about the fact that this
 * viewer does not handle it.
 */
/**
 * Why the evidence cannot be shown.
 *
 * 410 and 409 are integrity incidents, not display problems, and they are worded
 * as such: they say what is wrong, that it needs escalation, and — importantly —
 * that nothing here says anything about the content. The one thing this notice
 * must never imply is that the file was checked and found to be unremarkable.
 */
function UnavailableNotice({
  failure,
  evidence,
}: {
  failure: Unavailable;
  evidence: EvidenceRead;
}) {
  if (failure.kind === "missing_bytes") {
    return (
      <Notice tone="alert" title="This evidence's bytes are missing from storage">
        <p>{failure.detail}</p>
        <p className="mt-2">
          The record for <span className="font-mono">{evidence.filename}</span> exists,
          but the object it points at does not. That is an integrity incident: escalate
          it rather than re-uploading, because a replacement copy cannot be shown to be
          the original.
        </p>
        <p className="mt-2">Nothing has been analysed here. This is not a result.</p>
      </Notice>
    );
  }

  if (failure.kind === "integrity") {
    return (
      <Notice tone="alert" title="This evidence failed its integrity check">
        <p>{failure.detail}</p>
        <p className="mt-2">
          The stored bytes no longer hash to the digest recorded when this evidence was
          accepted. Do not use it, and do not re-upload over it — that would destroy the
          only evidence of the change.
        </p>
      </Notice>
    );
  }

  return (
    <Notice tone="warn" title="This evidence could not be loaded">
      <p>{failure.detail}</p>
      <p className="mt-2">
        It has not been viewed or analysed. An evidence item you cannot open is an
        absence of examination, not a clean result.
      </p>
    </Notice>
  );
}

function MediaPane({
  label,
  evidence,
  kind,
  src,
  zoom,
  onZoom,
}: {
  label?: string;
  evidence: EvidenceRead;
  kind: ReturnType<typeof imageKind>;
  src: string;
  zoom?: boolean;
  onZoom?: () => void;
}) {
  return (
    <figure className="space-y-2">
      {label ? (
        <figcaption className="text-[11px] font-medium uppercase tracking-wide text-tertiary">
          {label}
        </figcaption>
      ) : null}

      <div
        className={cx(
          "relative overflow-hidden rounded-md border border-border-default bg-surface-inset",
          kind === "image" ? "min-h-48" : "p-4",
        )}
      >
        {kind === "image" ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={src}
              alt={`Evidence image: ${evidence.filename}`}
              onClick={onZoom}
              className={cx(
                "w-full object-contain transition-all",
                zoom ? "cursor-zoom-out" : "cursor-zoom-in",
              )}
              // Click to inspect at native resolution. A forensic detail that is
              // one pixel of a scaled-down thumbnail is not reviewable.
              onLoad={(e) => {
                const img = e.currentTarget;
                if (zoom) {
                  img.style.maxWidth = "none";
                  img.style.width = `${img.naturalWidth}px`;
                } else {
                  img.style.maxWidth = "100%";
                  img.style.width = "100%";
                }
              }}
            />
            {onZoom ? (
              <span className="absolute right-2 bottom-2 rounded bg-surface-0/85 px-1.5 py-0.5 text-[10px] text-secondary">
                {zoom ? "click to fit" : "click for 1:1"}
              </span>
            ) : null}
          </>
        ) : kind === "video" ? (
          <video
            src={src}
            controls
            className="max-h-96 w-full"
            aria-label={`Evidence video: ${evidence.filename}`}
          />
        ) : kind === "audio" ? (
          <div className="space-y-2">
            <div className="text-xs text-secondary">{evidence.filename}</div>
            <audio src={src} controls className="w-full" aria-label="Evidence audio" />
          </div>
        ) : kind === "text" ? (
          <TextPreview src={src} />
        ) : (
          <div className="py-6 text-center">
            <div className="text-xs text-secondary">
              This viewer does not render{" "}
              <span className="font-mono">{evidence.content_type}</span>.
            </div>
            <a
              href={src}
              className="mt-2 inline-block text-xs text-accent underline underline-offset-2"
              download={evidence.filename}
            >
              Download to inspect
            </a>
          </div>
        )}
      </div>

      <figcaption className="truncate text-[11px] text-tertiary">
        {evidence.filename}
      </figcaption>
    </figure>
  );
}

/**
 * Text preview.
 *
 * Truncated with a stated limit, because "the first 64 KB is shown" is useful and
 * "here is your evidence" would not be. The limit is named in the UI so nobody
 * reads a truncated file as a complete one.
 */
/** How much of a text file the inline preview shows, in bytes. */
const PREVIEW_LIMIT = 64 * 1024;

function TextPreview({ src }: { src: string }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(src)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((body) => {
        if (!cancelled) setText(body.slice(0, PREVIEW_LIMIT));
      })
      .catch(() => {
        if (!cancelled) setError("could not be read");
      });
    return () => {
      cancelled = true;
    };
  }, [src]);

  if (error) {
    return <p className="text-xs text-alert">This file could not be read: {error}.</p>;
  }
  if (text === null) {
    return <p className="text-xs text-tertiary">Loading preview…</p>;
  }

  return (
    <div className="space-y-2">
      <pre className="max-h-80 overflow-auto rounded border border-border-subtle bg-surface-0 p-3 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">
        {text}
      </pre>
      {text.length >= PREVIEW_LIMIT ? (
        <p className="text-[11px] text-warn">
          Preview truncated at {PREVIEW_LIMIT / 1024} KB. Download the file to read it
          in full — the download is the byte-exact original.
        </p>
      ) : null}
    </div>
  );
}
