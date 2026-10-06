"""ELA overlay rendering (P7.B4).

The in-app evidence viewer shows the Error Level Analysis heat map over the
original image. That needs the *spatial* error field, which the stored result does
not carry — `analyse_levels` deliberately reduces it to scalars for the score,
because a score must not depend on where the anomalies happen to be.

So this is a rendering endpoint, not an analysis one: it recomputes the error
field from the same function the analyzer uses and returns a PNG. Recomputing
rather than storing the map means the overlay always agrees with the algorithm
that produced the score beside it — a second stored artefact would drift.

It reuses the ingest service's download path rather than reading object storage
itself, so the bytes served here go through the same hash verification and custody
trail as a download. A viewer that could display altered bytes would undermine the
one property the rest of the system works to guarantee.
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.modules.image_ela import (
    ELA_SIGMAS,
    error_levels,
)
from app.analysis.modules.image_ela import (
    _decode as _decode_image,
)
from app.analysis.types import AnalysisNotApplicable
from app.auth.rbac import CurrentUser, get_current_user
from app.config import Settings, get_settings
from app.db import get_session
from app.domain.access import visible_case
from app.domain.antivirus import NoopScanner
from app.domain.repository import get_evidence
from app.domain.service import IngestService
from app.domain.storage import ObjectStore

router = APIRouter(tags=["analysis"], prefix="/cases/evidence")


def _ingest(settings: Settings) -> IngestService:
    """The ingest service, for reading bytes back out.

    Duplicated from the domain router rather than imported from it: that module
    is the router itself, and importing a router from a router creates a cycle
    for two lines. `NoopScanner` is right here — this path already has malware
    scanning applied at upload time, and re-scanning on every overlay render
    would add latency to a view, not safety.
    """
    return IngestService(ObjectStore(settings), NoopScanner())


#: Colour-ramp ceiling, as a multiple of the anomaly cutoff.
#:
#: A linear ramp to the observed maximum would render almost everything black: a
#: handful of pixels carry most of the range in a normal JPEG, so the maximum is
#: far above the level anyone is looking for. Clipping at a few sigma above the
#: cutoff shows the regions the analyzer actually flagged.
RAMP_SIGMAS = 3.0


def render_ela_png(data: bytes) -> bytes:
    """The ELA error field of `data` as a grayscale PNG.

    Separated from the route so it can be tested without a session, a case and a
    user — the pixel maths is the part worth testing, and burying it in a
    dependency-laden handler means it never is.
    """
    image = _decode_image(data)
    levels = error_levels(image)

    median = float(np.median(levels))
    mad = float(np.median(np.abs(levels - median))) or 1.0
    cutoff = median + ELA_SIGMAS * mad
    span = max(cutoff, 1e-6) * RAMP_SIGMAS

    # sqrt rather than linear: linear packs the entire interesting range into the
    # first couple of shades of grey, which is the range the viewer exists to show.
    shaped = np.sqrt(np.clip(levels / span, 0.0, 1.0))
    rgb = np.repeat((shaped * 255).astype(np.uint8)[:, :, None], 3, axis=2)

    from PIL import Image

    buffer = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@router.get("/{evidence_id}/ela.png")
async def ela_overlay(
    evidence_id: uuid.UUID,
    user: Annotated[CurrentUser, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    """The ELA error field of one evidence item, as a PNG.

    404 when the evidence does not exist *or* is not visible to the caller — the
    same answer either way, because existence is not something this endpoint will
    disclose to someone who cannot see the case. 422 when the evidence exists but
    the technique cannot apply, which is a different thing and must be
    distinguishable: "this is not a JPEG" and "this does not exist" lead to
    completely different next steps for whoever is looking.
    """
    evidence = await get_evidence(session, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    case = await visible_case(session, evidence.case_id, user)
    if case is None:
        raise HTTPException(status_code=404, detail="Evidence not found")

    try:
        _, data = await _ingest(settings).download(
            session, evidence_id=evidence.id, actor_id=uuid.UUID(user.id)
        )
    except FileNotFoundError as exc:
        # 410 for the same reason as the download route: the record exists and the
        # bytes do not. 404 would read as "no such evidence" and nobody would go
        # looking for why the stored object vanished.
        logging.getLogger("forensight").error(
            "ELA overlay for %s cannot be produced: %s", evidence_id, exc
        )
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ValueError as exc:
        # An integrity failure: the stored bytes no longer hash to the recorded
        # digest. The custody row for that was flushed but not committed, and this
        # event is the one that most needs to be durable.
        await session.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()

    try:
        png = render_ela_png(data)
    except AnalysisNotApplicable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Short cache only: the overlay is derived from immutable evidence, but
            # by a versioned algorithm that is not named in the URL, so a long
            # cache would outlive an algorithm change.
            "Cache-Control": "private, max-age=300",
        },
    )
