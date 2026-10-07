"""Report endpoints: generate a signed PDF, and verify one (T8.1, T8.3).

Two endpoints, and the split is deliberate. Verification takes the PDF **back** from
the reader rather than looking up something this server already holds, because the
question an examiner asks is "is the document in my hand the one that was signed?" —
not "does your database still contain a file you once produced". Storing every
generated report would also quietly make the database a place reports accumulate.

Reports are generated, never stored. Nothing here writes the PDF anywhere: it is
built in memory, signed, and returned. The audit log records the manifest digest and
the signed PDF's own hash, so a report can be identified and its integrity re-checked
later without the file itself being retained.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rbac import CurrentUser, require_roles
from app.auth.roles import MUTATING_ROLES
from app.config import Settings
from app.config import get_settings as get_settings_dep
from app.db import get_session
from app.domain.access import scoped_case
from app.reports.builder import SignerInfo, build_pdf
from app.reports.repository import as_audit_payload, gather
from app.reports.signing import (
    SigningUnavailable,
    certificate_fingerprint,
    load_signer,
    sign_pdf,
    signer_subject,
    verify_pdf,
)

router = APIRouter(prefix="/reports", tags=["reports"])

#: A report that exceeds this is refused rather than truncated. It exists only to
#: stop someone uploading a gigabyte to the verifier and taking the worker with it;
#: reports of real cases are tens of kilobytes.
MAX_REPORT_BYTES = 32 * 1024 * 1024


def _require_signer(settings: Settings) -> Any:
    """The configured signer, or a refusal that says what to do about it.

    The message names the script rather than saying "misconfigured", because the
    person hitting it is an operator, not a developer.
    """
    try:
        return load_signer(settings.report_signing_p12, settings.report_signing_passphrase)
    except SigningUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/cases/{case_id}.pdf")
async def generate_report(
    case_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> Response:
    """A signed PDF report for a case.

    Refuses outright when no signing key is configured. An unsigned report would be a
    normal-looking PDF anyone could edit, and the only place that difference becomes
    visible is in a verification step somebody may never reach.
    """
    # Visibility is decided by the case router's own rule, imported rather than
    # reimplemented. A report is a view of the case, so it inherits the access rule
    # exactly; two copies of an authorisation rule eventually disagree, and the one
    # that disagrees in the permissive direction leaks a case.
    case = await scoped_case(session, case_id, user)
    signer = _require_signer(settings)

    data = await gather(session, case)
    info = SignerInfo(subject=signer_subject(signer), fingerprint=certificate_fingerprint(signer))

    # ReportLab is synchronous CPU work and this document runs to dozens of pages.
    # On the event loop it would stall every other request for the length of a
    # render, so it goes to a worker thread.
    pdf = await anyio.to_thread.run_sync(lambda: build_pdf(data, info))
    signed = await sign_pdf(pdf, signer)

    subject = info.subject
    fingerprint = info.fingerprint
    pdf_sha256 = hashlib.sha256(signed).hexdigest()
    await append_report_audit(
        session,
        user=user,
        case_id=case.id,
        payload={
            **as_audit_payload(data, signed=True),
            "signature_subject": subject,
            "signature_fingerprint": fingerprint,
            "pdf_sha256": pdf_sha256,
            "pdf_bytes": len(signed),
        },
    )

    return Response(
        content=signed,
        media_type="application/pdf",
        headers={
            # `case.number` is validated free of quotes and newlines on write, so
            # interpolating it into Content-Disposition is safe — see CaseCreate.
            "Content-Disposition": f'attachment; filename="{case.number}-report.pdf"',
            # The report's own hash, so a transport or storage problem downstream is
            # detectable without trusting the PDF's contents.
            "X-Report-SHA256": pdf_sha256,
            # The evidence-set digest this report describes. Distinct from the above:
            # this one is about the evidence, not the document.
            "X-Manifest-SHA256": data.manifest_digest,
        },
    )


@router.post("/verify")
async def verify_report(
    _user: Annotated[CurrentUser, Depends(require_roles(*MUTATING_ROLES))],
    pdf: Annotated[UploadFile, File(description="The report to check")],
    certificate: Annotated[
        UploadFile | None,
        File(description="The signer's certificate, as a trust root"),
    ] = None,
) -> dict[str, Any]:
    """Check a report's signature and report both verdicts separately.

    Multipart because that is what the reader actually has: a PDF, and usually the
    signer's certificate as a second file. Supplying the certificate makes a
    self-signed report verify as trusted; omitting it returns `trusted: false`
    alongside a correct `intact`, because those are two different questions and the
    caller is better served by both answers than by one refusal.

    Stateless by design — verification consults nothing this server stores, so it
    works for a report this deployment never generated.
    """
    data = await pdf.read()
    if not data:
        raise HTTPException(status_code=400, detail="no PDF supplied")
    if len(data) > MAX_REPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"report is {len(data)} bytes; the limit is {MAX_REPORT_BYTES}",
        )

    trust_roots: list[bytes] | None = None
    if certificate is not None:
        raw = await certificate.read()
        if raw:
            trust_roots = [raw]

    result = await verify_pdf(data, trust_roots)
    return {
        **result.as_dict(),
        "filename": pdf.filename,
        "reported_bytes": len(data),
        "reported_sha256": hashlib.sha256(data).hexdigest(),
    }


async def append_report_audit(
    session: AsyncSession,
    *,
    user: CurrentUser,
    case_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    """Record that a report was produced.

    Generating a report is a *read* of the case, so no chain-of-custody entry is
    written — a custody entry claims the evidence moved, and it did not. The audit
    entry is still written: producing a signed document about a case is an act worth
    having a timestamp for even though it changes nothing.
    """
    from app.domain.repository import append_audit

    await append_audit(
        session,
        actor_id=uuid.UUID(user.id),
        action="case.report.generate",
        target=str(case_id),
        payload=payload,
    )
    await session.commit()


__all__ = ["append_report_audit", "router"]
