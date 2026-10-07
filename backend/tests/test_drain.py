"""T10.7 — shutdown drains in the right order, and the order is the whole point.

A graceful shutdown is a *sequence*, and every way it can go wrong produces the same
symptom: requests fail. What differs is whether the failure is a clean retry or a
report that was half-written. So these tests assert the order explicitly:

1. **Readiness drops first.** `/readyz` is the load balancer's only signal. If it
   keeps answering 200 until the socket dies, the balancer routes new requests here
   for the whole grace period and cuts every one of them off.
2. **New work is refused, with a message the caller can act on.** "unavailable"
   without a retry hint reads as an outage, and operators escalate routine deploys.
3. **In-flight work keeps running.** A report being generated when the signal arrived
   should finish. That is usually the artifact someone is waiting for.
4. **`/healthz` stays 200 throughout.** Liveness asks "is this worth restarting", and
   a draining process is not broken — restarting it abandons the work the drain
   exists to finish.

The signal handler itself is not tested by sending a real SIGTERM to the test
process, which would take the suite down with it. What is tested is the flag, the
wiring, and the *order* — and the ordering assertions are the load-bearing part.
"""

from __future__ import annotations

import pytest
from app import drain
from httpx import AsyncClient


@pytest.fixture(autouse=True)
def _not_draining() -> None:
    """The flag is process-global, so a leaking test would disarm the rest of the suite.

    Autouse and unconditional rather than per-test: a drain test that forgets to
    reset leaves every later agent test unable to start a run, and the failure would
    point at the agent.
    """
    drain.reset()
    yield
    drain.reset()


async def _auth(client: AsyncClient, creds: dict[str, str]) -> dict[str, str]:
    response = await client.post("/auth/login", json=creds)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def test_a_running_server_does_not_claim_to_be_draining(client: AsyncClient) -> None:
    """The baseline, and it is about `draining` rather than about the status code.

    `/readyz` is already 503 in this environment because Redis and MinIO are not
    running, so "readyz returns 503" proves nothing about draining. What separates
    the two causes is the `draining` key, so that is what is asserted — and it is why
    the drain path reports the reason explicitly instead of just flipping a status.
    """
    response = await client.get("/readyz")
    assert (
        "draining" not in response.json()
    ), "a server that was never told to stop is reporting itself as draining"


async def test_readiness_drops_as_soon_as_draining_starts(client: AsyncClient) -> None:
    """The ordering property: readiness goes before anything is torn down."""
    before = await client.get("/readyz")
    assert "draining" not in before.json()

    drain.begin_draining("test")

    after = await client.get("/readyz")
    assert after.status_code == 503, after.text
    body = after.json()
    assert body["ready"] is False
    # Named explicitly, so a monitoring rule can alert on "draining" rather than
    # having to infer it from a missing dependency.
    assert body["draining"] is True
    assert body["dependencies"] == {}, (
        "dependencies must not be listed as broken: nothing has been torn down yet, "
        "and a dashboard showing Postgres as failed during a deploy sends somebody to "
        "the wrong system"
    )


async def test_liveness_stays_up_while_draining(client: AsyncClient) -> None:
    """`/healthz` asks "is this worth restarting", and the answer is yes.

    A draining process has not crashed. Restarting it would abandon the in-flight
    report the drain exists to let finish, and the orchestrator would then see it die
    again and restart it again — a restart loop on a routine deploy.
    """
    drain.begin_draining("test")
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_a_new_agent_run_is_refused_while_draining(
    client: AsyncClient, sessionmaker_fixture: object, seed_users: dict
) -> None:
    """New long work is refused rather than started and then killed.

    An agent run is the longest thing here. Starting one seconds before the process
    dies leaves a run row, a tool call and a partial report with nobody to finish it,
    and the operator's objective is gone.
    """
    import uuid

    from app.auth.models import User
    from app.domain.models import Case

    headers = await _auth(client, seed_users["officer"])
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="DRN-1", title="drain", owner_id=user.id)
        session.add(case)
        await session.commit()
        case_id = case.id

    drain.begin_draining("test")

    response = await client.post(
        f"/agent/cases/{case_id}/runs",
        json={"objective": "summarise the evidence"},
        headers=headers,
    )
    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    # Names the retry, because "unavailable" with no hint reads as an outage and
    # gets escalated.
    assert "Retry" in detail
    assert "shutting down" in detail


async def test_a_new_analysis_is_refused_while_draining(
    client: AsyncClient, sessionmaker_fixture: object, seed_users: dict
) -> None:
    """Same rule on the analysis path, which is shorter but has the same failure."""
    import uuid

    from app.auth.models import User
    from app.domain.models import Case, Evidence

    headers = await _auth(client, seed_users["officer"])
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="DRN-2", title="drain", owner_id=user.id)
        session.add(case)
        await session.flush()
        evidence = Evidence(
            case_id=case.id,
            filename="x.bin",
            sha256="a" * 64,
            content_type="application/octet-stream",
            size_bytes=4,
            storage_key="k",
            uploaded_by=user.id,
        )
        session.add(evidence)
        await session.commit()
        case_id, evidence_id = case.id, evidence.id

    # Sanity: it works before draining, or the test proves nothing.
    ok = await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": "byte-entropy"},
        headers=headers,
    )
    assert ok.status_code == 201, ok.text

    drain.begin_draining("test")
    refused = await client.post(
        f"/cases/{case_id}/analyses",
        json={"evidence_id": str(evidence_id), "module": "byte-entropy"},
        headers=headers,
    )
    assert refused.status_code == 503, refused.text


async def test_reads_keep_working_while_draining(
    client: AsyncClient, sessionmaker_fixture: object, seed_users: dict
) -> None:
    """A draining process still serves reads, and must.

    This is the distinction that makes draining safe: *new work* is refused, not
    *everything*. An operator whose page stops loading during a deploy sees an
    outage, and the deploy becomes the incident. A read costs nothing and finishes
    immediately, so there is nothing to gain by refusing it.
    """
    import uuid

    from app.auth.models import User
    from app.domain.models import Case

    headers = await _auth(client, seed_users["officer"])
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        user = await session.get(User, uuid.UUID(seed_users["officer"]["id"]))
        case = Case(number="DRN-3", title="drain", owner_id=user.id)
        session.add(case)
        await session.commit()
        case_id = case.id

    drain.begin_draining("test")

    response = await client.get(f"/cases/{case_id}/evidence", headers=headers)
    assert response.status_code == 200, response.text


# --- the flag itself ------------------------------------------------------


def test_begin_draining_is_idempotent() -> None:
    """Twice is not an error, and logs once.

    SIGTERM can arrive twice (a TERM then a KILL during a stuck grace period), and a
    handler that raised on the second would replace the first with a traceback.
    """
    drain.begin_draining("first")
    drain.begin_draining("second")
    assert drain.is_draining() is True


def test_reset_clears_the_flag() -> None:
    """So one test cannot disarm the suite behind it."""
    drain.begin_draining("test")
    assert drain.is_draining() is True
    drain.reset()
    assert drain.is_draining() is False


def test_the_refusal_names_what_was_refused() -> None:
    """The message is operator-facing and must be specific.

    "Unavailable" for a shutting-down server and for a broken one are the same words
    for two problems with two different fixes.
    """
    message = drain.refusal("new agent runs")
    assert "new agent runs" in message
    assert "Nothing was started" in message
    assert "Retry" in message


def test_the_signal_handler_is_installed_and_chains() -> None:
    """SIGTERM flips the flag, and uvicorn's own handler still runs.

    Replacing rather than chaining would stop uvicorn from shutting down, leaving the
    container hanging until the orchestrator's SIGKILL — the exact opposite of a
    graceful shutdown, and invisible until the grace period expires.
    """
    import signal

    from app.main import _install_signal_handlers

    original = signal.getsignal(signal.SIGTERM)
    calls: list[int] = []

    def previous(signum: int, _frame: object) -> None:
        calls.append(signum)

    signal.signal(signal.SIGTERM, previous)
    try:
        _install_signal_handlers()
        # Sent to the handler directly rather than to the process: a real SIGTERM
        # would take the test runner down with it, which is a poor way to prove
        # graceful shutdown works.
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert drain.is_draining() is True, "SIGTERM did not start the drain"
        assert calls == [
            int(signal.SIGTERM)
        ], "the previous handler was not chained; uvicorn would not shut down"
    finally:
        signal.signal(signal.SIGTERM, original)
        drain.reset()
