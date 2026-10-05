"""Provenance enforcement (P5.B8) — the honesty clause as code.

The rule is simple and absolute: the agent may not assert anything it cannot
cite to a real analyzer result. `enforce` resolves every citation against the
`analysis_results` table before letting a response through, because a
self-reported citation is exactly the thing a compromised or confused model
would invent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.types import Citation, Claim, ProvenanceError
from app.analysis.models import AnalysisResult


@dataclass
class ProvenanceVerdict:
    ok: bool
    problems: list[str]
    citations: list[Citation]


class ProvenanceGuard:
    """Validates claims against the database, not against the model's word."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(self, citation: Citation) -> AnalysisResult | None:
        """Fetch the cited result, scoped to the run's case.

        Scoping matters: a valid result id from *another* case must not be usable
        to source a claim here, or case scope leaks through the agent.
        """
        try:
            result_id = uuid.UUID(citation.result_id)
        except ValueError:
            return None
        result = await self._session.get(AnalysisResult, result_id)
        if result is None:
            return None
        return result

    async def check_citation(self, citation: Citation, *, case_id: uuid.UUID) -> str | None:
        """Return a problem string, or None if the citation holds up."""
        try:
            citation.validate()
        except ProvenanceError as exc:
            return str(exc)

        result = await self.resolve(citation)
        if result is None:
            return (
                f"cited result {citation.result_id} does not exist; provenance "
                f"cannot be invented"
            )
        if result.case_id != case_id:
            return (
                f"cited result {citation.result_id} belongs to another case; "
                f"cross-case citations are not admissible"
            )
        # The denormalised fields must match the stored row. If they do not, the
        # caller is paraphrasing a result rather than citing it.
        if result.evidence_id != uuid.UUID(citation.evidence_id):
            return f"citation evidence {citation.evidence_id} does not match result"
        if result.module != citation.analyzer:
            return (
                f"citation names analyzer {citation.analyzer!r} but result "
                f"{result.id} was produced by {result.module!r}"
            )
        if result.model_version != citation.model_version:
            return (
                f"citation claims model_version {citation.model_version!r} but "
                f"result {result.id} used {result.model_version!r}"
            )
        if abs(result.score - citation.score) > 1e-9:
            return f"citation score {citation.score} does not match stored score " f"{result.score}"
        if abs(result.threshold - citation.threshold) > 1e-9:
            return f"citation threshold {citation.threshold} does not match stored"
        if result.tier != citation.tier:
            return f"citation tier {citation.tier!r} does not match stored {result.tier!r}"
        return None

    async def enforce(self, claims: list[Claim], *, case_id: uuid.UUID) -> ProvenanceVerdict:
        """Validate a whole response. Raises on any unsourced claim.

        Raises rather than returning a verdict, because a caller that ignores a
        failed verdict would emit exactly the unsourced text this guards against.
        Use `check` for a non-raising inspection.
        """
        verdict = await self.check(claims, case_id=case_id)
        if not verdict.ok:
            raise ProvenanceError("; ".join(verdict.problems))
        return verdict

    async def check(self, claims: list[Claim], *, case_id: uuid.UUID) -> ProvenanceVerdict:
        problems: list[str] = []
        for claim in claims:
            if not claim.text.strip():
                problems.append("a claim has empty text")
                continue
            if not claim.citations:
                problems.append(f"unsourced claim: {claim.text[:80]!r} cites no evidence")
                continue
            for citation in claim.citations:
                problem = await self.check_citation(citation, case_id=case_id)
                if problem:
                    problems.append(problem)
        return ProvenanceVerdict(
            ok=not problems, problems=problems, citations=list(self._all(claims))
        )

    @staticmethod
    def _all(claims: list[Claim]) -> list[Citation]:
        seen: dict[str, Citation] = {}
        for claim in claims:
            for citation in claim.citations:
                seen.setdefault(citation.result_id, citation)
        return list(seen.values())

    async def render_report(self, claims: list[Claim], *, case_id: uuid.UUID) -> str:
        """Build a report that cannot be misread.

        Every claim is followed by its citations, and the tier disclaimer is
        repeated per citation rather than stated once at the top — a reader who
        sees only one line still sees the limitation.
        """
        verdict = await self.enforce(claims, case_id=case_id)
        if not verdict.citations:
            return "No sourced findings to report."

        by_id = {c.result_id: c for c in verdict.citations}
        lines = ["# Investigation report", ""]
        for claim in claims:
            lines.append(claim.text)
            for citation in claim.citations:
                c = by_id[citation.result_id]
                marker = "above threshold" if c.score >= c.threshold else "below threshold"
                lines.append(
                    f"  - [{c.tier}] {c.analyzer} v{c.model_version}: "
                    f"score {c.score:.3f} (threshold {c.threshold:.3f}, {marker}); "
                    f"evidence {c.evidence_id}; result {c.result_id}"
                )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


async def citations_from_result(result: AnalysisResult) -> Citation:
    """Build a citation from a stored result row. The only sanctioned way to
    mint a citation, so callers cannot hand-build a subtly wrong one."""
    return Citation(
        result_id=str(result.id),
        evidence_id=str(result.evidence_id),
        analyzer=result.module,
        score=result.score,
        threshold=result.threshold,
        model_version=result.model_version,
        tier=result.tier,
    )


async def result_ids_for_case(session: AsyncSession, case_id: uuid.UUID) -> list[str]:
    """Every citable result in a case. Used to build the agent's citation index."""
    result = await session.execute(
        select(AnalysisResult.id).where(AnalysisResult.case_id == case_id)
    )
    return [str(r) for r in result.scalars().all()]
