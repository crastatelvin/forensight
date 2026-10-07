"""T10.5 — an air-gapped deployment actually cannot reach a cloud provider.

The property is *negative*: it is not "the allowlist works", it is "there is no
configured provider this deployment can reach that it was not told about". A control
that only ever blocks the obvious case is a control that stops working the first time
someone puts an unusual host in it.

The surface being guarded is exactly one call. `app/agent/llm.py` holds the only
outbound HTTP in `app/` — Postgres and MinIO are on the deployment's own network — so
these tests assert a claim that is actually true of this codebase rather than a
general one about the application.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from app.agent.egress import (
    LOCAL_HOSTS,
    EgressBlocked,
    allowed_hosts,
    check,
    permits,
)
from app.agent.llm import ModelError, resolve_model
from app.config import Settings


class _Settings(Settings):
    secret_key: str = "x" * 40  # type: ignore[assignment]


def settings(**overrides: object) -> Settings:
    """Defaults to a local Ollama, then applies overrides.

    The defaults go through a dict rather than being written as explicit keyword
    arguments, because a caller overriding one of them then collides with the
    literal — which is a confusing error for a test helper to raise.
    """
    return Settings(
        **{
            "secret_key": "x" * 40,
            "minio_endpoint": "",
            "llm_provider": "ollama",
            "llm_model": "qwen2.5:3b",
            **overrides,
        }  # type: ignore[arg-type]
    )


# --- the policy, on its own -----------------------------------------------


def test_an_empty_allowlist_permits_nothing_public() -> None:
    """The air-gapped default: blank configuration means no egress.

    Defaulting to permissive would make the control opt-in, and a control that has to
    be switched on before it does anything is a control most deployments never turn
    on.
    """
    assert not permits("https://api.openai.com/v1", frozenset())
    assert not permits("https://api.anthropic.com/v1", frozenset())


def test_local_and_container_endpoints_are_not_egress() -> None:
    """A local Ollama is the air-gapped deployment working correctly.

    Requiring it on the allowlist would teach operators the control is noise, and the
    one thing they most need to do in an air-gapped lab would be the one thing that
    needed a config change.
    """
    for url in (
        "http://localhost:11434/v1",
        "http://127.0.0.1:11434/v1",
        "http://ollama:11434/v1",
        "http://host.docker.internal:11434/v1",
    ):
        assert permits(url, frozenset()), url


def test_a_dot_local_name_is_treated_as_egress() -> None:
    """`.local` is mDNS, and mDNS names resolve on a LAN.

    Treating it as "local" would let any host on the network claim the name and
    receive every prompt, which is the exact thing an air-gapped control exists to
    prevent — reached by a name that looks harmless.
    """
    assert not permits("http://printer.local:11434/v1", frozenset())
    # The set is a positive list of literals; the rule that would make this local is
    # the single-label one, and this name is not single-label. Asserted directly so a
    # future "helpfully" adding `.local` to the literals fails here.
    assert "printer.local" not in LOCAL_HOSTS


def test_a_listed_host_is_permitted() -> None:
    allowed = allowed_hosts("api.example.com")
    assert permits("https://api.example.com/v1", allowed)
    assert not permits("https://api.other.com/v1", allowed)


def test_a_scheme_pinned_entry_does_not_permit_plaintext() -> None:
    """`https://x` allowed does not mean `http://x` allowed.

    A permitted hostname reached over plaintext is a permitted hostname anyone on the
    path can read, and pinning the scheme is the only way to say "and it must be
    encrypted" in one list.
    """
    allowed = allowed_hosts("https://api.example.com")
    assert permits("https://api.example.com/v1", allowed)
    assert not permits("http://api.example.com/v1", allowed)


def test_the_allowlist_parses_commas_whitespace_and_schemes() -> None:
    assert allowed_hosts("a.com, b.com  c.com") == frozenset({"a.com", "b.com", "c.com"})
    assert allowed_hosts("https://a.com") == frozenset({"https://a.com"})
    assert allowed_hosts("") == frozenset()
    # Case does not matter, because a host comparison that is case-sensitive would be
    # bypassed by writing the same host in a different case.
    assert permits("https://API.Example.COM/v1", allowed_hosts("api.example.com"))


def test_a_malformed_url_is_refused_rather_than_crashing() -> None:
    """Unparseable is not "permitted because we could not tell".

    The dotless case is the sharp one. Every dotless host is treated as a
    container-network name and therefore as local — which is right for `ollama` and
    catastrophic for `"not a url at all"`, because a string with no dot would
    otherwise be classified as local by default. So the host is validated as a
    hostname *before* it is classified.
    """
    assert not permits("", frozenset())
    assert not permits("https://", frozenset())
    assert not permits("not a url at all", frozenset())
    assert not permits("http://bad host:11434/v1", frozenset())
    assert not permits("http://-leading-hyphen.example.com/v1", frozenset())


def test_the_refusal_names_the_host_and_the_setting() -> None:
    """An operator has to be able to act on this at 3am.

    The message must name the host (so they know what was blocked), the setting (so
    they know where to change it), and say why — not merely that it failed.
    """
    with pytest.raises(EgressBlocked) as caught:
        check("https://api.openai.com/v1", frozenset())

    message = str(caught.value)
    assert "api.openai.com" in message
    assert "FORENSIGHT_EGRESS_ALLOWED_HOSTS" in message
    assert "air-gapped" in message.lower()


# --- the wiring -----------------------------------------------------------


def test_resolving_a_cloud_provider_is_blocked_by_default() -> None:
    """The end-to-end property: a cloud provider cannot be reached unconfigured."""
    with pytest.raises(EgressBlocked):
        resolve_model(
            settings(
                llm_provider="cloud",
                llm_model="gpt-4o",
                llm_base_url="https://api.openai.com/v1",
            )
        )


def test_resolving_a_local_model_is_unaffected() -> None:
    """The air-gapped deployment keeps working. This is the regression to guard."""
    model = resolve_model(settings(llm_base_url="http://ollama:11434/v1"))
    assert model.name == "ollama"


def test_an_explicitly_permitted_cloud_provider_works() -> None:
    """Listing a host is how you say yes, not editing code."""
    model = resolve_model(
        settings(
            llm_provider="cloud",
            llm_model="gpt-4o",
            llm_base_url="https://api.openai.com/v1",
            egress_allowed_hosts="api.openai.com",
        )
    )
    assert model.name == "cloud"


def test_the_check_runs_before_the_provider_is_chosen() -> None:
    """Structural: `resolve_model` cannot be made to skip the check.

    If the check lived inside the cloud branch, choosing `provider="openai"` — or any
    future spelling — would route around it. `test_capabilities_never_promise_an_
    unimplemented_module` is the same lesson in a different file.
    """
    source = pathlib.Path(__file__).resolve().parent.parent / "app" / "agent" / "llm.py"
    text = source.read_text()
    tree = ast.parse(text)

    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_model"
    )
    calls = [
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "check_settings" in calls

    # And it must come before the provider branch, so no branch can be reached first.
    first_check = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "check_settings"
    )
    provider_branch = next(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "provider" for t in node.targets)
    )
    assert first_check < provider_branch, (
        "the egress check must run before the provider is decided, or a branch could "
        "be reached without it"
    )


def test_the_llm_client_is_the_only_outbound_http_in_app() -> None:
    """The claim the whole control rests on, asserted.

    `app/agent/llm.py` is the only place `app/` makes an outbound request. If that
    stops being true — a webhook, a geocoder, an OCR service — this control silently
    stops covering anything, and it would keep reporting that the deployment is
    air-gapped. An AST scan rather than a grep, because `grep http` finds comments.
    """
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name == "llm.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"httpx", "requests", "aiohttp"}:
                        offenders.append(f"{path.name}: imports {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in {"httpx", "requests", "aiohttp"}
            ):
                offenders.append(f"{path.name}: imports from {node.module}")

    assert not offenders, (
        "new outbound HTTP outside app/agent/llm.py, which is not covered by the "
        f"egress allowlist: {offenders}"
    )


def test_a_blocked_provider_fails_loudly_rather_than_silently_going_offline() -> None:
    """The agent must say "blocked", not "no model configured".

    Two different problems with two different fixes, and the operator has to be told
    which one they have.
    """
    with pytest.raises(EgressBlocked) as blocked:
        resolve_model(
            settings(
                llm_provider="cloud",
                llm_model="gpt-4o",
                llm_base_url="https://api.openai.com/v1",
            )
        )
    assert "EGRESS_ALLOWED_HOSTS" in str(blocked.value)

    with pytest.raises(ModelError, match="no LLM configured"):
        resolve_model(settings(llm_provider="cloud", llm_model=""))
