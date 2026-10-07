#!/usr/bin/env python
"""Park a state-changing agent action, so a test has something to answer.

Arranging state and exercising behaviour are separate jobs, and conflating them is
why this exists. Getting a parked action on a case needs a model that *decides* to
propose one, and a 3B model decides that inconsistently — so a browser test that
tried to produce one by asking would be flaky for reasons that have nothing to do
with the browser.

Parking it with the real graph and a scripted model is honest setup: the same code
path runs, and what is being tested downstream is untouched. The browser then
exercises exactly the thing that broke — whether the approve control is *there* after
a reload, and whether answering it applies the write.

This is not a way to make an agent misbehave for production. It is a fixture
generator with a narrow purpose, kept out of `app/` for that reason.

    python scripts/seed_parked_run.py --email user@example.org --title "HITL check"

Prints the case id, the run id and the action id, one per line, so a caller can
drive the API with them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from typing import Any

from app.agent.graph import AgentGraph
from app.agent.llm import ChatResponse
from app.agent.repository import EMBED_DIM, create_run
from app.agent.types import AgentState, RunStatus
from app.analysis.models import AnalysisJob, AnalysisResult
from app.auth.models import User
from app.config import Settings
from app.db import SessionLocal
from app.domain.models import Case, Evidence


class OneShotModel:
    """Emits a single `update_case_finding` call, then nothing.

    The smallest thing that produces a parked action. `update_case_finding` is
    state-changing, so the tool runner raises `NeedsApproval` and the graph parks —
    the same branch a real model takes, reached without depending on a real model's
    judgement.

    Synchronous, because `ChatModel` is: two methods and a name.
    """

    name = "seeded-one-shot"

    def __init__(self) -> None:
        self._served = False

    def complete(
        self,
        messages: list[Any],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.0,
    ) -> ChatResponse:
        del messages, tools, temperature
        if self._served:
            return ChatResponse(content="Proposing the finding for review.")
        self._served = True
        return ChatResponse(
            tool_calls=[
                {
                    "function": {
                        "name": "update_case_finding",
                        "arguments": json.dumps(
                            {
                                "title": "Window forced on the primary exhibit",
                                "body": (
                                    "Recorded by the seed script so a browser test has "
                                    "an action to answer."
                                ),
                            }
                        ),
                    }
                }
            ]
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """A deterministic stand-in, at the column's real width.

        Two things matter here. It must be deterministic, because a real embedding
        would make the seed depend on a provider being reachable — exactly the
        flakiness the script exists to remove. And it must be `EMBED_DIM` wide,
        because the memory recall that runs *before* the agent reaches its tool call
        searches a pgvector column of that size; an 8-element stub looks plausible
        and is rejected by the database part-way through a run that was otherwise
        working.
        """
        out: list[list[float]] = []
        for text in texts:
            seed = uuid.uuid5(uuid.NAMESPACE_OID, text).int
            out.append([((seed >> (i % 64 * 12)) & 0xFFF) / 4095.0 for i in range(EMBED_DIM)])
        return out


async def seed(email: str, title: str) -> dict[str, str]:
    async with SessionLocal() as session:
        user = await session.get(User, uuid.UUID(email) if _is_uuid(email) else None)
        if user is None:
            from app.auth.repository import get_user_by_email

            user = await get_user_by_email(session, email)
        if user is None:
            raise SystemExit(f"no user with email {email!r}")

        case = Case(
            number=f"HITL-{uuid.uuid4().hex[:8].upper()}",
            title=title,
            owner_id=user.id,
        )
        session.add(case)
        await session.flush()

        evidence = Evidence(
            case_id=case.id,
            filename="seed.jpg",
            # A fixed, obviously-fake digest. Nothing verifies this file's bytes; it
            # exists so the case has something to analyse.
            sha256="0" * 64,
            content_type="image/jpeg",
            size_bytes=1,
            storage_key="seed/hitl.jpg",
            uploaded_by=user.id,
        )
        session.add(evidence)
        await session.flush()

        # One idempotency key for both rows: the job's is unique, and the result
        # carries the same one by construction.
        key = f"hitl-{uuid.uuid4().hex[:8]}"

        # The job row is not optional: `analysis_results.job_id` is a foreign key, so
        # a result without one is rejected by the database rather than by this
        # script. Seeding a result in isolation was the first version and it failed
        # with a constraint violation, which says nothing about what was being set up.
        job = AnalysisJob(
            case_id=case.id,
            evidence_id=evidence.id,
            module="byte-entropy",
            model_version="1.0.0",
            idempotency_key=key,
            status="done",
            requested_by=user.id,
        )
        session.add(job)
        await session.flush()

        result = AnalysisResult(
            job_id=job.id,
            case_id=case.id,
            evidence_id=evidence.id,
            module="byte-entropy",
            idempotency_key=key,
            score=7.83,
            threshold=7.5,
            model_version="1.0.0",
            tier="B",
            disclaimer="Visual similarity / triage assist only.",
            payload={},
        )
        session.add(result)
        await session.flush()

        run = await create_run(
            session,
            case_id=case.id,
            thread_id=f"hitl-{uuid.uuid4()}",
            objective="Record a finding describing the entropy result on this evidence.",
            actor_id=user.id,
            model="seeded",
        )
        graph = AgentGraph(
            session=session,
            model=OneShotModel(),
            settings=Settings(secret_key="x" * 40, minio_endpoint=""),
            run=run,
        )
        outcome = await graph.start(
            AgentState(
                run_id=str(run.id),
                case_id=str(case.id),
                thread_id=run.thread_id,
                objective=run.objective,
            )
        )

        state = outcome.state
        if state.status is not RunStatus.AWAITING_APPROVAL or state.pending is None:
            raise SystemExit(
                "the seed did not park an action, so it cannot serve its purpose; "
                f"status={state.status.value} pending={state.pending is not None}"
            )

        await session.commit()
        return {
            "case_id": str(case.id),
            "run_id": str(run.id),
            "action_id": state.pending.action_id,
            "tool": state.pending.tool,
        }


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="the case owner's email")
    parser.add_argument("--title", default="Seeded HITL case")
    args = parser.parse_args()

    print(json.dumps(asyncio.run(seed(args.email, args.title)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
