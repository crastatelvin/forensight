"""Collecting everything a report needs, in one read pass.

Kept apart from `builder.py` because this is "what the case contains" and that is
a question about the database, not about typography. It also makes the report's
*inputs* testable without rendering a single page — which matters more than it
sounds, because the failure this whole feature exists to prevent is a report that
prints something the system does not actually know.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRun, AgentToolCall
from app.analysis.models import AnalysisResult
from app.analysis.repository import list_results_for_case
from app.domain.models import Case, ChainOfCustody, Evidence, Finding, FindingCitation

#: Tier C means "an accredited laboratory's result, attached to this case". It is
#: deliberately not producible by any analyzer here — see DESIGN_PLAN section 2,
#: which cuts DNA, toxicology, pollen, entomology, odor, explosives and arson
#: chemistry as AI analyzers because none of them can be done for real from a web
#: upload. The tier exists so the report can render such a result correctly: as an
#: attached document from someone else, never as this system's own opinion.
TIER_LAB = "C"


@dataclass(frozen=True)
class CitationView:
    """One finding's warrant, resolved to something a reader can check."""

    result_id: str
    evidence_id: str
    filename: str
    evidence_sha256: str
    module: str
    score: float
    threshold: float
    tier: str
    model_version: str
    disclaimer: str
    not_applicable: bool
    cited_at: datetime

    @property
    def is_lab_result(self) -> bool:
        return self.tier == TIER_LAB


@dataclass(frozen=True)
class FindingView:
    """A finding, plus its sources — or the fact that it has none."""

    finding_id: str
    title: str
    body: str
    status: str
    created_at: datetime
    citations: tuple[CitationView, ...]

    @property
    def is_sourced(self) -> bool:
        """Whether this finding can be presented as resting on analysis.

        A finding with no citations is an opinion someone typed. The report must say
        so on its face rather than laying it out among sourced findings in a way
        that implies otherwise.
        """
        return bool(self.citations)


@dataclass(frozen=True)
class ManifestRow:
    filename: str
    sha256: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
    #: Digests this report actually depends on, as opposed to every file on the
    #: case. Both are printed: the report's integrity claim is about the former.
    cited: bool


@dataclass
class ReportData:
    """Everything the builder draws on."""

    case: Case
    evidence: list[Evidence]
    findings: list[FindingView]
    results: list[AnalysisResult]
    custody: list[ChainOfCustody]
    runs: list[AgentRun]
    tool_calls: list[AgentToolCall] = field(default_factory=list)
    manifest: list[ManifestRow] = field(default_factory=list)
    manifest_digest: str = ""

    @property
    def lab_results(self) -> list[CitationView]:
        """Citations that are attached lab work rather than analysis."""
        seen: set[str] = set()
        out: list[CitationView] = []
        for finding in self.findings:
            for citation in finding.citations:
                if citation.is_lab_result and citation.result_id not in seen:
                    seen.add(citation.result_id)
                    out.append(citation)
        return out

    @property
    def unsourced_findings(self) -> list[FindingView]:
        return [f for f in self.findings if not f.is_sourced]

    @property
    def human_approved_calls(self) -> list[AgentToolCall]:
        """Tool calls that only ran because a person approved them.

        The appendix prints these with the arguments the human actually saw and
        hashes to bind them. A finding written by the agent is a change a person
        authorised; the report should show who authorised it and what they saw, not
        present the finding as though the system had acted on its own.
        """
        return [c for c in self.tool_calls if c.approved_action_id]

    @property
    def cited_evidence_ids(self) -> set[str]:
        return {c.evidence_id for f in self.findings for c in f.citations}


def canonical_manifest(rows: list[ManifestRow]) -> str:
    """SHA-256 over the manifest, computed from a canonical serialisation.

    Canonical because the point is for a reader to be able to recompute it: sorted
    keys, no whitespace, no timestamps-that-vary. Two labs generating the manifest
    from the same evidence get the same digest, and a reader who disagrees with one
    row can prove it by recomputing.

    Covers *every* row on the case, not only the cited ones. A digest over only the
    cited subset would let someone quietly add an uncited evidence file without the
    manifest digest changing.
    """
    payload = [
        {
            "content_type": r.content_type,
            "filename": r.filename,
            "sha256": r.sha256,
            "size_bytes": r.size_bytes,
            "uploaded_at": r.uploaded_at.isoformat(),
        }
        for r in sorted(rows, key=lambda r: (r.filename, r.sha256))
    ]
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


async def gather(session: AsyncSession, case: Case) -> ReportData:
    """Read a case into the shape the report needs.

    One pass, deliberately. A report assembled from several queries can describe a
    case that never existed: an evidence item added between the manifest query and
    the custody query would appear in one section and not the other. Every row here
    comes from the same transaction snapshot.
    """
    evidence = list(
        (
            await session.execute(
                select(Evidence).where(Evidence.case_id == case.id).order_by(Evidence.created_at)
            )
        ).scalars()
    )
    findings = list(
        (
            await session.execute(
                select(Finding).where(Finding.case_id == case.id).order_by(Finding.created_at)
            )
        ).scalars()
    )
    citations = list(
        (
            await session.execute(
                select(FindingCitation)
                .join(Finding, Finding.id == FindingCitation.finding_id)
                .where(Finding.case_id == case.id)
                .order_by(FindingCitation.cited_at)
            )
        ).scalars()
    )
    results = await list_results_for_case(session, case.id, limit=500)
    custody = list(
        (
            await session.execute(
                select(ChainOfCustody)
                .join(Evidence, Evidence.id == ChainOfCustody.evidence_id)
                .where(Evidence.case_id == case.id)
                .order_by(ChainOfCustody.created_at)
            )
        ).scalars()
    )
    runs = list(
        (
            await session.execute(
                select(AgentRun).where(AgentRun.case_id == case.id).order_by(AgentRun.created_at)
            )
        ).scalars()
    )

    tool_calls = list(
        (
            await session.execute(
                select(AgentToolCall)
                .where(AgentToolCall.case_id == case.id)
                .order_by(AgentToolCall.created_at)
            )
        ).scalars()
    )

    by_evidence = {str(e.id): e for e in evidence}
    by_result = {str(r.id): r for r in results}

    by_finding: dict[str, list[CitationView]] = {}
    for row in citations:
        evidence_row = by_evidence.get(str(row.evidence_id))
        result_row = by_result.get(str(row.result_id))
        if evidence_row is None or result_row is None:
            # A citation pointing at something this case no longer contains. Skipped
            # rather than rendered blank: the report's appendix would otherwise show
            # a citation with no evidence behind it, which is worse than omitting it.
            continue
        by_finding.setdefault(str(row.finding_id), []).append(
            CitationView(
                result_id=str(row.result_id),
                evidence_id=str(row.evidence_id),
                filename=evidence_row.filename,
                evidence_sha256=evidence_row.sha256,
                module=row.module,
                score=row.score,
                threshold=row.threshold,
                tier=row.tier,
                model_version=row.model_version,
                # The disclaimer is read from the live result, not the snapshot:
                # it is the module's own wording about its own output, and a
                # snapshot of prose would be a second place for it to drift.
                disclaimer=result_row.disclaimer,
                not_applicable=bool(row.not_applicable),
                cited_at=row.cited_at,
            )
        )

    finding_views = [
        FindingView(
            finding_id=str(f.id),
            title=f.title,
            body=f.body,
            status=f.status,
            created_at=f.created_at,
            citations=tuple(by_finding.get(str(f.id), [])),
        )
        for f in findings
    ]

    cited_ids = {c.evidence_id for f in finding_views for c in f.citations}
    manifest = [
        ManifestRow(
            filename=e.filename,
            sha256=e.sha256,
            content_type=e.content_type,
            size_bytes=e.size_bytes,
            uploaded_at=e.created_at,
            cited=str(e.id) in cited_ids,
        )
        for e in evidence
    ]

    return ReportData(
        case=case,
        evidence=evidence,
        findings=finding_views,
        results=results,
        custody=custody,
        runs=runs,
        tool_calls=tool_calls,
        manifest=manifest,
        manifest_digest=canonical_manifest(manifest),
    )


def summary_counts(data: ReportData) -> dict[str, int]:
    """Counts the cover page leads with. Derived, never stored."""
    return {
        "evidence": len(data.evidence),
        "findings": len(data.findings),
        "sourced_findings": sum(1 for f in data.findings if f.is_sourced),
        "unsourced_findings": len(data.unsourced_findings),
        "lab_results": len(data.lab_results),
        "analyses": len(data.results),
    }


def as_audit_payload(data: ReportData, *, signed: bool) -> dict[str, Any]:
    """The report's own fingerprint, for the audit log.

    Recorded so an auditor can find the report that was generated even if the file
    itself is not retained — which it is not, by design: keeping every generated
    report would make the database a place reports quietly accumulate.
    """
    return {
        "case_number": data.case.number,
        "manifest_sha256": data.manifest_digest,
        "evidence_sha256": sorted(r.sha256 for r in data.manifest),
        "counts": summary_counts(data),
        "signed": signed,
    }


def human_approvals(data: ReportData) -> int:
    """How many actions a person authorised. Reported on the cover."""
    return len(data.human_approved_calls)
