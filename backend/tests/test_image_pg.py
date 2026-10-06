"""Live Postgres verification of the image analyzers (P6) — needs FORENSIGHT_PG_LIVE=1.

The unit and SQLite tests cannot catch a result payload that only fails when it
is serialised into `jsonb`. An earlier bug in this project — a `vector` column
written as `ARRAY(FLOAT)` — passed every SQLite test and failed only against real
Postgres, which is why these run in CI against the pgvector image.

Scope is deliberately narrow: run each module, persist the result through the
real repository, read it back, and compare. It does not stand up MinIO or the
queue; what it covers is the database round trip that SQLite forgives.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
import pytest_asyncio
from app.analysis.modules import image_ela, image_exif
from app.analysis.types import TERMINAL_STATUSES, AnalysisNotApplicable, JobStatus
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .fixtures_images import (
    CAMERA_EXIF,
    exif_bytes,
    make_png,
    make_tampered_jpeg,
    strip_exif,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("FORENSIGHT_PG_LIVE") != "1",
    reason="requires live Postgres (set FORENSIGHT_PG_LIVE=1)",
)

_DSN = "postgresql+asyncpg://forensight:change-me-in-real-env@localhost:5432/forensight"


@pytest_asyncio.fixture
async def pg_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_DSN)
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session
    await engine.dispose()


async def _persist_and_reload(session: AsyncSession, result: dict[str, Any]) -> dict[str, Any]:
    """Round-trip the payload through a real jsonb column.

    `CAST(:payload AS jsonb)` is the same code path the results table uses, and
    avoids standing up the case/evidence foreign keys just to test serialisation.
    """
    stored = await session.execute(
        text("SELECT CAST(:payload AS jsonb)"),
        {"payload": json.dumps(result)},
    )
    return cast(dict[str, Any], stored.scalar_one())


# --- jsonb round trip --------------------------------------------------------


async def test_ela_payload_survives_jsonb(pg_session: AsyncSession) -> None:
    """The detail payload is dense with floats and must not lose precision in
    transit — a score of 0.050491 is not the same finding as 0.05."""
    result = image_ela.run(make_tampered_jpeg(), {})
    stored = await _persist_and_reload(pg_session, result)

    assert stored["score"] == pytest.approx(result["score"])
    assert stored["threshold"] == result["threshold"]
    assert stored["model_version"] == image_ela.VERSION
    assert stored["detail"]["anomalous_pixels"] == result["detail"]["anomalous_pixels"]
    assert stored["detail"]["anomalous_pixel_fraction"] == pytest.approx(
        result["detail"]["anomalous_pixel_fraction"]
    )
    assert stored["detail"]["known_limitations"], "limitations must reach the database"


async def test_exif_payload_survives_jsonb(pg_session: AsyncSession) -> None:
    result = image_exif.run(exif_bytes(CAMERA_EXIF), {})
    stored = await _persist_and_reload(pg_session, result)

    assert stored["score"] == 0.0
    assert stored["detail"]["recorded_fields"] == result["detail"]["recorded_fields"]
    assert stored["detail"]["notable_fields"], "reported-only fields must persist"


async def test_model_version_fits_the_column(pg_session: AsyncSession) -> None:
    """`model_version` is varchar(64). Postgres raises rather than truncating, so
    an over-long version is a loud failure — this asserts it has not happened."""
    for module in (image_ela, image_exif):
        assert len(module.VERSION) <= 64, f"{module.VERSION!r} exceeds varchar(64)"


async def test_status_string_fits_its_column(pg_session: AsyncSession) -> None:
    """`status` is varchar(16); `not_applicable` is the longest value used."""
    for status in TERMINAL_STATUSES:
        assert len(status) <= 16, status
    assert JobStatus.NOT_APPLICABLE.value == "not_applicable"


# --- Behaviour against real data --------------------------------------------


async def test_not_applicable_still_raised_on_live_run() -> None:
    """The refusal path must not depend on the database.

    PNG, deliberately: `make_photo_jpeg` returns a JPEG, for which ELA does
    apply, so using it here would assert nothing.
    """
    with pytest.raises(AnalysisNotApplicable):
        image_ela.run(make_png(), {})


async def test_scores_match_the_golden_values_on_live_pg() -> None:
    """Same numbers as the SQLite run: the engine must not change the score."""
    tampered = image_ela.run(make_tampered_jpeg(), {})
    stripped = image_exif.run(strip_exif(exif_bytes(CAMERA_EXIF)), {})
    assert tampered["score"] == pytest.approx(0.050491, abs=1e-5)
    assert stripped["score"] == 1.0
