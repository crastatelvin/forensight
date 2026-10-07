"""The fixture gate refuses what it should, and permits what it should.

A gate that has never been seen to refuse is a gate that might not work — the same
argument as the security scanners in `scripts/verify_security_gates.py`, applied to
licensing. So the refusals are the tests here, not the successes.

**The refusals are the design.** Each one corresponds to a real thing found while
researching Phase 9's missing fixtures:

- a **gated** source (`nuriachandra/Deepfake-Eval-2024`, whose terms require attested
  prior deepfake-detection work and restrict use to evaluation);
- an **unstated** licence (every ungated deepfake dataset found on the Hub);
- **CC BY-NC-ND-4.0** (the one usable fingerprint set — NoDerivatives forbids the
  derived golden set this project needs);
- **CC BY-SA-4.0**, refused *despite* this repository having no `LICENSE` file and so
  nothing to conflict with today. That is a decision, and a decision should be tested
  or it will quietly change.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import urllib.error

import pytest
from scripts import fetch_fixtures as ff

PERMITTED = ff.Fixture(
    url="https://example.org/fixture.bin",
    sha256="a" * 64,
    licence="cc-by-4.0",
    role="deepfake/real",
)


# --- what must be refused -------------------------------------------------


def test_a_dataset_with_no_stated_licence_is_refused() -> None:
    """Unstated is not permission.

    Every ungated deepfake dataset found on the Hub has no licence tag at all, so this
    is not a hypothetical refusal — it is the common case.
    """
    with pytest.raises(ff.FixtureRefused, match="states no licence"):
        ff.Fixture(url="u", sha256="a" * 64, licence="unstated", role="x/y").check_licence()

    with pytest.raises(ff.FixtureRefused, match="states no licence"):
        ff.Fixture(url="u", sha256="a" * 64, licence="", role="x/y").check_licence()


def test_nd_is_refused_with_its_reason_stated() -> None:
    """NoDerivatives forbids creating a modified copy, and a fixture set is one."""
    with pytest.raises(ff.FixtureRefused) as caught:
        ff.Fixture(url="u", sha256="a" * 64, licence="cc-by-nd-4.0", role="f/p").check_licence()

    message = str(caught.value)
    assert "derivative" in message
    # It must say *why*, not merely that the string is not on a list: someone with a
    # legitimate reason to reconsider needs to know what they are reconsidering.
    assert "NoDerivatives" in message


def test_sa_is_refused_even_though_this_repo_has_no_licence() -> None:
    """A deliberate decision, tested so it cannot change by accident.

    There is no `LICENSE` file here, so ShareAlike conflicts with nothing today. It is
    refused anyway because fixtures get copied into the repository and the obligation
    would then bind whoever adds a licence later to a term they never chose.
    """
    assert not pathlib.Path(__file__).resolve().parents[2].joinpath("LICENSE").exists()

    with pytest.raises(ff.FixtureRefused, match="ShareAlike"):
        ff.Fixture(url="u", sha256="a" * 64, licence="cc-by-sa-4.0", role="f/p").check_licence()


def test_a_gated_dataset_is_refused() -> None:
    """A gate is a human accepting terms, not a technical obstacle.

    `nuriachandra/Deepfake-Eval-2024` requires an institutional email, an institution,
    and attested prior work in the field. Fetching around that would put terms someone
    else agreed to into this repository.
    """
    for gated in ("manual", "auto-approval", "external"):
        with pytest.raises(ff.FixtureRefused, match="gated"):
            ff.check_gated(gated)

    # The ungated cases must still pass, or the check is refusing everything and
    # saying nothing.
    ff.check_gated(False)
    ff.check_gated("auto")


def test_an_unlisted_licence_is_refused_even_though_it_is_not_nd_or_sa() -> None:
    """The allowlist is closed, so `other` and `unknown` do not slip through."""
    for licence in ("other", "unknown", "apache-2.0", "mit", "by-nc-sa-4.0"):
        with pytest.raises(ff.FixtureRefused, match="not on the permitted list"):
            ff.Fixture(url="u", sha256="a" * 64, licence=licence, role="f/p").check_licence()


def test_the_refusal_explains_what_is_permitted() -> None:
    """An operator hitting this needs the next step, not just a rejection."""
    with pytest.raises(ff.FixtureRefused) as caught:
        ff.Fixture(url="u", sha256="a" * 64, licence="other", role="f/p").check_licence()
    assert "cc-by-4.0" in str(caught.value)


# --- what must be permitted -----------------------------------------------


@pytest.mark.parametrize("licence", ["cc0-1.0", "cc-by-4.0", "CC-BY-4.0", "publicdomain"])
def test_permissive_licences_are_permitted(licence: str) -> None:
    """Case-insensitively, because a licence tag's case is not meaningful."""
    ff.Fixture(url="u", sha256="a" * 64, licence=licence, role="f/p").check_licence()


def test_a_mismatched_digest_writes_nothing(tmp_path: pathlib.Path) -> None:
    """The pin is the claim; a mismatch must abort before the file exists.

    A fixture whose bytes do not match its recorded digest makes every test that uses
    it meaningless while looking perfectly healthy.
    """
    served = b"the bytes the server actually sent"

    class _Response:
        def read(self) -> bytes:
            return served

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    def fake_urlopen(url: str, timeout: int) -> _Response:
        del url, timeout
        return _Response()

    destination = tmp_path / "fixture.bin"
    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = fake_urlopen  # type: ignore[assignment]
    try:
        with pytest.raises(ff.FixtureRefused, match="hashes to"):
            ff.download(
                "https://example.org/x",
                destination,
                expected_sha256="b" * 64,
            )
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]

    assert not destination.exists(), "a file was written despite the digest mismatch"


def test_a_matching_digest_is_written_and_recorded(tmp_path: pathlib.Path) -> None:
    """The happy path, so the refusals above are not the only thing exercised."""
    payload = b"the fixture bytes"
    digest = hashlib.sha256(payload).hexdigest()

    class _Response:
        def read(self) -> bytes:
            return payload

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = lambda url, timeout: _Response()  # type: ignore[assignment]
    try:
        observed = ff.download(
            "https://example.org/x",
            tmp_path / "out.bin",
            expected_sha256=digest,
        )
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]

    assert observed == digest
    assert (tmp_path / "out.bin").read_bytes() == payload


def test_gating_happens_before_any_network_call(tmp_path: pathlib.Path) -> None:
    """A refused source must not be fetched even once.

    Otherwise the gate is advisory: it would have already pulled bytes from a source
    whose terms say it should not have.
    """

    def explode(url: str, timeout: int) -> object:
        raise AssertionError(f"a refused source was fetched: {url}")

    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = explode  # type: ignore[assignment]
    try:
        with pytest.raises(ff.FixtureRefused):
            ff.fetch_all(tmp_path, [PERMITTED], gated="manual")
        with pytest.raises(ff.FixtureRefused):
            ff.fetch_all(
                tmp_path,
                [ff.Fixture(url="u", sha256="a" * 64, licence="unstated", role="f/p")],
                gated=False,
            )
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]


def test_an_ungated_permitted_fixture_is_pinned_and_manifested(tmp_path: pathlib.Path) -> None:
    """End to end: gate, fetch, verify, write a manifest recording the provenance."""
    payload = b"real photographic bytes"
    digest = hashlib.sha256(payload).hexdigest()

    class _Response:
        def read(self) -> bytes:
            return payload

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = lambda url, timeout: _Response()  # type: ignore[assignment]
    try:
        manifest = ff.fetch_all(
            tmp_path,
            [
                ff.Fixture(
                    url="https://example.org/real.jpg",
                    sha256=digest,
                    licence="cc-by-4.0",
                    role="deepfake/real",
                    note="a real capture",
                )
            ],
            gated=False,
        )
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]

    assert manifest["count"] == 1
    entry = manifest["fixtures"][0]
    # The provenance has to be in the artefact, not only in the git history: a pin
    # that is not readable at the point of use is a claim rather than a check.
    assert entry["licence"] == "cc-by-4.0"
    assert entry["role"] == "deepfake/real"
    assert entry["note"] == "a real capture"
    assert entry["sha256"] == digest

    on_disk = json.loads((tmp_path / ff.MANIFEST_NAME).read_text())
    assert on_disk["fixtures"][0]["url"] == "https://example.org/real.jpg"


def test_a_present_fixture_with_the_wrong_bytes_is_re_fetched(tmp_path: pathlib.Path) -> None:
    """A file on disk that does not match its pin is not trusted.

    `skip_existing` must mean "skip if it *verifies*", not "skip if a file of that
    name exists" — otherwise a corrupted checkout silently becomes the fixture every
    golden test then measures against.
    """
    good = b"the fixture bytes"
    digest = hashlib.sha256(good).hexdigest()
    fixture = ff.Fixture(
        url="https://example.org/x", sha256=digest, licence="cc-by-4.0", role="f/p"
    )
    destination = tmp_path / f"{fixture.role.replace('/', '-')}-{digest[:16]}"
    destination.write_bytes(b"corrupted by something")

    class _Response:
        def read(self) -> bytes:
            return good

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = lambda url, timeout: _Response()  # type: ignore[assignment]
    try:
        manifest = ff.fetch_all(tmp_path, [fixture], gated=False)
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]

    assert manifest["fixtures"][0]["status"] == "fetched"
    assert destination.read_bytes() == good


def test_an_unreachable_source_is_refused_with_the_url(tmp_path: pathlib.Path) -> None:
    """A 404 must not look like a licence problem or an empty result."""
    destination = tmp_path / "out.bin"

    def boom(url: str, timeout: int) -> object:
        raise urllib.error.URLError("404 Not Found")

    original = ff.urllib.request.urlopen
    ff.urllib.request.urlopen = boom  # type: ignore[assignment]
    try:
        with pytest.raises(ff.FixtureRefused, match="could not fetch"):
            ff.download("https://example.org/gone", destination, expected_sha256="a" * 64)
    finally:
        ff.urllib.request.urlopen = original  # type: ignore[assignment]

    assert not destination.exists()
