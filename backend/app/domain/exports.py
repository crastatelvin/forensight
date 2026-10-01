"""Case export helpers: CSV / JSON / ZIP manifest (T3.4)."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any

from app.domain.models import Case, Evidence


def case_to_dict(case: Case) -> dict[str, Any]:
    return {
        "id": str(case.id),
        "number": case.number,
        "title": case.title,
        "description": case.description,
        "status": case.status,
        "owner_id": str(case.owner_id),
        "assigned_to": str(case.assigned_to) if case.assigned_to else None,
        "priority": case.priority,
        "created_at": case.created_at.isoformat(),
    }


def evidence_to_dict(ev: Evidence) -> dict[str, Any]:
    return {
        "id": str(ev.id),
        "case_id": str(ev.case_id),
        "filename": ev.filename,
        "sha256": ev.sha256,
        "content_type": ev.content_type,
        "size_bytes": ev.size_bytes,
        "created_at": ev.created_at.isoformat(),
    }


def export_json(case: Case, evidence: list[Evidence]) -> bytes:
    doc = {
        "case": case_to_dict(case),
        "evidence": [evidence_to_dict(e) for e in evidence],
    }
    return json.dumps(doc, indent=2).encode("utf-8")


def export_csv(evidence: list[Evidence]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "filename", "sha256", "content_type", "size_bytes", "created_at"]
    )
    for e in evidence:
        writer.writerow(
            [str(e.id), e.filename, e.sha256, e.content_type, e.size_bytes,
             e.created_at.isoformat()]
        )
    return buf.getvalue().encode("utf-8")


def export_zip(case: Case, evidence: list[Evidence]) -> bytes:
    """ZIP with case.json + evidence.csv + a manifest listing content-hashes.

    Does NOT include the WORM evidence bytes (metadata-only export, T3.4).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("case.json", export_json(case, evidence))
        zf.writestr("evidence.csv", export_csv(evidence))
        manifest = {
            "case_id": str(case.id),
            "case_number": case.number,
            "evidence_count": len(evidence),
            "content_hashes": [
                {"id": str(e.id), "filename": e.filename, "sha256": e.sha256}
                for e in evidence
            ],
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue()
