"""Case export helpers: CSV / JSON / ZIP manifest (T3.4).

Redaction (FI-3) is applied here rather than in the router, so every export path gets
it — adding a format later cannot accidentally produce an unredacted one. Each builder
takes an explicit `Redaction`; the default is inactive, so an existing caller is
unchanged, and each verifies its own output before returning.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from typing import Any

from app.domain.models import Case, Evidence
from app.domain.redaction import (
    Redaction,
    apply,
    collect,
    verify_absent,
    verify_zip,
)


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


def export_json(
    case: Case, evidence: list[Evidence], *, redaction: Redaction | None = None
) -> bytes:
    rule = redaction or Redaction()
    doc = {
        "case": case_to_dict(case),
        "evidence": [evidence_to_dict(e) for e in evidence],
    }
    # Collect before applying: after redaction the original values are gone, and a
    # verifier that only knows the placeholders cannot tell a working redaction from
    # one that never reached the field.
    secrets = collect(doc, rule)
    content = json.dumps(apply(doc, rule), indent=2).encode("utf-8")
    verify_absent(content, secrets)
    return content


def export_csv(evidence: list[Evidence], *, redaction: Redaction | None = None) -> bytes:
    rule = redaction or Redaction()
    rows: list[dict[str, Any]] = [
        {
            "id": str(e.id),
            "case_id": str(e.case_id),
            "filename": e.filename,
            "sha256": e.sha256,
            "content_type": e.content_type,
            "size_bytes": e.size_bytes,
            "created_at": e.created_at.isoformat(),
        }
        for e in evidence
    ]
    secrets = collect(rows, rule)
    redacted = [apply(row, rule) for row in rows]

    buf = io.StringIO()
    writer = csv.writer(buf)
    columns = ["id", "case_id", "filename", "sha256", "content_type", "size_bytes", "created_at"]
    writer.writerow(columns)
    for row in redacted:
        writer.writerow([row[column] for column in columns])
    content = buf.getvalue().encode("utf-8")
    verify_absent(content, secrets)
    return content


def export_zip(
    case: Case, evidence: list[Evidence], *, redaction: Redaction | None = None
) -> bytes:
    """ZIP with case.json + evidence.csv + a manifest listing content-hashes.

    Does NOT include the WORM evidence bytes (metadata-only export, T3.4).

    `case_id` appears in the manifest under a key the field allowlist does not cover,
    so it is redacted explicitly there. That is deliberate and narrow: a case id is the
    one identifier an export must keep, because a manifest without it cannot be matched
    back to anything. It is *not* a person's identity, so redacting it would destroy
    the export's usefulness without protecting anyone.
    """
    rule = redaction or Redaction()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("case.json", export_json(case, evidence, redaction=rule))
        zf.writestr("evidence.csv", export_csv(evidence, redaction=rule))
        manifest = {
            "case_id": str(case.id),
            "case_number": case.number,
            "evidence_count": len(evidence),
            "content_hashes": [
                {"id": str(e.id), "filename": e.filename, "sha256": e.sha256} for e in evidence
            ],
        }
        zf.writestr("manifest.json", json.dumps(apply(manifest, rule), indent=2))

    content = buf.getvalue()
    # Every member, not the container: ZIP compresses, so the plaintext is not
    # necessarily visible in the outer bytes at all.
    verify_zip(content, collect({"case": case_to_dict(case), "manifest": manifest}, rule))
    verify_absent(content, collect(manifest, rule))
    return content
