"""T10.2 — the metrics endpoint reports what happened, and an alert can fire.

`/metrics` existing is not the same as it being useful, and a scraper returning 200
with nothing in it looks identical, from Prometheus's side, to a healthy service. So
these tests assert **content**, not availability: a metric that counts requests must
have counted the requests that were made.

The alert rules get the same treatment. A rule that parses is not a rule that fires —
the failure mode for alerting is silent, and a permanently green board is
indistinguishable from "no problems".

No Prometheus and no Grafina: `generate_latest` is the same function the endpoint
calls, so the exposition format is verified directly, and the rules are checked for
referencing metrics this build actually emits.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
import yaml
from app import drain
from app.observability import ANALYSIS_JOBS, EVIDENCE_INGESTED
from httpx import AsyncClient

OBS = pathlib.Path(__file__).resolve().parents[2] / "infra" / "observability"
RULES = OBS / "alerts.yml"
PROMETHEUS_YML = OBS / "prometheus.yml"


def exposition() -> str:
    """The exact bytes the endpoint serves."""
    from prometheus_client import generate_latest

    return generate_latest().decode()


def sample(name: str, labels: dict[str, str]) -> float | None:
    """One sample's value, or None when that series does not exist."""
    match_labels = ",".join(f'{k}="{v}"' for k, v in labels.items())
    pattern = rf"^{re.escape(name)}\{{{re.escape(match_labels)}\}} ([0-9.e+-]+)$"
    for line in exposition().splitlines():
        found = re.match(pattern, line)
        if found:
            return float(found.group(1))
    return None


def _total(name: str) -> float:
    """Sum of every series of a counter."""
    total = 0.0
    for line in exposition().splitlines():
        found = re.match(rf"^{re.escape(name)}\{{.*\}} ([0-9.e+-]+)$", line)
        if found:
            total += float(found.group(1))
    return total


def series(name: str, label_key: str) -> set[str]:
    """Every value of one label, across the series of `name`."""
    found: set[str] = set()
    for line in exposition().splitlines():
        found_match = re.match(rf"^{re.escape(name)}\{{(.*)\}}", line)
        if not found_match:
            continue
        for pair in re.findall(r'([a-z_]+)="([^"]*)"', found_match.group(1)):
            if pair[0] == label_key:
                found.add(pair[1])
    return found


def rule_expressions() -> list[tuple[str, str]]:
    rules = yaml.safe_load(RULES.read_text())
    return [
        (rule["alert"], str(rule["expr"]))
        for group in rules.get("groups", [])
        for rule in group.get("rules", [])
    ]


# --- the endpoint reports what happened -----------------------------------


async def test_requests_are_counted_per_route_and_status(client: AsyncClient) -> None:
    """Counted, and labelled well enough to alert on.

    An unlabelled request counter is a number nobody can act on: "requests went up"
    does not say which route, and the routes have wildly different expected rates. The
    route label is the *template* (`/cases/{case_id}`), not the concrete path, which
    is what keeps a per-case id from creating a new time series on every request.
    """
    assert not series(
        "forensight_http_requests_total", "route"
    ), "nothing has been requested yet; the assertions below would pass vacuously"

    await client.get("/readyz")
    assert series(
        "forensight_http_requests_total", "route"
    ), "a request was made and no route series appeared"

    # The template, not the concrete path: a UUID in a label is a cardinality bomb.
    for route in series("forensight_http_requests_total", "route"):
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", route), (
            f"route label {route!r} contains a uuid; every case would create its own "
            f"time series and the metric would become unusable"
        )


async def test_two_statuses_are_counted_separately(client: AsyncClient) -> None:
    """A 200 and a 503 are different facts, and must not be one number.

    `/readyz` returns 503 by design whenever a dependency is down or the process is
    draining. Counting that as the same thing as success would make an outage
    indistinguishable from normal operation.
    """
    drain.begin_draining("test")
    try:
        response = await client.get("/readyz")
        assert response.status_code == 503
    finally:
        drain.reset()

    statuses = series("forensight_http_requests_total", "status")
    assert "503" in statuses, f"a 503 was not counted under its own status: {statuses}"


async def test_latency_is_a_histogram_with_usable_buckets(client: AsyncClient) -> None:
    """A histogram, not a summary.

    Prometheus cannot compute percentiles across instances from a summary — each
    process would carry its own quantiles and they would not be comparable. Buckets
    also survive a restart, which summaries do not.
    """
    await client.get("/readyz")
    text = exposition()

    assert "forensight_http_request_duration_seconds_bucket" in text
    assert "forensight_http_request_duration_seconds_sum" in text
    assert "forensight_http_request_duration_seconds_count" in text
    # Realistic buckets across four orders of magnitude: an evidence download and a
    # health check share this metric.
    for bucket in ("0.01", "0.5", "5.0", "60.0"):
        assert f'le="{bucket}"' in text, f"no bucket at {bucket}s"


async def test_a_scrape_is_counted_but_no_rule_alerts_on_it(client: AsyncClient) -> None:
    """A scrape *is* a request, so counting it is right. Alerting on it is not.

    An earlier draft of this test asserted `/metrics` never appears in its own output,
    and failed. That was the test being wrong: omitting it would mean the request
    counter silently disagreed with reality. The requirement is on the *rules* — a
    scrape every 15 seconds is a flat, reliable source of traffic with nothing to do
    with anyone's latency or error rate.
    """
    before = series("forensight_http_requests_total", "route")
    await client.get("/metrics")
    assert "/metrics" in series("forensight_http_requests_total", "route")

    checked = 0
    for name, expr in rule_expressions():
        if "forensight_http_request" not in expr:
            continue
        checked += 1
        # Checked on the label's matcher rather than on one exact spelling: the
        # exclusion is legitimately a regex covering /readyz and /metrics together.
        excluded = re.findall(r'route!?=?~?"([^"]+)"', expr)
        assert any(
            "metrics" in pattern for pattern in excluded
        ), f"{name} aggregates over the metrics endpoint without excluding it"
    assert checked, "no rule reads the request metrics, so nothing was checked"


async def test_metrics_content_type_is_the_exposition_format(
    client: AsyncClient,
) -> None:
    """A wrong content type is rejected by the scraper after it has connected."""
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert (
        "text/plain" in response.headers["content-type"]
    ), f"content-type is {response.headers['content-type']!r}"


def test_domain_metrics_are_declared_at_import() -> None:
    """Declared and registered at import, so they appear at zero rather than absent.

    An unlabelled counter does appear at zero; a *labelled* one does not. That
    difference is the next test, and it is the reason this one exists.
    """
    assert "forensight_evidence_ingested_total" in exposition()


def test_the_labelled_job_counter_is_absent_until_a_job_runs() -> None:
    """A real gap, recorded rather than papered over.

    `prometheus_client` emits nothing for a labelled counter until `.labels()` is
    called, so `forensight_analysis_jobs_total` is absent from `/metrics` on a
    deployment that has analysed nothing — and the rule watching it sits on an empty
    vector, which is silent rather than firing.

    It is not fixable in the metric: pre-initialising every (module, outcome)
    combination means enumerating modules that plugins register at runtime. The
    honest options are the alert's `absent()` variant or accepting that this rule only
    exists once the first job has run. Asserted so the behaviour is known rather than
    discovered during an incident.
    """
    text = exposition()
    # HELP/TYPE lines are emitted for a declared collector even with no samples, so
    # what matters is that no *sample* exists. That is what makes the rule's vector
    # empty, and an empty vector never fires.
    assert not re.search(
        r"^forensight_analysis_jobs_total\{", text, re.M
    ), "the job counter has samples, so this test needs re-evaluating"
    assert any(
        "forensight_analysis_jobs_total" in expr for _, expr in rule_expressions()
    ), "the job rule has gone; the metric is now unobserved"
    assert any(
        name == "ForenSightAPIDown" for name, _ in rule_expressions()
    ), "with the job counter possibly absent, `up{}` is what keeps this observable"


def test_the_job_counter_labels_outcomes_not_statuses() -> None:
    """`outcome`, not `status`: the pipeline reports four terminal states.

    `done`, `failed`, `unsupported` and `not_applicable` are not HTTP statuses, and
    only the first two are HTTP-shaped. Forcing them into {2xx, 5xx} would make
    `unsupported` masquerade as a failure — the exact distinction this project exists
    to keep, and one the alert rule depends on.
    """
    assert set(ANALYSIS_JOBS._labelnames) == {"module", "outcome"}
    assert not EVIDENCE_INGESTED._labelnames


def test_the_failure_alert_does_not_count_the_two_non_failures() -> None:
    """`unsupported` and `not_applicable` are not failures.

    A module declining an input it cannot honestly score did exactly what it should,
    and paging someone every time it happens trains people to ignore the page.
    """
    failure = next(expr for name, expr in rule_expressions() if "AnalysisFailures" in name)
    assert 'outcome="failed"' in failure
    assert 'outcome="unsupported"' not in failure
    assert 'outcome="not_applicable"' not in failure


# --- the scrape config actually points at this service --------------------


def test_prometheus_scrapes_this_service() -> None:
    """A wrong host or path is a permanently-down target that looks like an outage."""
    config = yaml.safe_load(PROMETHEUS_YML.read_text())
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert "forensight-api" in jobs, f"no scrape job for the API: {sorted(jobs)}"

    job = jobs["forensight-api"]
    assert job["metrics_path"] == "/metrics"
    assert any("api:8000" in target for target in job["static_configs"][0]["targets"])


def test_the_rules_file_is_wired_into_prometheus() -> None:
    """A rule file nothing loads is a file that does nothing.

    `rule_files` absent means the alerts exist as a reviewable artefact and as
    nothing else — which is precisely the shape of "we have alerting" in a repo.
    """
    config = yaml.safe_load(PROMETHEUS_YML.read_text())
    files = config.get("rule_files") or []
    assert files, "prometheus.yml loads no rule files"
    assert any("alerts.yml" in str(f) for f in files), files


# --- alerts that can actually fire ----------------------------------------


def test_every_referenced_metric_is_emitted_by_this_build() -> None:
    """A rule against a metric this build does not emit never fires.

    The rule parses, the dashboard renders, and there is no alert — while the board
    stays green through the outage the rule was written for.
    """
    emitted = set(re.findall(r"^(forensight_[a-z_]+)", exposition(), re.M))
    referenced = set(re.findall(r"forensight_[a-z_]+", RULES.read_text()))

    unknown = referenced - emitted - {"forensight_analysis_jobs_total"}
    assert not unknown, (
        f"alerts.yml references metrics this build does not emit: {sorted(unknown)}. "
        f"Emitted: {sorted(emitted)}"
    )


def test_every_rule_groups_only_by_labels_that_exist() -> None:
    """`by (x)` on a label nothing carries produces an empty vector."""
    for name, expr in rule_expressions():
        for group in re.findall(r"\bby\s*\(([^)]*)\)", expr):
            for label in re.findall(r"[a-z_]+", group):
                assert label in {"method", "route", "status", "module", "outcome", "le"}, (
                    f"{name} groups by {label!r}, which no metric in this service "
                    f"labels by; the grouping would be empty and the alert silent"
                )


def test_every_rule_is_annotated_and_severe_enough_to_act_on() -> None:
    """A notification with no summary is unreadable, and one with no severity is triaged.

    Asserted per rule rather than once, because a rule added next year without them is
    the ordinary way this regresses.
    """
    rules = yaml.safe_load(RULES.read_text())
    seen = 0
    for group in rules["groups"]:
        for rule in group.get("rules", []):
            seen += 1
            assert rule.get("labels", {}).get("severity") in {
                "critical",
                "warning",
            }, f"{rule['alert']} has no usable severity"
            assert rule.get("annotations", {}).get(
                "summary"
            ), f"{rule['alert']} has no summary, so the notification is unreadable"
    assert seen >= 4, f"only {seen} rules; an alert set that small is not a safety net"


def test_no_rule_fires_on_a_single_sample() -> None:
    """`for` must be a real, non-zero duration on every rule.

    `for: 0` fires on one bad sample, which produces a flapper. No `for` at all means
    the alert fires on a single scrape's worth of noise.
    """
    for name, expr in rule_expressions():
        rules = yaml.safe_load(RULES.read_text())
        match = next(r for g in rules["groups"] for r in g.get("rules", []) if r["alert"] == name)
        assert "for" in match, f"{name} has no `for`, so it fires on one sample"
        duration = str(match["for"])
        assert re.search(r"\d+[smh]", duration), f"{name}: `for: {duration}` is not a duration"
        assert re.search(r"\b[1-9]", duration), f"{name}: `for: {duration}` fires immediately"
        del expr


def test_promtool_validates_the_rules_when_available() -> None:
    """The real validator, when the real binary is present.

    Skipped rather than faked: a rule promtool rejects is a rule that will not load, and
    pretending it was checked is exactly the false assurance this suite exists to
    avoid.
    """
    if shutil.which("promtool") is None:
        pytest.skip("promtool is not installed on this host")

    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [shutil.which("promtool") or "promtool", "check", "rules", str(RULES)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
