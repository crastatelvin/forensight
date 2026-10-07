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
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.repository import AgentRun, AgentToolCall
from app.analysis.models import AnalysisResult
from app.analysis.repository import list_results_for_case
from app.auth.models import User
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
    #: Who recorded it. T8.3 asks for the operator on every finding and the report was
    #: printing a date instead, which identifies *when* and not *who* — the less
    #: useful half of the two. Resolved from the user record; a UUID on the face of a
    #: signed report tells a reader nothing they can act on.
    operator: str
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


@dataclass(frozen=True)
class ApprovalView:
    """A human decision, with the person who made it.

    T8.3 asks for HITL approvals in the provenance appendix and the first version
    printed only hashes — which bind the record to *what* was approved but not *who*.
    The approving identity exists only in the audit log: `agent_tool_calls` records
    that an action was approved, never by whom, because that is what the audit chain
    is for.
    """

    action_id: str
    tool: str
    args_hash: str
    operator: str
    approved_at: datetime


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
    approvals: list[ApprovalView] = field(default_factory=list)
    #: Actor identity resolved per custody entry. Carried separately rather than
    #: attached to the ORM row, so the query result stays a plain read.
    custody_operators: dict[str, str] = field(default_factory=dict)
    operators: dict[str, str] = field(default_factory=dict)
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

    # Who approved what. The approving identity exists only in the audit chain:
    # `agent_tool_calls` records *that* an action was approved, never by whom, and
    # that separation is the point of the chain.
    approvals = await _approvals(session, case.id)

    # Who did what. One query for every actor on the case rather than one per row:
    # an N+1 across a report's appendix is a slow report, and the set of actors is
    # small and bounded by the case's size.
    actor_ids = {
        *[str(f.created_by) for f in findings],
        *[str(c.actor_id) for c in custody],
        *[str(a["actor_id"]) for a in approvals if a["actor_id"]],
    }
    operators: dict[str, str] = {}
    if actor_ids:
        actors = (
            (
                await session.execute(
                    select(User).where(
                        User.__table__.c.id.in_([uuid.UUID(a) for a in actor_ids if _is_uuid(a)])
                    )
                )
            )
            .scalars()
            .all()
        )
        for actor in actors:
            operators[str(actor.id)] = f"{actor.email} ({actor.role})"

    def operator_of(actor_id: object) -> str:
        """A person's identity, or an honest statement that it is unknown.

        Deliberately not the raw UUID. If the user record is gone the report says so
        rather than printing an identifier the reader cannot look up.
        """
        return operators.get(str(actor_id), "unknown — the user record no longer exists")

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
            operator=operator_of(f.created_by),
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
        custody_operators={str(c.id): operator_of(c.actor_id) for c in custody},
        approvals=[
            ApprovalView(
                action_id=str(row["action_id"]),
                tool=str(row["tool"]),
                args_hash=str(row["args_hash"]),
                operator=operator_of(row["actor_id"]),
                approved_at=row["approved_at"],
            )
            for row in approvals
        ],
        operators=operators,
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


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def _approvals(session: AsyncSession, case_id: uuid.UUID) -> list[dict[str, Any]]:
    """Every human approval recorded for this case, from the audit chain.

    Read from `AuditLog` rather than from the tool-call rows because that is the only
    place the *actor* is recorded. `agent_tool_calls.approved_action_id` says an
    action was approved; the audit entry says by whom and when, and it is
    hash-chained, so it is also the entry a reader can rely on.

    Scoped to this case's runs. The payload carries the run id precisely so this is
    a filter rather than a guess — an approval from another case must never appear in
    this case's provenance.
    """
    from app.domain.models import AuditLog

    rows = (
        (
            await session.execute(
                select(AuditLog)
                .where(AuditLog.action.in_(["agent.action.approve"]))
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )

    # `select(AgentRun.id)` yields bare UUID scalars, not model instances — selecting
    # the column rather than the row is what keeps this from loading every checkpoint
    # on the case to read one id.
    run_ids = {
        str(run_id)
        for run_id in (
            (await session.execute(select(AgentRun.id).where(AgentRun.case_id == case_id)))
            .scalars()
            .all()
        )
    }

    out: list[dict[str, Any]] = []
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        run_id = payload.get("run_id")
        if run_id is None or str(run_id) not in run_ids:
            # Not this case's run. Excluded rather than trusted: the payload is
            # untyped JSON, so the only way to be sure is to check.
            continue
        out.append(
            {
                "action_id": row.target,
                "tool": payload.get("tool", ""),
                "args_hash": payload.get("args_hash", ""),
                "actor_id": str(row.actor_id) if row.actor_id else "",
                "approved_at": row.created_at,
            }
        )
    return out
