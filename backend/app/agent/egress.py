"""Egress control: deciding what this deployment is allowed to send to the internet.

An air-gapped forensic lab is a real deployment, not a hypothetical one — case data
should not leave the building, and "we only send to the model provider" is a promise
an operator has to be able to *enforce* rather than one they have to trust.

**The surface here is genuinely one call.** `llm.py` holds the only outbound HTTP in
`app/`; Postgres and MinIO are on the deployment's own network. That makes this
small, and it is worth saying plainly rather than implying broader coverage: this
control governs the model provider, and nothing else in the application makes an
outbound request to begin with.

**An allowlist of hosts, not a boolean.** `FORENSIGHT_EGRESS_ALLOWED_HOSTS` lists
what may be reached. Turning egress *off* entirely is the same as an empty
allowlist, and is expressed that way, so there is one concept rather than a mode
flag and a list that can disagree.

**Checked when the client is built, not per request.** Per-request checking would be
correct and would also be checking a string that has not changed since construction —
and a check that runs on every call is a check that eventually gets wrapped in a
try/except by someone making the endpoint faster.

**Scheme matters.** An `https` host is permitted where the same name over plain `http`
is not, unless the deployment says otherwise, because a permitted hostname reached
over plaintext is a permitted hostname anyone on the path can read. Local addresses
are permitted without being listed, because a deployment pointing at its own Ollama
is not egressing — and requiring operators to allowlist `ollama` would teach them
that the control is noise.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: Hosts treated as "not egress" even when unlisted.
#:
#: `localhost` and `127.0.0.1` are literal. `.local` is not — mDNS names resolve on a
#: LAN, and a name ending in `.local` can be claimed by any host on that LAN.
# 0.0.0.0 is here because a configured base_url occasionally is, and reaching it
# reaches localhost. It is not a name anything off-host can claim.
LOCAL_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "::1",
        "[::1]",
        # 0.0.0.0 is here because a configured base_url occasionally is, and reaching
        # it reaches localhost. It is not a name anything off-host can claim.
        "0.0.0.0",  # noqa: S104
        # Docker Desktop's alias for the host machine. Dotted, so it is not covered by
        # the single-label rule, and it is exactly the address an operator reaches for
        # when running a local model on the host — which made its absence a hole in a
        # control whose whole job is to have no holes.
        "host.docker.internal",
    }
)


#: Hostnames that look local because they are single-label, container-network names
#: like `ollama` or `host.docker.internal`.
def _is_single_label(host: str) -> bool:
    return "." not in host and ":" not in host


class EgressBlocked(RuntimeError):
    """This deployment is not permitted to reach the configured host."""


def allowed_hosts(raw: str) -> frozenset[str]:
    """Parse the configured allowlist.

    Comma or whitespace separated, lower-cased. Entries may carry a scheme
    (`https://api.example.com`) to pin that host to TLS; a bare hostname permits any
    scheme. Pinned entries are kept with the scheme so `permits` can tell
    `https://x` from `http://x`.
    """
    out: set[str] = set()
    for token in raw.replace(",", " ").split():
        token = token.strip().lower()
        if not token:
            continue
        if "://" in token:
            parsed = urlsplit(token)
            if parsed.hostname:
                out.add(f"{parsed.scheme}://{parsed.hostname.lower()}")
            continue
        out.add(token)
    return frozenset(out)


#: A hostname is letters, digits, dots and hyphens, optionally with a port. Used to
#: reject junk before it is classified, because the single-label rule treats *any*
#: dotless string as a container-network name — so "not a url" would otherwise be
#: permitted as local. A control that permits malformed input is a control whose
#: classification can be talked past by a typo.
_HOSTNAME = re.compile(r"\A[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?\Z")


def _split(url: str) -> tuple[str, str]:
    parsed = urlsplit(url if "://" in url else f"//{url}")
    return parsed.scheme.lower(), (parsed.hostname or "").lower()


def permits(url: str, allowed: frozenset[str]) -> bool:
    """Whether `url`'s host may be reached under this allowlist.

    Split out from `check` so the policy is testable on its own and so a caller who
    wants to ask a question without provoking an exception can.
    """
    scheme, host = _split(url)
    if not host or not _HOSTNAME.match(host):
        return False

    if host in LOCAL_HOSTS or _is_single_label(host):
        # A local or container-network endpoint is not egress. See the module
        # docstring for why `.local` is deliberately not in this set.
        return True

    # Scheme-pinned entry.
    if f"{scheme}://{host}" in allowed:
        return True
    # Bare hostname: permits any scheme, which is the operator's choice.
    return host in allowed


def check(url: str, allowed: frozenset[str], *, what: str = "the model provider") -> None:
    """Raise unless `url` is permitted.

    `what` names the caller, because the useful message is "you configured a cloud
    provider on an air-gapped deployment" rather than a bare hostname.
    """
    if permits(url, allowed):
        return
    scheme, host = _split(url)
    listed = ", ".join(sorted(allowed)) or "nothing"
    raise EgressBlocked(
        f"{what} at {host!r} ({scheme or 'no scheme'}) is not permitted by this "
        f"deployment's egress allowlist ({listed}). Either point the provider at a "
        f"local endpoint, or add the host to FORENSIGHT_EGRESS_ALLOWED_HOSTS. "
        f"Egress controls where evidence-related prompts may be sent, and an "
        f"unreviewed host would send them somewhere nobody has agreed to — which is "
        f"the whole point of an air-gapped deployment."
    )


def check_settings(settings: object) -> None:
    """Enforce the allowlist against whatever the deployment configured.

    Called by `resolve_model`, so it cannot be bypassed by choosing a different code
    path to build a client.
    """
    raw = str(getattr(settings, "egress_allowed_hosts", "") or "")
    allowed = allowed_hosts(raw)
    base = str(getattr(settings, "llm_base_url", "") or "")
    if base:
        check(base, allowed)


__all__ = ["LOCAL_HOSTS", "EgressBlocked", "allowed_hosts", "check", "check_settings", "permits"]
