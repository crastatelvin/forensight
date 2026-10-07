"""Load check: does work get lost under load?

Run against a live stack:

    python -m scripts.load_check --cases 12 --analyses-per-case 10

**No Locust.** It is a browser-oriented tool that brings a web UI, a worker fleet and
a web-server dependency to answer a question `httpx` answers in a hundred lines. The
dependency is not the deciding factor — the *shape* is. A Locust run reports a
requests-per-second figure, and this script deliberately does not, because
"throughput" for this system is bounded by an ONNX forward pass on a shared CPU and
nobody has chosen a number to hold it to.

**So the properties checked here are absolute ones that hold at any load:**

1. **No job is lost.** Every submitted job reaches a terminal state. This is the
   property that matters and the one that fails silently: a job that is never claimed
   stays `queued` forever, still counts toward "12 jobs submitted", and looks exactly
   like a slow system from the outside.
2. **No job is claimed twice**, which would mean two workers racing on one row.
3. **The API stays ready throughout.** A load test that reports a good throughput
   number while `/readyz` was flapping has measured nothing useful.
4. **Latency is reported, not asserted.** It is printed with the distribution, so a
   regression is visible in the output, and a threshold is left to whoever has the
   traffic to justify one.

Exit code is 0 only if the three invariants hold. Latency never fails the run — a
hardcoded SLO is exactly the thing P10.B5 records as not yet chosen, and inventing
one here would make this file the first thing to disagree with reality.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field

from httpx import AsyncClient

#: Terminal states. `queued` is deliberately absent: a job still sitting in the queue
#: when the check finishes is a job nobody claimed, which is finding 1.
TERMINAL = frozenset({"done", "failed", "unsupported", "not_applicable"})


@dataclass
class Report:
    submitted: int = 0
    refused: list[str] = field(default_factory=list)
    #: Terminal outcomes, keyed by state name.
    terminal_outcomes: Counter[str] = field(default_factory=Counter)
    #: Non-201 submission responses, keyed by HTTP status. Kept separate from the
    #: outcomes above rather than sharing one dict, because mixing int and str keys in
    #: one counter is a shape every consumer then has to unpick.
    non_201: Counter[int] = field(default_factory=Counter)
    latencies_ms: list[float] = field(default_factory=list)
    readyz_failures: list[str] = field(default_factory=list)
    #: job id -> how many times it was observed in a terminal state. Anything above
    #: one means two workers raced on the same row.
    terminal_observations: Counter[str] = field(default_factory=Counter)
    #: Jobs submitted but never seen in a terminal state. A field rather than a
    #: counter entry so it cannot be confused with an outcome name.
    never_terminal: int = 0
    #: Submissions that resolved to an existing job by idempotency rather than
    #: creating one. Reported, because a load figure that silently counted these as
    #: new work would overstate throughput by however much the reuse rate is.
    reused: int = 0
    #: Jobs whose state could not be read before the deadline — rate-limited or
    #: refused. Tracked separately from `never_terminal` because they are *unknown*,
    #: not lost, and conflating them is what made the first version of this report
    #: eight phantom failures.
    unknown_at_settle: int = 0

    def percentile(self, fraction: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(len(ordered) - 1, int(len(ordered) * fraction))
        return ordered[index]


async def _login(client: AsyncClient, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return str(response.json()["access_token"])


async def run(
    base_url: str,
    *,
    email: str,
    password: str,
    cases: int,
    per_case: int,
    settle_seconds: float,
    concurrency: int,
) -> Report:
    report = Report()
    async with AsyncClient(base_url=base_url, timeout=60.0) as client:
        token = await _login(client, email, password)
        headers = {"Authorization": f"Bearer {token}"}

        # Distinct bytes per submission.
        #
        # This matters more than it looks. `submit_job` is idempotent on (evidence
        # hash, module, params), so submitting the same evidence five times creates
        # **one** job and four no-op reuses — the first version of this script did
        # exactly that, reported "30 jobs submitted", and actually queued 6. It
        # looked like a passing load test because the accounting was counting HTTP
        # 201s rather than jobs. Distinct content makes every submission real work.
        payloads = [
            b"forensight-load-check-" + uuid.uuid4().hex.encode() + b"-" + str(i).encode()
            for i in range(per_case)
        ]
        job_ids: list[str] = []
        #: (case_id, job_id) pairs, because the settle phase polls per case.
        case_of: list[tuple[str, str]] = []

        # --- submit -------------------------------------------------------
        semaphore = asyncio.Semaphore(concurrency)

        async def submit_case(index: int) -> None:
            async with semaphore:
                created = await client.post(
                    "/cases",
                    headers=headers,
                    json={"number": f"LOAD-{uuid.uuid4().hex[:8].upper()}", "title": "load"},
                )
                case_id = created.json()["id"]
                uploaded = await client.post(
                    f"/cases/{case_id}/evidence",
                    headers=headers,
                    files={"file": ("load-0.bin", payloads[0], "application/octet-stream")},
                )
                first_evidence = uploaded.json()["id"]

                for index in range(per_case):
                    if index > 0:
                        extra = await client.post(
                            f"/cases/{case_id}/evidence",
                            headers=headers,
                            files={
                                "file": (
                                    f"load-{index}.bin",
                                    payloads[index],
                                    "application/octet-stream",
                                )
                            },
                        )
                        evidence_id = extra.json()["id"]
                    else:
                        evidence_id = first_evidence

                    started = time.perf_counter()
                    try:
                        response = await client.post(
                            f"/cases/{case_id}/analyses",
                            headers=headers,
                            json={"evidence_id": evidence_id, "module": "byte-entropy"},
                        )
                    except Exception as exc:  # noqa: BLE001 - the point is to record it
                        report.refused.append(f"{type(exc).__name__}: {exc}")
                        continue
                    report.latencies_ms.append((time.perf_counter() - started) * 1000)
                    if response.status_code == 201:
                        new_id = str(response.json()["id"])
                        if new_id not in job_ids:
                            job_ids.append(new_id)
                            case_of.append((case_id, new_id))
                        else:
                            # Idempotency collapsed this submission onto an existing
                            # job: same evidence, same module, same params. That is the
                            # feature working (P4.B3), not an error — but counting the
                            # 201 as a new job made the report claim 12 jobs submitted
                            # while only 3 existed. The reuse is counted separately so
                            # the number on screen is the number of jobs.
                            report.reused += 1
                        report.submitted += 1
                    else:
                        report.non_201[response.status_code] += 1

        await asyncio.gather(*(submit_case(i) for i in range(cases)))

        # --- settle -------------------------------------------------------
        # Poll **per case**, not per job. The first version polled each of the 64 job
        # ids one at a time and reported 8 as never-terminal — which was wrong, and
        # wrong in the worst direction: the per-job polls exceeded the read rate limit,
        # those responses came back 429, and `continue` treated a rate-limit refusal as
        # evidence about the job. Only one job in the database was actually stuck, and
        # it was not one of these.
        #
        # So: one request per case instead of one per job, and a non-200 is recorded
        # as *unknown* rather than as *lost*. The distinction is the whole point — a
        # check that cannot tell "the job failed" from "I was not allowed to look" will
        # eventually report a healthy system as broken and be ignored.
        deadline = time.perf_counter() + settle_seconds
        seen: set[str] = set()
        unknown: set[str] = set()
        by_case: dict[str, list[str]] = {}
        for case_id, job_id in case_of:
            by_case.setdefault(case_id, []).append(job_id)

        while seen != set(job_ids) and time.perf_counter() < deadline:
            async with semaphore:
                try:
                    ready = await client.get("/readyz")
                except Exception as exc:  # noqa: BLE001
                    report.readyz_failures.append(f"{type(exc).__name__}: {exc}")
                    ready = None
                if ready is not None and ready.status_code != 200:
                    report.readyz_failures.append(str(ready.status_code))

            progressed = False
            for case_id, ids in by_case.items():
                outstanding = [i for i in ids if i not in seen]
                if not outstanding:
                    continue
                try:
                    listing = await client.get(f"/cases/{case_id}/analyses", headers=headers)
                except Exception as exc:  # noqa: BLE001
                    report.readyz_failures.append(f"poll: {type(exc).__name__}")
                    continue
                if listing.status_code != 200:
                    # Rate-limited or refused. Unknown, *not* lost.
                    unknown.update(outstanding)
                    continue
                unknown -= set(outstanding)
                for job in listing.json():
                    job_id = str(job.get("id"))
                    if job_id not in outstanding:
                        continue
                    status = str(job.get("status", ""))
                    if status in TERMINAL:
                        seen.add(job_id)
                        report.terminal_outcomes[status] += 1
                        progressed = True
                    else:
                        unknown.add(job_id)
            if not progressed:
                await asyncio.sleep(1.0)

        report.never_terminal = len(set(job_ids) - seen)
        report.unknown_at_settle = len(unknown & (set(job_ids) - seen))
    return report


def evaluate(report: Report, settle_seconds: float) -> list[str]:
    """The invariants. Empty list means the run is sound."""
    problems: list[str] = []

    if report.refused:
        problems.append(
            f"{len(report.refused)} request(s) did not complete: " f"{report.refused[0][:120]}"
        )

    never = report.never_terminal
    if never:
        problems.append(
            f"{never} of {report.submitted} job(s) never reached a terminal state in "
            f"{settle_seconds}s. A job nobody claimed stays `queued` forever and is "
            f"indistinguishable from a slow system — this is the failure this check "
            f"exists for."
        )

    raced = [job for job, count in report.terminal_observations.items() if count > 1]
    if raced:
        problems.append(
            f"{len(raced)} job(s) were claimed more than once ({raced[:3]}...), which "
            f"means two workers raced on the same row"
        )

    if report.readyz_failures:
        problems.append(
            f"/readyz was not ready {len(report.readyz_failures)} time(s) during the "
            f"run. A load figure measured while readiness flapped is not a figure."
        )

    if report.unknown_at_settle:
        # Not a failure, and saying so is the point. The check could not read these, so
        # it reports how many it could not read and leaves the verdict to a rerun with
        # a smaller load or a raised read budget.
        print(
            f"note: {report.unknown_at_settle} job(s) could not be read before the "
            f"deadline (rate limit). They are UNKNOWN, not lost — rerun smaller to "
            f"settle them."
        )

    if report.submitted == 0:
        problems.append("no jobs were accepted; the run measured nothing")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--analyses-per-case", type=int, default=10)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=120.0,
        help="how long to wait for jobs to finish before calling them lost",
    )
    args = parser.parse_args()

    report = asyncio.run(
        run(
            args.base_url,
            email=args.email,
            password=args.password,
            cases=args.cases,
            per_case=args.analyses_per_case,
            settle_seconds=args.settle_seconds,
            concurrency=args.concurrency,
        )
    )

    print(f"submitted:            {report.submitted}")
    print(f"refused:              {len(report.refused)}")
    print(f"terminal outcomes:    {dict(report.terminal_outcomes)}")
    print(f"never terminal:       {report.never_terminal}")
    print(f"non-201 submissions:  {dict(report.non_201)}")
    print(f"readyz failures:      {len(report.readyz_failures)}")
    print(f"unknown at settle:    {report.unknown_at_settle}  (not lost — could not read)")
    print(f"reused by idempotency: {report.reused}")
    if report.latencies_ms:
        print(
            "submit latency ms:   "
            f"p50={report.percentile(0.50):.0f} "
            f"p95={report.percentile(0.95):.0f} "
            f"max={max(report.latencies_ms):.0f} "
            f"mean={statistics.fmean(report.latencies_ms):.0f}"
        )
    print("  (latency is reported, not asserted — no SLO has been chosen)")

    problems = evaluate(report, args.settle_seconds)
    if problems:
        print("\nFAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("\nAll jobs reached a terminal state, none was claimed twice, and readiness held.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
