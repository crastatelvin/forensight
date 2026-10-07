/**
 * Signed report: generate it, and check one you were handed.
 *
 * Two panels rather than one download button, because they answer different
 * questions. Generating asks "what does this case say right now?". Verification
 * asks "is the PDF in my hand the one that was signed?" — and that question has to
 * be answerable about a file this deployment never produced, which is why the
 * verifier takes the PDF *back* rather than looking something up.
 *
 * The verifier's output is deliberately not a single pass/fail. Integrity and
 * identity are separate: a self-signed report can be provably unaltered while its
 * signer's identity is unconfirmed, and collapsing those into one verdict would
 * either cry wolf on every report or hide a real tampering.
 */

"use client";

import { useRef, useState, useTransition } from "react";
import { Button, Card, CardHeader, CardBody, Notice, Badge } from "@/components/ui";

/** The verifier's answer, as the API returns it. */
interface VerifyResponse {
  intact: boolean;
  coverage: string;
  docmdp_ok: boolean;
  trusted: boolean;
  signer_subject: string | null;
  signing_time: string | null;
  problems: string[];
  verdict: string;
  filename: string | null;
  reported_sha256: string;
}

export function ReportPanel({
  caseId,
  disabled,
}: {
  caseId: string;
  disabled: boolean;
}) {
  const [busy, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<VerifyResponse | null>(null);
  const [checking, setChecking] = useState(false);
  const pdfInput = useRef<HTMLInputElement>(null);
  const certInput = useRef<HTMLInputElement>(null);

  function download() {
    setError(null);
    startTransition(async () => {
      // Routed through the Next server, like everything else: the access token is an
      // HttpOnly cookie, so a browser-side fetch would be unauthenticated.
      const response = await fetch(`/api/cases/${caseId}/report`);
      if (!response.ok) {
        let detail = "The report could not be generated.";
        try {
          const body = await response.json();
          if (typeof body.detail === "string") detail = body.detail;
        } catch {
          /* keep the generic message; the body was not the JSON we expected */
        }
        setError(detail);
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      // The header carries the filename; falling back keeps the download working if
      // a proxy strips it.
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = /filename="([^"]+)"/.exec(disposition);
      link.download = match?.[1] ?? "case-report.pdf";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });
  }

  async function verify() {
    const file = pdfInput.current?.files?.[0];
    if (!file) {
      setError("Choose a PDF to check first.");
      return;
    }
    setChecking(true);
    setError(null);
    setResult(null);
    try {
      const form = new FormData();
      form.append("pdf", file);
      const certificate = certInput.current?.files?.[0];
      if (certificate) form.append("certificate", certificate);

      const response = await fetch("/api/reports/verify", {
        method: "POST",
        body: form,
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(
          typeof payload.detail === "string"
            ? payload.detail
            : "The report could not be checked.",
        );
        return;
      }
      setResult(payload as VerifyResponse);
    } catch {
      setError("The check could not be completed.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Signed report"
          subtitle="A PDF covering the findings, the evidence digests they rest on, and who authorised each change. Signed with this deployment's key."
        />
        <CardBody className="space-y-3">
          <Button onClick={download} disabled={disabled || busy}>
            {busy ? "Generating…" : "Download signed report"}
          </Button>
          <p className="text-[11px] leading-relaxed text-tertiary">
            The report is generated on request and not stored. Nothing is retained
            server-side beyond an audit entry recording its manifest digest and its
            signature.
          </p>
          {error ? (
            <div role="alert">
              <Notice tone="alert">{error}</Notice>
            </div>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Check a report"
          subtitle="Upload a report and this deployment will tell you whether it is unaltered. Works for a report from anywhere, not only one this system produced."
        />
        <CardBody className="space-y-3">
          <div className="space-y-2">
            <label className="block text-xs font-medium" htmlFor="verify-pdf">
              Report PDF
            </label>
            <input
              ref={pdfInput}
              id="verify-pdf"
              type="file"
              accept="application/pdf,.pdf"
              className="block w-full text-xs"
            />
          </div>
          <div className="space-y-2">
            <label className="block text-xs font-medium" htmlFor="verify-cert">
              Signer&apos;s certificate (optional)
            </label>
            <input
              ref={certInput}
              id="verify-cert"
              type="file"
              accept=".pem,.crt,.cer,.p12,application/x-pem-file"
              className="block w-full text-xs"
            />
            <p className="text-[11px] leading-relaxed text-tertiary">
              Without it you learn the file is unaltered but not who signed it. Signing
              here is self-signed, so until you supply the certificate the signer is
              unconfirmed — that is the honest answer, not a failure.
            </p>
          </div>

          <Button onClick={verify} disabled={checking}>
            {checking ? "Checking…" : "Check signature"}
          </Button>

          {result ? <Verdict result={result} /> : null}
        </CardBody>
      </Card>
    </div>
  );
}

/**
 * The verdict.
 *
 * Every claim the API makes is shown, including the ones that are not reassuring.
 * A checker that displayed only "OK" would be indistinguishable from one that had
 * not looked, and a reader who trusts it would be trusting that.
 */
function Verdict({ result }: { result: VerifyResponse }) {
  const passed = result.intact && result.coverage === "ENTIRE_FILE";
  return (
    <div role="status" className="space-y-3 rounded-md border border-border-subtle p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={passed ? "calm" : "alert"}>
          {result.intact ? "unaltered" : "altered"}
        </Badge>
        <Badge tone={result.trusted ? "calm" : "warn"}>
          {result.trusted ? "signer trusted" : "signer unconfirmed"}
        </Badge>
        {result.coverage !== "ENTIRE_FILE" ? (
          <Badge tone="warn">partial coverage</Badge>
        ) : null}
      </div>

      <p className="text-xs leading-relaxed text-secondary">{result.verdict}</p>

      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[11px]">
        <dt className="text-tertiary">Signed by</dt>
        <dd className="font-mono text-secondary">
          {result.signer_subject ?? "unknown"}
        </dd>
        <dt className="text-tertiary">Coverage</dt>
        <dd className="font-mono text-secondary">{result.coverage}</dd>
        <dt className="text-tertiary">Signing time</dt>
        <dd className="font-mono text-secondary">
          {result.signing_time ?? "not stated"}
        </dd>
        <dt className="text-tertiary">File SHA-256</dt>
        <dd className="break-all font-mono text-tertiary">{result.reported_sha256}</dd>
      </dl>

      {result.problems.length > 0 ? (
        <div>
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-tertiary">
            What the check found
          </div>
          <ul className="space-y-1 text-[11px] leading-relaxed text-secondary">
            {result.problems.map((problem, i) => (
              <li key={i} className="flex gap-2">
                <span aria-hidden="true" className="text-warn">
                  &middot;
                </span>
                <span>{problem}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <Notice tone="info" title="What this check does not cover">
        A signature proves the bytes have not changed since signing. It does not prove
        the analysis was correct, nor that the evidence described is the evidence that
        was seized.
      </Notice>
    </div>
  );
}
