"use client";

import { useRef, useState, useTransition, useCallback } from "react";
import { useRouter } from "next/navigation";
import type { EvidenceRead, AnalysisResultRead } from "@/lib/types";
import { formatBytes, formatDateTime, shortHash, imageKind } from "@/lib/format";
import { uploadEvidenceAction } from "@/app/cases/actions";
import { Button, Card, CardHeader, EmptyState, Notice, Badge } from "@/components/ui";
import { EvidenceViewer } from "./evidence-viewer";

/**
 * Evidence list, upload and viewer.
 *
 * Uploading goes through a server action with `FormData`, so the browser streams
 * the file to the Next server rather than holding it in JavaScript. That matters
 * for the file sizes this tool handles — a phone video dump is not something to
 * base64 through a state variable.
 */
export function EvidenceTab({
  caseId,
  evidence,
  results,
  canWrite,
}: {
  caseId: string;
  evidence: EvidenceRead[] | null;
  results: AnalysisResultRead[] | null;
  canWrite: boolean;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [uploaded, setUploaded] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [selected, setSelected] = useState<EvidenceRead | null>(null);
  const [reference, setReference] = useState<EvidenceRead | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const send = useCallback(
    (file: File) => {
      setError(null);
      setUploaded(null);
      const form = new FormData();
      form.append("file", file);
      startTransition(async () => {
        const result = await uploadEvidenceAction(caseId, form);
        if (!result.ok) {
          setError(result.error ?? "Upload failed.");
          return;
        }
        setUploaded(result.filename ?? file.name);
        router.refresh();
      });
    },
    [caseId, router],
  );

  // Drop handlers on the container, not on a styled child: `dragleave` fires
  // whenever the pointer crosses onto a child element, so per-element styling
  // flickers off the moment you move within the target.
  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      setDragging(false);
      const file = event.dataTransfer.files?.[0];
      if (file && canWrite) send(file);
    },
    [canWrite, send],
  );

  if (evidence === null) {
    return (
      <Notice tone="alert" title="Could not load evidence">
        The evidence list is unavailable. That is a problem with the server, not an
        empty case — do not conclude this case has no evidence.
      </Notice>
    );
  }

  return (
    <div className="space-y-5">
      {canWrite ? (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`rounded-lg border-2 border-dashed p-6 text-center transition-colors ${
            dragging
              ? "border-accent bg-accent-subtle"
              : "border-border-default bg-surface-1"
          }`}
        >
          <input
            ref={inputRef}
            id="evidence-file"
            type="file"
            className="sr-only"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) send(file);
              // Reset so re-selecting the same file fires `change` again.
              e.target.value = "";
            }}
          />
          <label htmlFor="evidence-file" className="block">
            <div className="text-sm font-medium">Drop a file here, or choose one</div>
            <div className="mx-auto mt-1 max-w-md text-xs leading-relaxed text-secondary">
              The file is hashed on arrival and written to the chain of custody before
              anything is read from it. The original bytes are never modified.
            </div>
          </label>
          <Button
            className="mt-3"
            variant="primary"
            disabled={pending}
            onClick={() => inputRef.current?.click()}
          >
            {pending ? "Uploading and hashing…" : "Choose file"}
          </Button>

          <div role="status" aria-live="polite" className="mt-3 empty:mt-0">
            {uploaded ? (
              <Notice tone="calm">
                <span className="font-medium">{uploaded}</span> was hashed and added to
                the chain of custody.
              </Notice>
            ) : null}
            {error ? <Notice tone="alert">{error}</Notice> : null}
          </div>
        </div>
      ) : null}

      {evidence.length === 0 ? (
        <Card>
          <EmptyState
            icon="◫"
            title="No evidence yet"
            hint={
              canWrite
                ? "Upload a file above. Each upload is hashed and recorded, so what you see here is what was received."
                : "Nothing has been uploaded to this case."
            }
          />
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader
              title={`Evidence (${evidence.length})`}
              subtitle="Select an item to view it. Ctrl/Cmd-click a second for side-by-side comparison."
            />
            <ul className="divide-y divide-border-subtle">
              {evidence.map((item) => {
                const itemResults = (results ?? []).filter(
                  (r) => r.evidence_id === item.id,
                );
                const kind = imageKind(item.content_type);
                const isReference = reference?.id === item.id;
                const isSelected = selected?.id === item.id;

                return (
                  <li
                    key={item.id}
                    className={`px-3 py-2.5 transition-colors ${
                      isSelected ? "bg-accent-subtle" : "hover:bg-surface-2"
                    }`}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        onClick={() =>
                          setSelected(isSelected ? null : isReference ? null : item)
                        }
                        onKeyDown={(e) => {
                          // Ctrl/Cmd-click sets the comparison reference. A plain
                          // click selects, so the common case stays one action.
                          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                            e.preventDefault();
                            setReference(item);
                          }
                        }}
                        className="min-w-0 flex-1 text-left"
                      >
                        <div className="flex items-center gap-2">
                          <span
                            aria-hidden="true"
                            className="shrink-0 font-mono text-[10px] uppercase text-tertiary"
                          >
                            {kind ?? "file"}
                          </span>
                          <span className="truncate text-sm font-medium">
                            {item.filename}
                          </span>
                          {isSelected ? <Badge tone="accent">viewing</Badge> : null}
                        </div>
                        <div className="mt-0.5 flex flex-wrap gap-x-3 text-[11px] text-tertiary">
                          <span>{formatBytes(item.size_bytes)}</span>
                          <span className="font-mono" title={item.sha256}>
                            {shortHash(item.sha256)}
                          </span>
                          <span>{formatDateTime(item.created_at)}</span>
                          {itemResults.length > 0 ? (
                            <span className="text-accent">
                              {itemResults.length} result
                              {itemResults.length === 1 ? "" : "s"}
                            </span>
                          ) : null}
                        </div>
                      </button>

                      <div className="flex shrink-0 gap-1.5">
                        {evidence.length > 1 ? (
                          <Button
                            size="sm"
                            variant={isReference ? "primary" : "ghost"}
                            onClick={() => setReference(isReference ? null : item)}
                            aria-pressed={isReference}
                          >
                            {isReference ? "reference ✓" : "set reference"}
                          </Button>
                        ) : null}
                        <a
                          href={`/api/evidence/${item.id}/download`}
                          className="inline-flex h-7 items-center rounded-md border border-border-default px-2.5 text-xs font-medium text-secondary hover:bg-surface-2"
                          download={item.filename}
                        >
                          Download
                        </a>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
          </Card>

          {selected ? (
            <EvidenceViewer
              // Keyed on the evidence id, so switching files remounts the viewer
              // with clean state. Resetting that state in an effect instead would
              // carry the previous file's ELA error into the new one — attributing
              // a refusal to the wrong artifact, which is a lie about evidence.
              key={selected.id}
              evidence={selected}
              reference={reference?.id === selected.id ? null : reference}
              results={(results ?? []).filter((r) => r.evidence_id === selected.id)}
              onClear={() => {
                setSelected(null);
                setReference(null);
              }}
            />
          ) : null}
        </>
      )}
    </div>
  );
}
