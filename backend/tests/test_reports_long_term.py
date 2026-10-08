"""PAdES long-term validation: a real timestamp authority, a real countersignature.

**No mocks anywhere in this file.** A timestamp authority is the kind of component that
either works or produces a plausible-looking signature that establishes nothing, and a
mocked TSA cannot tell the difference. So these tests run the actual `openssl ts`
authority in `scripts/rfc3161_tsa.py`, point pyHanko at it over HTTP, sign a real PDF, and
verify the resulting bytes.

That is affordable because the authority is OpenSSL's, not ours: `provision_tsa.py`
creates a self-signed `timeStamping` certificate and `rfc3161_tsa.py` forwards DER to
`openssl ts -reply`. The cryptography is production-grade; only the certificate is ours.

**What these tests cannot establish**, and why it is stated here rather than in a comment
nobody reads: this authority's certificate is self-signed, so every timestamp in this file
is worth exactly what that certificate is worth. A report signed this way establishes its
signing time *to a lab that trusts this key* and to nobody else. Testing against a
certified public TSA is not something this environment can do, and a test that implied it
would be the over-confident analyzer payload this project keeps refusing to ship.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import socket
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from app.config import Settings
from app.reports import signing
from app.reports.builder import SignerInfo
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.fields import SigSeedSubFilter

# --- a TSA, for real ------------------------------------------------------

_TSA_SERVER: ThreadingHTTPServer | None = None
_TSA_THREAD: threading.Thread | None = None
_TSA_URL = ""
_TSA_CERT: Path | None = None
_TSA_CONFIG: Path | None = None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="session")
def tsa(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """A running RFC 3161 authority, and the certificate to trust it with.

    Session-scoped: provisioning an authority and starting a thread is slow, and nothing
    here mutates it. Each test signs its own PDF against it, which is the part that must
    be independent.
    """
    global _TSA_SERVER, _TSA_THREAD, _TSA_URL, _TSA_CERT, _TSA_CONFIG

    workdir = tmp_path_factory.mktemp("tsa")
    # Imported here rather than at module scope: `scripts` is not a package on the path
    # the backend imports from, and a sys.path edit at import time would leak into every
    # other test in the session.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import provision_tsa
        import rfc3161_tsa
    finally:
        sys.path.pop(0)

    paths = provision_tsa.generate(workdir, days=3650, subject_cn="ForenSight Test TSA")
    _TSA_CERT = paths["cert"]
    _TSA_CONFIG = paths["config"]

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), rfc3161_tsa.Handler)
    server.config = paths["config"]  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    _TSA_SERVER = server
    _TSA_THREAD = thread
    _TSA_URL = f"http://127.0.0.1:{port}/timestamp"

    # Wait for the socket to accept before handing the URL to a test, rather than
    # sleeping a guessed interval: a slow machine and a fast one both work, and a
    # flake-prone `sleep(0.2)` is replaced by something that measures the actual thing.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with (
            contextlib.suppress(OSError),
            socket.create_connection(("127.0.0.1", port), timeout=0.5),
        ):
            break
        time.sleep(0.02)
    else:  # pragma: no cover - only on a badly broken machine
        server.shutdown()
        pytest.fail("the timestamp authority never started listening")

    try:
        yield _TSA_URL
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _tsa_settings(url: str) -> Settings:
    """Settings with a timestamp authority and nothing else changed.

    The authority's certificate is supplied because signing validates the countersignature
    before embedding it, and a self-signed TSA has no path to anything else. That makes
    `report_timestamp_ca` a requirement rather than a convenience, which is why the runbook
    says so.
    """
    return Settings(
        report_timestamp_url=url,
        report_timestamp_timeout=20.0,
        report_timestamp_ca=str(_TSA_CERT) if _TSA_CERT else "",
        secret_key="t" * 32,
    )


# --- fixtures -------------------------------------------------------------


@pytest.fixture(scope="session")
def signer_key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A PKCS#12 report-signing key, created the way a lab's would be."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    try:
        import provision_signer
    finally:
        sys.path.pop(0)

    workdir = tmp_path_factory.mktemp("signer")
    path = workdir / "signer.p12"
    provision_signer.generate(
        path,
        "test-passphrase-not-real",
        common_name="ForenSight Test Lab",
        organisation="ForenSight",
        days=3650,
    )
    return path


def _minimal_pdf() -> bytes:
    """A one-page PDF with real content, so signing has something to sign."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen.canvas import Canvas

    out = io.BytesIO()
    #  so the text is a literal in the bytes. A tamper test has to be
    # able to find the string it is replacing, and a compressed stream does not contain
    # it.
    canvas = Canvas(out, pagesize=A4, pageCompression=0)
    canvas.drawString(72, 780, "ForenSight case report")
    canvas.drawString(72, 760, "Integrity test document")
    canvas.showPage()
    canvas.save()
    return out.getvalue()


async def _sign(pdf: bytes, signer: signers.PdfSigner) -> bytes:
    out = io.BytesIO()
    await signer.async_sign_pdf(IncrementalPdfFileWriter(io.BytesIO(pdf)), output=out)
    return out.getvalue()


def _load_signer(settings: Settings, key: Path) -> signers.PdfSigner:
    stamps = signing.timestamper_for(settings)
    bare = signing.load_signer(key, "test-passphrase-not-real")
    if stamps is None:
        return bare
    return signing.load_signer(
        key,
        "test-passphrase-not-real",
        timestamper=stamps,
        validation_context=signing.signing_validation_context(settings, bare),
    )


# --- the level is configured, then measured -------------------------------


def test_no_timestamp_authority_means_baseline_b(tsa: str) -> None:
    settings = _tsa_settings(tsa)
    settings.report_timestamp_url = ""
    assert signing.configured_level(settings) == signing.BASELINE_B
    assert signing.timestamper_for(settings) is None


def test_a_timestamp_authority_means_baseline_lt(tsa: str) -> None:
    settings = _tsa_settings(tsa)
    assert signing.configured_level(settings) == signing.BASELINE_LT
    assert signing.timestamper_for(settings) is not None


def test_an_unreachable_authority_is_a_refusal_not_a_quiet_fallback(tsa: str) -> None:
    """The dangerous failure is a report that looks long-term-validated and is not.

    So signing against an authority that is not there must raise. If it silently produced
    a baseline-B signature, the report would print BASELINE-LT on its face and a verifier
    would find no timestamp — the exact gap this whole mechanism exists to close.
    """
    settings = _tsa_settings("http://127.0.0.1:1/timestamp")
    settings.report_timestamp_timeout = 2.0
    bare = signing.load_signer(_key_path, "test-passphrase-not-real")
    signer = signing.load_signer(
        _key_path,
        "test-passphrase-not-real",
        timestamper=signing.timestamper_for(settings),
        validation_context=signing.signing_validation_context(settings, bare),
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 - the type is pyHanko's, not ours
        asyncio.run(_sign(_minimal_pdf(), signer))


# Assigned by the fixture below; module scope keeps the type checker happy without a cast.
_key_path: Path


@pytest.fixture(autouse=True)
def _bind_key(signer_key: Path) -> None:
    global _key_path
    _key_path = signer_key


# --- end to end -----------------------------------------------------------


def test_a_timestamped_report_carries_a_countersignature_and_says_so(
    tsa: str, signer_key: Path
) -> None:
    settings = _tsa_settings(tsa)
    signer = _load_signer(settings, signer_key)
    signed = asyncio.run(_sign(_minimal_pdf(), signer))

    # The countersignature is present, and it is a *document* timestamp rather than the
    # signing machine's own claim.
    asserted, authority = signing.document_timestamp(signed)
    assert asserted is not None, "no document timestamp was embedded"
    assert authority is not None and "ForenSight Test TSA" in authority

    # **Two signature fields, not one.** A PDF document timestamp is a signature field in
    # its own right (`/Type /DocTimeStamp`, `/SubFilter /ETSI.RFC3161`), which is how PDF
    # has always modelled one; it is not an attribute of the signature. The first version
    # of this test asserted a single field and read `field.timestamp`, which does not
    # exist — and would have been the kind of assertion that passes for the wrong reason.
    embedded = PdfFileReader(io.BytesIO(signed)).embedded_signatures
    assert len(embedded) == 2, f"expected a signature and a timestamp, got {len(embedded)}"
    stamps = [f for f in embedded if signing._is_document_timestamp(f)]
    assert len(stamps) == 1, "the countersignature field was not recognised as a timestamp"
    assert stamps[0].self_reported_timestamp is not None

    # The signature field itself is a PAdES signature, not a timestamp — checked so the
    # discriminator above cannot be passing because everything matched.
    assert not signing._is_document_timestamp(embedded[0])

    # The catalog, not the signature, is where the Document Security Store lives —
    # measured against a real report, whose catalog keys included `/DSS` while its
    # signature dictionary held only `/ByteRange /Contents /Filter /Location /M /Reason
    # /SubFilter /Type`. Asserted exactly rather than as "LT or T": the earlier version of
    # this check accepted either, and would have passed while the level was being
    # under-reported.
    catalog = PdfFileReader(io.BytesIO(signed)).root
    assert "/DSS" in catalog, "the validation material is not in the document catalog"
    assert (
        signing._measured_level(embedded, timestamped=asserted is not None, catalog=catalog)
        == signing.BASELINE_LT
    )


def test_an_untimestamped_report_measures_as_baseline_b(signer_key: Path) -> None:
    settings = _tsa_settings("")
    settings.report_signing_p12 = str(signer_key)
    signer = signing.load_signer(signer_key, "test-passphrase-not-real")
    signed = asyncio.run(_sign(_minimal_pdf(), signer))

    assert signing.document_timestamp(signed) == (None, None)
    embedded = PdfFileReader(io.BytesIO(signed)).embedded_signatures
    assert signing._measured_level(embedded, timestamped=False) == signing.BASELINE_B


def test_verification_reports_the_timestamp_separately_from_trust(
    tsa: str, signer_key: Path
) -> None:
    """`timestamped` and `trusted` are different questions and stay different.

    A self-signed TSA certificate means the timestamp is present but not attributable to
    anyone the verifier has been told to trust. Reporting `trusted: true` here would be a
    lie, and reporting `timestamped: false` would throw away the one fact worth knowing.
    """
    settings = _tsa_settings(tsa)
    signer = _load_signer(settings, signer_key)
    signed = asyncio.run(_sign(_minimal_pdf(), signer))

    # No trust roots: the signature is intact but the signer is unconfirmed.
    result = asyncio.run(signing.verify_pdf(signed, trust_roots=None))
    assert result.intact is True
    assert result.trusted is False
    assert result.timestamped is True
    assert result.timestamp_time is not None
    assert result.tsa_subject is not None
    assert result.level == signing.BASELINE_LT
    assert any("no trust roots" in problem for problem in result.problems)
    # And critically: the baseline-B wording about having no timestamp is gone.
    assert not any("no timestamp authority" in problem for problem in result.problems)


def test_supplying_the_tsa_certificate_is_not_enough_to_trust_the_report(
    tsa: str, signer_key: Path, tmp_path: Path
) -> None:
    """Trusting the TSA does not vouch for the signer, and must not appear to.

    The two chains are separate. A verifier who trusts the timestamp authority and not the
    report's signing certificate has established *when* and not *who*.
    """
    settings = _tsa_settings(tsa)
    signer = _load_signer(settings, signer_key)
    signed = asyncio.run(_sign(_minimal_pdf(), signer))

    assert _TSA_CERT is not None
    result = asyncio.run(signing.verify_pdf(signed, trust_roots=[_TSA_CERT.read_bytes()]))
    assert result.timestamped is True
    # The report's own certificate is the one that is untrusted here, so the verdict must
    # say so rather than pass on the strength of the TSA.
    assert result.trusted is False
    assert result.verdict.startswith("UNVERIFIED SIGNER")


def test_a_tampered_timestamped_report_still_fails(signer_key: Path, tsa: str) -> None:
    """A countersignature must not make the document tamper-proof on its own.

    The digest covers the content; changing a byte in the content has to break it even
    when a valid timestamp rides along. Adding a timestamp changes what is *proven*, not
    whether alteration is detected.
    """
    settings = _tsa_settings(tsa)
    signer = _load_signer(settings, signer_key)
    signed = asyncio.run(_sign(_minimal_pdf(), signer))

    body = signed.replace(b"Integrity test document", b"Integrity test documenX")
    assert body != signed, "the tamper did not change any bytes"
    result = asyncio.run(signing.verify_pdf(body, trust_roots=None))
    assert result.intact is False


# --- configuration edge cases --------------------------------------------


def test_a_revocation_file_that_is_not_there_is_a_refusal(signer_key: Path) -> None:
    """A CRL path that does not resolve must not be silently skipped.

    Silently ignoring it would produce a document claiming long-term validation with no
    revocation material in it — which is the failure this feature exists to prevent, and
    it would be invisible on the report's face.
    """
    settings = _tsa_settings("http://tsa.invalid/timestamp")
    settings.report_revocation_files = "/nonexistent/revocation.crl"
    bare = signing.load_signer(signer_key, "test-passphrase-not-real")
    with pytest.raises(signing.SigningUnavailable, match="do not exist"):
        signing.signing_validation_context(settings, bare)


def test_a_trust_root_bundle_that_is_not_there_is_a_refusal(signer_key: Path) -> None:
    settings = _tsa_settings("http://tsa.invalid/timestamp")
    settings.report_signing_trust_roots = "/nonexistent/roots.pem"
    bare = signing.load_signer(signer_key, "test-passphrase-not-real")
    with pytest.raises(signing.SigningUnavailable, match="not a file"):
        signing.signing_validation_context(settings, bare)


def test_fetching_is_off_so_signing_cannot_reach_out(signer_key: Path) -> None:
    """The validation context must not fetch revocation data.

    Signing a report is a request on a case. Fetching from an OCSP responder would both
    stall the request and send a case identifier to a third party — and an air-gapped
    deployment would simply fail.
    """
    settings = _tsa_settings("http://tsa.invalid/timestamp")
    context = signing.signing_validation_context(
        settings, signing.load_signer(signer_key, "test-passphrase-not-real")
    )
    assert context.fetching_allowed is False


# --- the report's own statement ------------------------------------------


def _signature_text(pdf: bytes) -> str:
    return " ".join(signing.extract_text(pdf).split()) if hasattr(signing, "extract_text") else ""


def test_a_baseline_lt_report_prints_its_level_and_names_the_counter_signature(
    tsa: str,
) -> None:
    """The face of the document must state what it carries.

    A reader who never opens a verifier still has to be able to see that the signature is
    timestamped, and that the timestamp's own details live in the signature rather than
    having been invented for the page.
    """
    flat = _signature_face(
        SignerInfo(
            subject="CN=ForenSight Test Lab",
            fingerprint="a" * 64,
            level=signing.BASELINE_LT,
        )
    )

    assert "BASELINE-LT" in flat
    assert "Counter-signed" in flat
    assert "reports/verify" in flat, "the page must say where the timestamp details are"
    # And it must not still be claiming the signature cannot establish *when*.
    assert "no timestamp authority counter-signed it" not in flat


def test_a_baseline_b_report_says_plainly_that_it_has_no_timestamp_and_an_expiry() -> None:
    from app.reports.builder import _signature_block, _styles
    from reportlab.platypus import Paragraph

    styles = _styles()
    info = SignerInfo(subject="CN=Lab", fingerprint="b" * 64)
    story = _signature_block(_report_data(), info, styles)
    flat = " ".join(" ".join(p.getPlainText().split()) for p in story if isinstance(p, Paragraph))
    flat = " ".join(flat.split())

    assert "BASELINE-B" in flat
    assert "It also has an expiry" in flat
    assert "once the signing certificate lapses" in flat
    assert "Counter-signed" not in flat


def _signature_face(info: SignerInfo) -> str:
    """Every word the signature block puts on the page, flattened.

    Reads tables as well as paragraphs. The counter-signature row is a table row, so a
    paragraph-only extraction silently misses exactly the text these tests are about —
    which is the first version of this helper's behaviour, and it failed.
    """
    from app.reports.builder import _signature_block, _styles
    from reportlab.platypus import Paragraph, Table

    def cell_text(flowable: Any) -> Iterator[str]:
        if isinstance(flowable, Paragraph):
            yield flowable.getPlainText()
        elif isinstance(flowable, Table):
            for row in flowable._cellvalues:  # noqa: SLF001 - the only way to read a Table
                for cell in row:
                    yield from cell_text(cell)

    parts: list[str] = []
    for flowable in _signature_block(_report_data(), info, _styles()):
        parts.extend(cell_text(flowable))
    return " ".join(" ".join(parts).split())


def _report_data() -> Any:
    """The smallest `ReportData` the signature block will render.

    Built by introspection so this test does not become a second, stale copy of
    `ReportData`'s shape — which is the bug class that has bitten this project six times
    already, where a hand-written duplicate of a dataclass silently stops matching.
    """
    import dataclasses

    from app.reports import builder as builder_module

    required = {f.name for f in dataclasses.fields(builder_module.ReportData)}
    kwargs: dict[str, Any] = {}
    for name in required:
        kwargs[name] = None
    # Only these three are read by the signature block; the rest merely have to exist.
    kwargs["manifest_digest"] = "c" * 64
    for name in required - {"manifest_digest"}:
        annotation = next(
            (f.type for f in dataclasses.fields(builder_module.ReportData) if f.name == name),
            "",
        )
        # A list-shaped field must be a list: the cover counts them, and None is not.
        if isinstance(annotation, str) and annotation.startswith(("list", "Sequence", "tuple")):
            kwargs[name] = []
    return builder_module.ReportData(**kwargs)


# --- pyHanko contract we rely on ----------------------------------------


def test_the_timestamper_is_only_a_timestamper_when_both_halves_are_present(
    tsa: str, signer_key: Path
) -> None:
    """`use_pades_lta` must not be claimed with only one half.

    pyHanko will sign a signature labelled long-term that is missing its timestamp or its
    validation material. Both halves are therefore set from configuration together, and
    this asserts the metadata reflects that rather than the deployment's wish.
    """
    settings = _tsa_settings(tsa)
    signer = _load_signer(settings, signer_key)
    meta = signer.signature_meta
    assert meta.embed_validation_info is True
    assert meta.use_pades_lta is True
    assert meta.timestamp_field_name == signing.TIMESTAMP_FIELD

    bare = signing.load_signer(signer_key, "test-passphrase-not-real")
    assert bare.signature_meta.embed_validation_info is False
    assert bare.signature_meta.use_pades_lta is False
    assert bare.signature_meta.timestamp_field_name is None


def test_the_signature_field_stays_the_same_name_at_every_level(signer_key: Path) -> None:
    """A verifier finds the signature by field name, so changing it breaks every reader."""
    meta = signing.load_signer(signer_key, "test-passphrase-not-real").signature_meta
    assert meta.field_name == signing.SIGNATURE_FIELD
    assert meta.subfilter == SigSeedSubFilter.PADES
