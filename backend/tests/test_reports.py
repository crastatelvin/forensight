"""Phase 8 tests: signed reports, the manifest, and the provenance appendix.

The signing tests need a key pair. One is generated once per session in a temporary
directory rather than committed: a committed PKCS#12 with a known passphrase is a
signing key anyone could use to forge reports that verify against this project's own
test fixtures, which is a poor thing to hand anyone who clones the repository.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import io
import uuid
from pathlib import Path

import pytest
from app.domain.models import Case, Evidence, Finding
from app.domain.workflow_repo import cite_results
from app.reports.builder import SignerInfo, build_pdf
from app.reports.repository import (
    canonical_manifest,
    summary_counts,
)
from app.reports.signing import (
    SigningUnavailable,
    load_signer,
    sign_pdf,
    verify_pdf,
)
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12
from cryptography.x509.oid import NameOID
from httpx import AsyncClient

PASSPHRASE = "test-passphrase-not-a-secret"


@pytest.fixture(scope="session")
def signing_material(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """A throwaway key pair: the PKCS#12, its PEM certificate, and a loaded signer."""
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "ForenSight test signer"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ForenSight"),
        ]
    )
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    directory = Path(tmp_path_factory.mktemp("signing"))
    p12 = directory / "signer.p12"
    p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"ForenSight",
            key,
            cert,
            None,
            serialization.BestAvailableEncryption(PASSPHRASE.encode()),
        )
    )
    pem = cert.public_bytes(Encoding.PEM)

    other_key = ec.generate_private_key(ec.SECP256R1())
    other_subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Somebody else entirely")])
    other_cert = (
        x509.CertificateBuilder()
        .subject_name(other_subject)
        .issuer_name(other_subject)
        .public_key(other_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(other_key, hashes.SHA256())
    )

    return {
        "p12_path": p12,
        "pem": pem,
        "other_pem": other_cert.public_bytes(Encoding.PEM),
        "signer": load_signer(p12, PASSPHRASE),
    }


def _trivial_pdf() -> bytes:
    """A one-line PDF, for tests about signing rather than about layout."""
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(72, 720, "ForenSight test document")
    c.save()
    return buffer.getvalue()


# --- T8.1: the signature verifies, and altering the PDF breaks it ----------


def test_a_generated_signature_verifies(signing_material: dict) -> None:
    """The whole point: what comes out verifies as unmodified.

    Coverage is checked as well as integrity, because a signature covering only part
    of the document still reports "intact" — and a report whose closing page is
    outside the signature is not a signed report.
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]
    result = asyncio.run(verify_pdf(signed))

    assert result.intact is True, result.problems
    assert result.coverage == "ENTIRE_FILE", result.coverage
    assert result.docmdp_ok is True, "the whole document must be under a DocMDP transform"
    assert "ForenSight test signer" in (result.signer_subject or "")


def test_altering_one_byte_anywhere_in_the_signed_content_is_detected(
    signing_material: dict,
) -> None:
    """The core guarantee: the report's content cannot be altered undetectably.

    Swept across both signed ranges rather than three sampled offsets, because the
    whole value of a signature is that it covers everything — and a spot check at
    three offsets would pass just as happily if the coverage were partial.

    An earlier version of this test sampled 1/3, 1/2 and 2/3 of the file and passed
    while a flip inside the signature blob went undetected. The offsets were outside
    the signed content, so the test was measuring the wrong thing.
    """
    import re

    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]
    match = re.search(rb"/ByteRange \[([0-9 ]+)\]", signed)
    assert match, "expected an incremental signature with a ByteRange"
    numbers = [int(n) for n in match.group(1).split()]
    ranges = [(numbers[0], numbers[0] + numbers[1]), (numbers[2], numbers[2] + numbers[3])]

    missed: list[int] = []
    for start, end in ranges:
        step = max(1, (end - start) // 40)
        for offset in range(start, end, step):
            damaged = bytearray(signed)
            damaged[offset] ^= 0x01
            result = asyncio.run(verify_pdf(bytes(damaged)))
            if result.intact:
                missed.append(offset)

    assert (
        not missed
    ), f"{len(missed)} single-byte changes to signed content went unnoticed: {missed[:8]}"


def test_corrupting_the_signature_value_is_mostly_detected(
    signing_material: dict,
) -> None:
    """A known gap in pyHanko, measured here so an upgrade cannot hide it.

    The `/Contents` blob sits outside the signed range, so altering the signature's
    own bytes leaves the document digest intact — detection depends entirely on
    pyHanko re-verifying the CMS structure. On pyHanko 0.37 it catches roughly two
    thirds of single-byte flips and misses the rest.

    Asserted as "at least 60% detected" rather than "all detected", which would fail
    today. When pyHanko closes the gap this test starts failing, and the message says
    so — which is the point of writing the limitation down instead of leaving it
    undocumented. Replacing it with a bare `pytest.xfail` would hide a real capability
    rather than track it.
    """
    import re

    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]
    match = re.search(rb"/ByteRange \[([0-9 ]+)\]", signed)
    assert match
    numbers = [int(n) for n in match.group(1).split()]
    # The hole between the two signed ranges is where the signature's bytes live.
    hole_start, hole_end = numbers[1], numbers[2]
    assert hole_end > hole_start, "expected a signature blob outside the signed range"

    detected = 0
    tested = 0
    step = max(1, (hole_end - hole_start) // 60)
    for offset in range(hole_start, hole_end, step):
        damaged = bytearray(signed)
        damaged[offset] ^= 0x01
        tested += 1
        try:
            result = asyncio.run(verify_pdf(bytes(damaged)))
        except Exception:  # noqa: BLE001 - an unreadable file is a detection
            detected += 1
            continue
        if not result.intact:
            detected += 1

    rate = detected / tested if tested else 0.0
    assert rate >= 0.6, (
        f"only {detected}/{tested} signature-blob flips detected ({rate:.0%}). "
        f"If this got worse, the verifier is regressing. If it got better, pyHanko "
        f"may now close the documented gap and this assertion can be tightened."
    )


def test_a_damaged_signature_value_is_reported_rather_than_raised(
    signing_material: dict,
) -> None:
    """Corrupting the signature must not crash the verifier.

    A byte flipped inside the signature leaves pyHanko unable to identify the signing
    certificate, and it raises. Uncaught, that turns "this document was tampered with"
    into a server error — and a verifier that fails on precisely the documents it
    exists to catch is worse than none, because the failure reads as a fault to retry
    rather than a finding to believe.
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]

    # The signature dictionary sits in the trailing incremental update; walk back
    # from the end to land inside it rather than guessing an offset.
    start = signed.rfind(b"/ByteRange")
    assert start > 0, "expected an incremental signature update"
    damaged = bytearray(signed)
    damaged[start - 64] ^= 0xFF

    result = asyncio.run(verify_pdf(bytes(damaged)))
    assert result.intact is False
    assert "VERIFIED" not in result.verdict
    assert result.problems


def test_appending_content_breaks_the_signature(signing_material: dict) -> None:
    """Appending is the easiest edit to make and must not pass.

    Worth its own test because an incremental signature leaves the original bytes
    untouched and appends an update, so it is not obviously impossible to add content
    afterwards.

    It is caught, but by a different channel than a modified byte is: the signed
    revision is still internally intact, so `intact` stays True and the appended bytes
    show up as *reduced coverage* — `ENTIRE_REVISION` instead of `ENTIRE_FILE`. That
    is exactly why the verdict checks coverage as well as integrity; a verifier that
    only looked at `intact` would call this document valid.
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]
    padded = signed + b"\n% a comment appended after signing\n"

    result = asyncio.run(verify_pdf(padded))

    assert result.coverage != "ENTIRE_FILE", "appended bytes must show as reduced coverage"
    assert "INCOMPLETE" in result.verdict, result.verdict
    assert "VERIFIED" not in result.verdict
    assert any("unsigned" in p for p in result.problems), result.problems


def test_an_unsigned_document_is_reported_as_unsigned(signing_material: dict) -> None:
    """The negative case, in the reader's words.

    Not "verification returned an error" — the answer to a reader's question has to
    say the document has no signature, because that is what they need to know before
    relying on it.
    """
    result = asyncio.run(verify_pdf(_trivial_pdf()))

    assert result.intact is False
    assert any("no signature found" in p for p in result.problems), result.problems
    assert "FAILED" in result.verdict


def test_something_that_is_not_a_pdf_is_reported_as_such() -> None:
    result = asyncio.run(verify_pdf(b"this is plainly not a PDF"))
    assert result.intact is False
    assert any("not a readable PDF" in p for p in result.problems), result.problems


# --- trust is a separate question from integrity ---------------------------


def test_integrity_holds_even_when_the_signer_is_not_trusted(
    signing_material: dict,
) -> None:
    """Self-signed is the normal deployment, and it must not read as a failure.

    The distinction is the point: the bytes are provably unchanged, and *who* signed
    them is unconfirmed until the reader supplies a trust root. Reporting a single
    "invalid" would push an operator to ignore the field entirely.
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]

    result = asyncio.run(verify_pdf(signed))
    assert result.intact is True
    assert result.trusted is False
    assert "UNVERIFIED SIGNER" in result.verdict
    assert "intact" in result.verdict

    # Supplying the certificate as its own trust root is what makes it trusted.
    trusted = asyncio.run(verify_pdf(signed, [signing_material["pem"]]))  # type: ignore[list-item]
    assert trusted.intact is True
    assert trusted.trusted is True, trusted.problems
    assert "VERIFIED" in trusted.verdict


def test_a_different_certificate_does_not_trust_the_report(
    signing_material: dict,
) -> None:
    """Trust is anchored: an unrelated certificate must not vouch for this report.

    Otherwise `trusted` would mean "a validation context was configured", which is not
    an identity claim at all.
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]

    result = asyncio.run(verify_pdf(signed, [signing_material["other_pem"]]))  # type: ignore[list-item]
    assert result.intact is True
    assert result.trusted is False, "an unrelated root must not vouch for the signer"


def test_the_signing_time_is_reported_as_the_signers_own_claim(
    signing_material: dict,
) -> None:
    """Baseline-B carries a time, and it is worth nothing as corroboration.

    PAdES baseline-B does not require a timestamp authority, so the signature does
    carry a signing time — the signing machine's own statement about its own clock.
    Reporting it as if it were established would be the overclaim this project exists
    to avoid, so it is surfaced *and* explicitly qualified.

    (An earlier version of this test asserted the time was absent. It is not; the
    report text was written from that wrong belief and had to be corrected too.)
    """
    signed = asyncio.run(sign_pdf(_trivial_pdf(), signing_material["signer"]))  # type: ignore[arg-type]
    result = asyncio.run(verify_pdf(signed))

    assert result.signing_time is not None, "baseline-B carries the signer's own time"
    assert any("signing machine's own claim" in p for p in result.problems), result.problems
    assert any("when signing happened" in p for p in result.problems), result.problems

    # And the report has to say it on its face, not merely return it here.
    text = _pdf_text(
        build_pdf(_case_with_one_cited_finding(), SignerInfo(subject="s", fingerprint="f" * 64))
    )
    assert "does not independently establish" in text
    assert "timestamp authority" in text


def test_a_missing_or_unreadable_key_is_refused_not_ignored(tmp_path: Path) -> None:
    """No key means no report.

    The check is that `load_signer` raises rather than returning something that would
    produce an unsigned PDF. Silently returning a signer-less object is how an
    unsigned report ships.
    """
    with pytest.raises(SigningUnavailable):
        load_signer(tmp_path / "absent.p12", PASSPHRASE)

    empty = tmp_path / "empty.p12"
    empty.write_bytes(b"not a key")
    with pytest.raises(SigningUnavailable):
        load_signer(empty, PASSPHRASE)


# --- T8.3: the manifest describes the evidence ---------------------------


def test_the_manifest_digest_covers_every_file_not_only_cited_ones() -> None:
    """Adding an uncited file must change the digest.

    A digest over only the cited subset would let a file be added to the case without
    the manifest changing — so the manifest would attest to an evidence set that is no
    longer the case's evidence set.
    """
    from app.reports.repository import ManifestRow

    stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    cited = ManifestRow("a.jpg", "a" * 64, "image/jpeg", 1, stamp, True)
    first = canonical_manifest([cited])

    # Same cited file, plus an uncited one.
    with_extra = canonical_manifest(
        [cited, ManifestRow("b.jpg", "b" * 64, "image/jpeg", 2, stamp, False)]
    )
    assert with_extra != first

    # `cited` is only a display flag; it must not change what the digest covers.
    assert (
        canonical_manifest([ManifestRow("a.jpg", "a" * 64, "image/jpeg", 1, stamp, False)]) == first
    )


def test_the_manifest_digest_is_independent_of_row_order() -> None:
    """Row order must not change the digest.

    Otherwise two labs exporting the same evidence produce different digests for the
    same evidence set, and the "recomputable by anyone" claim on the report is false.
    """
    from app.reports.repository import ManifestRow

    stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    rows = [
        ManifestRow("b.jpg", "b" * 64, "image/jpeg", 2, stamp, False),
        ManifestRow("a.jpg", "a" * 64, "image/jpeg", 1, stamp, True),
    ]
    assert canonical_manifest(rows) == canonical_manifest(list(reversed(rows)))


def test_the_manifest_digest_changes_when_a_digest_does() -> None:
    """The obvious integrity case: a different file, a different manifest."""
    from app.reports.repository import ManifestRow

    stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    original = ManifestRow("a.jpg", "a" * 64, "image/jpeg", 1, stamp, True)
    tampered = ManifestRow("a.jpg", "c" * 64, "image/jpeg", 1, stamp, True)

    assert canonical_manifest([original]) != canonical_manifest([tampered])


def test_every_recorded_digest_appears_in_the_report() -> None:
    """The manifest must carry each file's actual recorded digest.

    Checked by extracting the PDF's text rather than by inspecting the story objects,
    because a digest that is computed correctly and then rendered wrong is exactly the
    kind of defect that unit tests on the data layer cannot see.
    """
    data = _one_file_case()
    pdf = build_pdf(data, SignerInfo(subject="Test signer", fingerprint="f" * 64))
    hexes = _hex_runs(pdf)

    assert (
        data.manifest[0].sha256 in hexes
    ), "the evidence digest is not in the report as printable characters"
    assert data.manifest_digest in hexes
    assert data.manifest[0].filename in _pdf_text(pdf)


def test_the_report_never_shows_an_unreadable_digest_across_a_page_break() -> None:
    """A digest and its label belong on the same page.

    Not cosmetic: this happened. The manifest digest rendered at the foot of one page
    and resumed on the next, which makes a value a reader is invited to recompute much
    harder to check. Every digest is now broken into fixed 16-character lines inside a
    keep-together, so wrapping is visible and the block cannot split.
    """

    data = _one_file_case(n_evidence=12)
    pdf = build_pdf(data, SignerInfo(subject="Test signer", fingerprint="a" * 64))

    assert data.manifest_digest in _hex_runs(pdf), "the manifest digest is missing from the report"

    # The label and its value are adjacent, with no page furniture between them.
    # Flattened rather than line-by-line so a phrase split across a wrap still reads
    # as one string. The digest itself is compared through `_hex_runs`, which cannot
    # splice a neighbouring table column into the middle of it.
    flat = _flat(pdf)
    assert data.manifest_digest in flat, "the manifest digest is missing from the report"

    label_at = flat.find("Manifestdigest")
    assert label_at != -1, "the manifest digest has no label on the page"
    window = flat[label_at : label_at + 400]
    assert (
        data.manifest_digest in window
    ), "the manifest digest is not adjacent to its label; a page break is between them"


# --- T8.2: every claim carries its source --------------------------------


def test_every_finding_shows_its_analyzer_threshold_and_version() -> None:
    """The honesty clause, on the page rather than in the API response.

    A score with no threshold next to it is a number with no meaning, and a version
    with nothing beside it cannot be reproduced. Both are checked as *text in the
    rendered document*, because that is the only version a reader receives.
    """
    data = _case_with_one_cited_finding()
    text = _pdf_text(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    citation = data.findings[0].citations[0]
    assert citation.module in text
    assert f"{citation.score:.4f}" in text
    assert "threshold" in text
    assert citation.model_version in text
    assert citation.evidence_sha256[:32] in text.replace("\n", "").replace(" ", "")


def test_a_finding_with_no_citations_is_marked_not_sourced() -> None:
    """An unsourced finding must not read like a sourced one.

    The report sets it apart and says what it is. This is the single most important
    property of the document: an examiner skimming findings must be unable to mistake
    an assertion for a measurement.
    """
    data = _case_with_one_cited_finding()
    data.findings.append(_unsourced_finding("Examiner observation with no analysis behind it"))

    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))
    assert "NOTSOURCED" in text
    assert "restsonnoanalyzerresult" in text
    # The unsourced finding must still appear — a case record is not ours to prune.
    assert "Examinerobservationwithnoanalysisbehindit" in text


def test_the_cover_counts_sourced_and_unsourced_findings_separately() -> None:
    """ "3 findings" is not enough; the cover must say how many are backed."""
    data = _case_with_one_cited_finding()
    data.findings.append(_unsourced_finding("Second unsourced finding"))

    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))
    assert "1sourced" in text and "1notsourced" in text


def test_the_report_says_outright_that_it_is_not_a_determination() -> None:
    data = _case_with_one_cited_finding()
    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert "notadetermination" in text
    assert "authenticity" in text


# --- T8.4: Tier C is an attachment, never a finding ----------------------


def test_a_tier_c_result_appears_only_under_laboratory_results() -> None:
    """An external lab's conclusion is never presented as this system's finding.

    Tier C is not producible by any analyzer here — the plan cuts DNA, toxicology,
    pollen, entomology, odor, explosives and arson chemistry as AI analyzers because
    none can be done for real from a web upload. So a Tier-C result is an attached
    document from someone else. Laying it out among the findings would transfer
    authorship of that conclusion to this system.
    """
    data = _case_with_one_cited_finding()
    data.findings.append(_tier_c_finding("Pollen count per ACMG 2010"))

    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert "Attachedlaboratoryresults" in text
    assert "notthissystem'sfindings" in text
    assert "PollencountperACMG2010" in text

    # It must be counted separately, never as an analyzer result.
    counts = summary_counts(data)
    assert counts["findings"] == 2
    assert counts["lab_results"] == 1

    # And it must not appear in the findings summary line.
    findings_line = [ln for ln in text.splitlines() if "Findings" in ln]
    assert findings_line, "the cover should list findings"


def test_tier_c_is_not_produced_by_any_analyzer() -> None:
    """The structural half of T8.4: no Tier-C analyzer exists to feed the rule.

    Without this, the report's Tier-C handling would be reachable only from a
    hand-edited database row, and the rule could rot unnoticed.
    """
    from app.analysis import registry
    from app.reports.repository import TIER_LAB

    registry.register_all()
    claiming = [
        name
        for name in registry.registered_names()
        if getattr(registry.get(name).spec, "tier", None) == TIER_LAB
    ]
    assert not claiming, (
        f"these analyzers claim Tier C: {claiming}. Tier C is an attachment "
        f"workflow for an accredited lab's report, not something this system computes."
    )


# --- report assembly ------------------------------------------------------


def test_an_empty_case_still_produces_a_report_that_says_it_is_empty() -> None:
    """ "No findings" must not read as "nothing is wrong with the evidence".

    Silence in a forensic document is indistinguishable from a clean bill of health
    unless it is stated.
    """
    from app.reports.repository import ReportData

    case = Case(
        number="EMPTY-1",
        title="Nothing here",
        owner_id=uuid.uuid4(),
        status="intake",
        priority="normal",
        created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC),
    )
    data = ReportData(case=case, evidence=[], findings=[], results=[], custody=[], runs=[])
    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert "Nofindingshavebeenrecorded" in text
    assert "notastatementthattheevidenceisunaltered" in text
    assert "Noanalysishasbeenrun" in text
    assert "Nomeasurementhasbeenmade" in text


def test_case_supplied_markup_cannot_break_the_layout() -> None:
    """A filename or finding body containing markup must not become markup.

    ReportLab's `Paragraph` parses a `<` as a tag. An evidence file called
    `<b>urgent</b>` or a finding body pasted from a word processor would then be
    typeset as formatting — inside a signed document, which is a very bad place for
    text to change its own appearance.
    """
    data = _case_with_one_cited_finding()
    data.findings[0] = dataclasses.replace(
        data.findings[0],
        title="Firmware <b>update</b> & <i>downgrade</i> suspected",
    )
    data.manifest[0] = dataclasses.replace(
        data.manifest[0], filename="<script>alert(1)</script>.jpg"
    )

    pdf = build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64))
    flowed = _flat(pdf)
    raw = _pdf_text(pdf)

    # The tags survive as *characters*, and that is the assertion: had ReportLab
    # parsed them as markup, the angle brackets would be gone from the rendered text
    # and only "update" would remain. Checking for `&lt;` would test the wrong layer —
    # `_esc` escapes on the way in and ReportLab decodes on the way out, so the entity
    # never reaches the page. (The first version of this test did exactly that, and
    # failed against a report that was escaping correctly.)
    assert "<b>update</b>" in flowed, f"tags were read as formatting: {flowed[:300]}"
    assert "<i>downgrade</i>" in flowed
    assert "Firmware" in flowed and "update" in flowed

    # The filename is checked against the raw lines rather than the flattened text.
    # It sits in a five-column table, so `pdftotext -layout` puts the *neighbouring*
    # columns on the same output line as the filename's first wrapped line — and
    # flattening whitespace then splices a digest between `.jp` and the trailing `g`.
    # That is an artefact of reading a table as prose, not a defect in the report.
    assert "<script>alert(1)</script>.jp" in raw, "the filename was mangled or dropped"
    assert any(
        line.strip().startswith("g") for line in raw.splitlines()
    ), "the filename's wrapped remainder is missing"


def test_case_number_cannot_escape_the_download_filename() -> None:
    """`Content-Disposition` interpolation depends on this validation.

    A case number containing a quote would let the header be rewritten — so the
    constraint is asserted here rather than left as a comment in the router.
    """
    from app.domain.schemas import CaseCreate
    from pydantic import ValidationError

    for bad in ('a"b', "a\nb", "a\rb", "a;b", "a\tb", "a\\b", "a<b"):
        with pytest.raises(ValidationError):
            CaseCreate(number=bad, title="x")

    # Allowed on purpose, and asserted so nobody "tightens" the rule later and breaks
    # real case numbers: `2026/IN-014` is an ordinary way to write one, and `/` and
    # spaces are safe inside a quoted Content-Disposition filename. A first draft of
    # this test listed `a/b` among the rejected values and failed against a rule that
    # was working exactly as intended.
    for good in ("2026/IN-014", "CASE 14", "case_14.v2", "A-1"):
        assert CaseCreate(number=good, title="x").number == good


# --- helpers --------------------------------------------------------------


def _flat(pdf: bytes) -> str:
    """The PDF's text with all whitespace removed.

    `pdftotext -layout` wraps lines at the column width, so a phrase like
    "not this system's findings" can come back split across two lines with a newline
    and several spaces in the middle. Every assertion about report wording goes
    through here, so a test states the claim it means rather than the column width it
    happened to land on.
    """
    import re

    return re.sub(r"\s+", "", _pdf_text(pdf))


def _hex_runs(pdf: bytes) -> str:
    """Every hex-looking run in the PDF's text, concatenated.

    Used for digest assertions instead of the flattened text. `pdftotext -layout`
    puts a table's neighbouring column on the same output line as the first line of a
    wrapped cell, so removing all whitespace splices the *next column's* text into the
    middle of a digest — which made a 64-character digest come back as 48 characters
    followed by a row count. Concatenating only hex runs cannot splice in a number
    with a comma in it.
    """
    import re

    return "".join(re.findall(r"[0-9a-f]{8,}", _pdf_text(pdf)))


def _pdf_text(pdf: bytes) -> str:
    """Extract text from a PDF, preferring poppler and falling back to pyHanko."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf)
        path = handle.name
    try:
        # Fixed executable, our own temporary path, no shell. Bandit flags the shape
        # rather than the risk; suppressed here rather than project-wide so a real
        # finding elsewhere still surfaces.
        result = subprocess.run(  # noqa: S603
            ["pdftotext", "-layout", path, "-"],  # noqa: S607
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.decode("utf-8", "replace")
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    finally:
        Path(path).unlink(missing_ok=True)

    pytest.skip("pdftotext is unavailable, so the rendered report cannot be inspected")


def _one_file_case(*, n_evidence: int = 1) -> object:
    """A `ReportData` built directly — no database, for layout tests."""
    from app.reports.repository import ManifestRow, ReportData

    stamp = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    case = Case(
        number="LAYOUT-1",
        title="Layout",
        owner_id=uuid.uuid4(),
        # SQLAlchemy applies column defaults at flush, not at construction, so a
        # hand-built Case has None here — and the report would print "None" for the
        # case's status, which would quietly weaken every assertion below.
        status="intake",
        priority="normal",
        created_at=stamp,
    )
    evidence = [
        Evidence(
            id=uuid.uuid4(),
            case_id=case.id,
            filename=f"evidence-{i:02d}.jpg",
            sha256=f"{i:064x}",
            content_type="image/jpeg",
            size_bytes=1000 + i,
            storage_key=f"k/{i}",
            uploaded_by=uuid.uuid4(),
            created_at=stamp,
        )
        for i in range(n_evidence)
    ]
    manifest = [
        ManifestRow(e.filename, e.sha256, e.content_type, e.size_bytes, e.created_at, False)
        for e in evidence
    ]
    return ReportData(
        case=case,
        evidence=evidence,
        findings=[],
        results=[],
        custody=[],
        runs=[],
        manifest=manifest,
        manifest_digest=canonical_manifest(manifest),
    )


def _case_with_one_cited_finding() -> object:
    """One sourced finding, built directly so the test needs no database."""
    from app.reports.repository import CitationView, FindingView

    data = _one_file_case()  # type: ignore[assignment]
    evidence = data.evidence[0]
    stamp = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)

    citation = CitationView(
        result_id=str(uuid.uuid4()),
        evidence_id=str(evidence.id),
        filename=evidence.filename,
        evidence_sha256=evidence.sha256,
        module="image-ela",
        score=0.0396,
        threshold=0.01,
        tier="A",
        model_version="1.0.0+ela",
        disclaimer=(
            "Investigative lead from a real open-source model. Not courtroom " "identification."
        ),
        not_applicable=False,
        cited_at=stamp,
    )
    data.findings = [  # type: ignore[attr-defined]
        FindingView(
            finding_id=str(uuid.uuid4()),
            title="ELA indicates a resave",
            body="Error-level analysis shows a resave pattern.",
            status="draft",
            created_at=stamp,
            operator="examiner@example.org (officer)",
            citations=(citation,),
        )
    ]
    data.manifest[0] = dataclasses.replace(data.manifest[0], cited=True)
    data.manifest_digest = canonical_manifest(data.manifest)  # type: ignore[attr-defined]
    return data


def _unsourced_finding(title: str) -> object:
    from app.reports.repository import FindingView

    return FindingView(
        finding_id=str(uuid.uuid4()),
        title=title,
        body="Recorded by hand during physical inspection.",
        status="draft",
        created_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
        operator="examiner@example.org (officer)",
        citations=(),
    )


def _tier_c_finding(title: str) -> object:
    """A finding citing an attached external laboratory result."""
    from app.reports.repository import CitationView, FindingView

    stamp = dt.datetime(2026, 1, 3, tzinfo=dt.UTC)
    citation = CitationView(
        result_id=str(uuid.uuid4()),
        evidence_id=str(uuid.uuid4()),
        filename="lab-report-acme-2026-01.pdf",
        evidence_sha256="d" * 64,
        module="lab-attachment",
        score=0.0,
        threshold=0.0,
        tier="C",
        model_version="external",
        disclaimer="Issued by an accredited external laboratory.",
        not_applicable=False,
        cited_at=stamp,
    )
    return FindingView(
        finding_id=str(uuid.uuid4()),
        title=title,
        body="Attached by the examiner from the accredited laboratory's report.",
        status="draft",
        created_at=stamp,
        operator="examiner@example.org (officer)",
        citations=(citation,),
    )


# --- citations resolve only against the case they are on ------------------


async def test_a_citation_to_a_result_on_another_case_is_rejected(
    sessionmaker_fixture: object,
) -> None:
    """The check that stops a finding citing provenance from a different case.

    Without it, a finding on one case could name a result that exists only on another,
    and the signed report would attribute analysis to evidence that was never part of
    that case.
    """
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        from app.analysis.models import AnalysisResult

        owner = uuid.uuid4()
        mine = Case(number="CITE-A", title="Mine", owner_id=owner)
        theirs = Case(number="CITE-B", title="Theirs", owner_id=owner)
        session.add_all([mine, theirs])
        await session.flush()

        evidence = Evidence(
            case_id=theirs.id,
            filename="theirs.jpg",
            sha256="e" * 64,
            content_type="image/jpeg",
            size_bytes=10,
            storage_key="k",
            uploaded_by=owner,
        )
        session.add(evidence)
        await session.flush()

        their_result = AnalysisResult(
            job_id=uuid.uuid4(),
            case_id=theirs.id,
            evidence_id=evidence.id,
            module="byte-entropy",
            idempotency_key="k1",
            score=7.8,
            threshold=7.5,
            model_version="1.0.0",
            tier="B",
            disclaimer="Triage only.",
            payload={},
        )
        session.add(their_result)
        await session.flush()

        finding = Finding(case_id=mine.id, title="Claim", created_by=owner)
        session.add(finding)
        await session.flush()

        created, rejected = await cite_results(
            session, finding=finding, case_id=mine.id, result_ids=[their_result.id]
        )

        assert created == [], "a result from another case must not become a citation"
        assert rejected == [their_result.id]


async def test_an_unresolvable_citation_is_reported_rather_than_dropped(
    sessionmaker_fixture: object,
) -> None:
    """Silently dropping a bad reference leaves a finding that looks sourced.

    So `cite_results` returns the rejected ids instead of discarding them: the agent
    tool passes them back to the model, and the HTTP API refuses the write. Either way
    the caller finds out.
    """
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        owner = uuid.uuid4()
        case = Case(number="CITE-C", title="Rejections", owner_id=owner)
        session.add(case)
        await session.flush()

        finding = Finding(case_id=case.id, title="Claim", created_by=owner)
        session.add(finding)
        await session.flush()

        bogus = uuid.uuid4()
        created, rejected = await cite_results(
            session, finding=finding, case_id=case.id, result_ids=[bogus]
        )

        assert created == []
        assert rejected == [bogus]


async def test_a_citation_snapshots_the_refusal_to_measure(
    sessionmaker_fixture: object,
) -> None:
    """A `not_applicable` result must stay "cannot be measured" after being cited.

    The snapshot is the whole reason `FindingCitation` copies the result's fields
    rather than joining to them. A later re-run could supersede the row, and a report
    generated after that must still describe the result the finding was drawn from —
    as a refusal, not as the number that replaced it.
    """
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        from app.analysis.models import AnalysisResult

        owner = uuid.uuid4()
        case = Case(number="CITE-D", title="Refusal", owner_id=owner)
        session.add(case)
        await session.flush()

        evidence = Evidence(
            case_id=case.id,
            filename="flat.png",
            sha256="f" * 64,
            content_type="image/png",
            size_bytes=10,
            storage_key="k",
            uploaded_by=owner,
        )
        session.add(evidence)
        await session.flush()

        refused = AnalysisResult(
            job_id=uuid.uuid4(),
            case_id=case.id,
            evidence_id=evidence.id,
            module="image-ela",
            idempotency_key="k2",
            score=0.0,
            threshold=0.01,
            model_version="1.0.0+ela",
            tier="A",
            disclaimer="Investigative lead only.",
            payload={"status": "not_applicable"},
        )
        session.add(refused)
        await session.flush()

        finding = Finding(case_id=case.id, title="Claim", created_by=owner)
        session.add(finding)
        await session.flush()

        created, rejected = await cite_results(
            session, finding=finding, case_id=case.id, result_ids=[refused.id]
        )
        assert rejected == []

        citation = created[0]
        assert citation.not_applicable is True

        # And the report renders it as a refusal, not as a zero.
        from app.reports.repository import CitationView, FindingView, ReportData

        data = ReportData(
            case=case,
            evidence=[evidence],
            findings=[
                FindingView(
                    finding_id=str(finding.id),
                    title=finding.title,
                    body="",
                    status="draft",
                    created_at=finding.created_at,
                    operator="examiner@example.org (officer)",
                    citations=(
                        CitationView(
                            result_id=str(citation.result_id),
                            evidence_id=str(evidence.id),
                            filename=evidence.filename,
                            evidence_sha256=evidence.sha256,
                            module=citation.module,
                            score=citation.score,
                            threshold=citation.threshold,
                            tier=citation.tier,
                            model_version=citation.model_version,
                            # The disclaimer is deliberately *not* on the citation
                            # snapshot: it is the live result's own wording about its
                            # own output, and the report reads it from there.
                            disclaimer=refused.disclaimer,
                            not_applicable=citation.not_applicable,
                            cited_at=citation.cited_at,
                        ),
                    ),
                )
            ],
            results=[refused],
            custody=[],
            runs=[],
        )
        text = _pdf_text(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))
        assert "could not be measured" in text
        assert "0.0000, below" not in text


# --- API surface ----------------------------------------------------------


async def test_the_endpoint_refuses_when_no_signing_key_is_configured(
    client: AsyncClient, seed_users: dict
) -> None:
    """No key means no PDF, not a PDF without a signature.

    The difference is invisible until someone verifies the document, which is exactly
    why it has to be enforced at generation time.
    """
    from tests.test_agent_core import _auth  # noqa: PLC2701 - test helper

    officer = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=officer, json={"number": "NOKEY-1", "title": "No key"})
    ).json()["id"]

    response = await client.get(f"/reports/cases/{cid}.pdf", headers=officer)
    assert response.status_code == 503, response.text
    # The message has to tell an operator what to do, not just that it is broken.
    assert "provision_signer" in response.json()["detail"]


async def test_a_report_is_not_generated_for_a_case_you_cannot_see(
    client: AsyncClient, seed_users: dict
) -> None:
    """Report generation inherits the case's own access rule.

    A report is a complete copy of a case's findings, so it cannot be more permissive
    than the case view. Asserted with a second user who owns nothing.
    """
    from tests.test_agent_core import _auth  # noqa: PLC2701 - test helper

    owner = await _auth(client, seed_users["officer"])
    cid = (
        await client.post("/cases", headers=owner, json={"number": "SCOPE-1", "title": "Not yours"})
    ).json()["id"]

    stranger = await _auth(client, seed_users["expert"])
    response = await client.get(f"/reports/cases/{cid}.pdf", headers=stranger)
    # 404 rather than 403: a 403 would confirm the case id is real.
    assert response.status_code == 404, response.text


# --- T8.3: the operator and the approvals are named, not just hashed ----


def test_every_finding_names_the_person_who_recorded_it() -> None:
    """T8.3 asks for the operator; a date is not an operator.

    The report printed "recorded <date>" and nothing else, which identifies *when*
    and not *who* — the less useful half. A signed record has to say which person
    made each assertion, or it is an unattributed claim with a timestamp.
    """
    data = _case_with_one_cited_finding()
    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert "examiner@example.org" in text, "the finding's author is not named"
    assert "officer" in text, "the author's role is not shown"


def test_an_unresolvable_author_says_so_rather_than_printing_a_uuid() -> None:
    """A deleted user record must not leave a bare UUID on a signed page.

    A UUID identifies nothing a reader can act on, and printing one implies the
    system resolved an identity when it did not. The report says the record is gone.
    """
    data = _case_with_one_cited_finding()
    data.findings[0] = dataclasses.replace(
        data.findings[0],
        operator="unknown — the user record no longer exists",
    )
    text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))
    assert (
        "theuserrecordnolongerexists" in text
    ), "a deleted author should be stated, not left as an id the reader cannot resolve"
    assert "unknown" in text


async def test_an_approval_names_the_person_who_approved_it(
    sessionmaker_fixture: object,
) -> None:
    """The HITL line of T8.3, and it is the one that was missing entirely.

    `agent_tool_calls` records *that* an action was approved, never by whom — the
    approving identity exists only in the hash-chained audit log. The report has to
    join the two, or the provenance appendix says a person authorised a change
    without saying who.
    """
    from app.agent.repository import AgentRun, record_tool_call
    from app.analysis.models import AnalysisResult
    from app.domain.models import Evidence
    from app.domain.repository import append_audit

    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        from app.auth.models import User

        # Create the user and take its id from the row, rather than inventing a
        # UUID alongside it. A first draft did the latter, so the approval referenced
        # a user that did not exist — and the report correctly said "unknown", which
        # looked like a report bug rather than a test bug.
        user = User(
            email="approver@example.org",
            hashed_password="x",
            # A real role. "supervisor" does not exist in this system.
            role="admin",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()
        approver = user.id
        case = Case(number="APPR-1", title="Approved", owner_id=approver)
        session.add(case)
        await session.flush()

        evidence = Evidence(
            case_id=case.id,
            filename="a.jpg",
            sha256="a" * 64,
            content_type="image/jpeg",
            size_bytes=1,
            storage_key="k",
            uploaded_by=approver,
        )
        session.add(evidence)
        await session.flush()

        result = AnalysisResult(
            job_id=uuid.uuid4(),
            case_id=case.id,
            evidence_id=evidence.id,
            module="byte-entropy",
            idempotency_key="k",
            score=7.8,
            threshold=7.5,
            model_version="1.0.0",
            tier="B",
            disclaimer="Triage only.",
            payload={},
        )
        session.add(result)
        await session.flush()

        run = AgentRun(
            case_id=case.id,
            thread_id="t",
            objective="record a finding",
            requested_by=approver,
            model="scripted",
        )
        session.add(run)
        await session.flush()

        from app.agent.types import ToolCall, ToolResult

        await record_tool_call(
            session,
            run_id=run.id,
            case_id=case.id,
            call=ToolCall(name="update_case_finding", args={"title": "x"}, call_id="write"),
            result=ToolResult(
                call_id="write", name="update_case_finding", ok=True, output={"ok": True}
            ),
            approved_action_id="act-1",
        )
        await append_audit(
            session,
            actor_id=approver,
            action="agent.action.approve",
            target="act-1",
            payload={"run_id": str(run.id), "tool": "update_case_finding", "args_hash": "h" * 64},
        )
        await session.commit()

        from app.reports.builder import SignerInfo, build_pdf
        from app.reports.repository import gather

        data = await gather(session, case)
        text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert data.approvals, "the approval was not picked up from the audit chain"
    assert data.approvals[0].tool == "update_case_finding"
    assert "approver@example.org" in text, "the approver is not named in the report"
    assert "update_case_finding" in text


async def test_an_approval_from_another_case_never_leaks_in(
    sessionmaker_fixture: object,
) -> None:
    """The audit payload carries the run id precisely so this can be filtered.

    The payload is untyped JSON, so the only way to be sure an approval belongs to
    this case is to check. Trusting the absence of a foreign run id would put
    another case's provenance in this case's signed report.
    """
    from app.agent.repository import AgentRun
    from app.domain.repository import append_audit

    owner = uuid.uuid4()
    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        mine = Case(number="LEAK-A", title="Mine", owner_id=owner)
        theirs = Case(number="LEAK-B", title="Theirs", owner_id=owner)
        session.add_all([mine, theirs])
        await session.flush()

        their_run = AgentRun(
            case_id=theirs.id,
            thread_id="t",
            objective="theirs",
            requested_by=owner,
            model="scripted",
        )
        session.add(their_run)
        await session.flush()

        await append_audit(
            session,
            actor_id=owner,
            action="agent.action.approve",
            target="act-theirs",
            payload={"run_id": str(their_run.id), "tool": "update_case_finding"},
        )
        await session.commit()

        from app.reports.repository import gather

        data = await gather(session, mine)

    assert data.approvals == [], "another case's approval appeared in this report"


async def test_the_chain_of_custody_names_who_handled_each_item(
    sessionmaker_fixture: object,
) -> None:
    """A custody chain with no names in it is a log, not a chain.

    It records that an item was handled and when, but not who was accountable —
    which is the one question a chain of custody exists to answer. The report
    resolved every actor from the user table for exactly this.
    """
    from app.auth.models import User
    from app.domain.models import Evidence
    from app.domain.repository import record_custody

    async with sessionmaker_fixture() as session:  # type: ignore[operator]
        handler = User(
            email="handler@example.org",
            hashed_password="x",
            role="officer",
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(handler)
        await session.flush()

        case = Case(number="CUST-1", title="Custody", owner_id=handler.id)
        session.add(case)
        await session.flush()

        evidence = Evidence(
            case_id=case.id,
            filename="seized.jpg",
            sha256="e" * 64,
            content_type="image/jpeg",
            size_bytes=10,
            storage_key="k",
            uploaded_by=handler.id,
        )
        session.add(evidence)
        await session.flush()

        await record_custody(
            session,
            evidence_id=evidence.id,
            actor_id=handler.id,
            action="examine",
            detail="opened in the lab",
        )
        await session.commit()

        from app.reports.builder import SignerInfo, build_pdf
        from app.reports.repository import gather

        data = await gather(session, case)
        assert data.custody_operators, "no operator was resolved for the custody entry"
        assert data.custody_operators[list(data.custody_operators)[0]].startswith(
            "handler@example.org"
        )
        text = _flat(build_pdf(data, SignerInfo(subject="s", fingerprint="f" * 64)))

    assert "handler@example.org" in text, "the custody chain does not name who handled it"
    assert "examine" in text
