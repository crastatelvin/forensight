"""Evidence ingest + retrieval service (Phase 2).

Wires together validation, AV scan, content-addressed WORM storage, DB record,
chain-of-custody, and the audit log. Download is strictly by object id + case
authz — no user-supplied path ever touches storage (T2.2).
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.antivirus import Scanner
from app.domain.ingest import validate_upload
from app.domain.models import Evidence
from app.domain.repository import (
    append_audit,
    find_evidence_by_hash,
    get_evidence,
    record_custody,
)
from app.domain.storage import ObjectStore


class IngestService:
    def __init__(self, store: ObjectStore, scanner: Scanner) -> None:
        self._store = store
        self._scanner = scanner

    async def ingest(
        self,
        session: AsyncSession,
        *,
        case_id: uuid.UUID,
        actor_id: uuid.UUID,
        filename: str,
        data: bytes,
    ) -> Evidence:
        safe_name, mime, digest = validate_upload(filename=filename, data=data)
        # AV scan before anything is persisted (T2.4).
        await self._scanner.scan(data)

        # Dedupe by content hash within the case (T2.1).
        existing = await find_evidence_by_hash(session, case_id, digest)
        if existing is not None:
            await record_custody(
                session,
                evidence_id=existing.id,
                actor_id=actor_id,
                action="upload-dedupe",
                detail=f"duplicate of {existing.id}",
            )
            await append_audit(
                session,
                actor_id=actor_id,
                action="evidence.upload.dedupe",
                target=str(existing.id),
                payload={"sha256": digest, "case_id": str(case_id)},
            )
            return existing

        # Content-addressed WORM write (T2.1/T2.6).
        self._store.ensure_bucket()
        storage_key = self._store.put_evidence(
            key=digest, data=data, content_type=mime
        )

        evidence = Evidence(
            case_id=case_id,
            filename=safe_name,
            sha256=digest,
            content_type=mime,
            size_bytes=len(data),
            storage_key=storage_key,
            uploaded_by=actor_id,
        )
        session.add(evidence)
        await session.flush()

        await record_custody(
            session,
            evidence_id=evidence.id,
            actor_id=actor_id,
            action="upload",
            detail=f"{safe_name} ({mime}, {len(data)} bytes)",
        )
        await append_audit(
            session,
            actor_id=actor_id,
            action="evidence.upload",
            target=str(evidence.id),
            payload={"sha256": digest, "case_id": str(case_id), "mime": mime},
        )
        return evidence

    async def download(
        self,
        session: AsyncSession,
        *,
        evidence_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> tuple[Evidence, bytes]:
        """Fetch bytes by object id; re-verify hash on read (T2.1)."""
        evidence = await get_evidence(session, evidence_id)
        if evidence is None:
            raise FileNotFoundError("evidence not found")

        data = self._store.get_evidence(evidence.storage_key)

        from app.domain.ingest import sha256_hex

        if sha256_hex(data) != evidence.sha256:
            # Integrity failure -> audit + refuse.
            await append_audit(
                session,
                actor_id=actor_id,
                action="evidence.integrity_fail",
                target=str(evidence.id),
                payload={"expected": evidence.sha256},
            )
            await session.flush()
            raise ValueError("evidence integrity check failed")

        await record_custody(
            session,
            evidence_id=evidence.id,
            actor_id=actor_id,
            action="access",
            detail="download",
        )
        await append_audit(
            session,
            actor_id=actor_id,
            action="evidence.access",
            target=str(evidence.id),
            payload={"sha256": evidence.sha256},
        )
        return evidence, data
