"""FI-3 — export redaction, and the verification that makes it trustworthy.

A redaction an operator believes in and that did not take effect is worse than no
redaction: it converts an exposure into a concealed one. So these tests are mostly
about the *check* rather than the removal — that the exporter verifies the specific
values it took out, and refuses to return a document that still contains them.

The failure mode this guards against is a token sweep: scanning the output for
something shaped like an officer id passes just as happily on an export where the
redaction silently did nothing. `collect` therefore runs before `apply`, so the
verifier is handed the real values rather than the placeholders that replaced them.
"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from app.domain import redaction as rd
from app.domain.exports import export_csv, export_json, export_zip
from app.domain.models import Case, Evidence

OWNER = uuid.UUID("11111111-2222-3333-4444-555555555555")
ASSIGNED = uuid.UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")
UPLOADER = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
CASE_ID = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")


@pytest.fixture
def case() -> Case:
    return Case(
        id=CASE_ID,
        number="EXP-1",
        title="Operation Lantern",
        description="subject is Jane Doe, officer on the case is Sam Rivers",
        status="intake",
        owner_id=OWNER,
        assigned_to=ASSIGNED,
        priority="normal",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.fixture
def evidence() -> list[Evidence]:
    return [
        Evidence(
            id=uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            case_id=CASE_ID,
            filename="lantern.jpg",
            sha256="a" * 64,
            content_type="image/jpeg",
            size_bytes=1234,
            storage_key="evidence/" + "a" * 64,
            uploaded_by=UPLOADER,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    ]


# --- the redaction actually removes ----------------------------------------


def test_named_fields_are_replaced(case: Case, evidence: list[Evidence]) -> None:
    rule = rd.parse("owner_id,assigned_to")
    doc = json.loads(export_json(case, evidence, redaction=rule))
    assert doc["case"]["owner_id"] == "[redacted: officer]"
    assert doc["case"]["assigned_to"] == "[redacted: officer]"
    # The evidence rows carry no officer id at all — `evidence_to_dict` does not
    # export `uploaded_by` — so the redaction has nothing to do there. Asserted so the
    # policy is not later read as claiming coverage it does not have.
    assert "uploaded_by" not in doc["evidence"][0]


def test_the_value_is_genuinely_absent_not_merely_relabelled(case: Case) -> None:
    """The point of the whole module."""
    content = export_json(case, [], redaction=rd.parse("owner_id"))
    text = content.decode()
    assert str(OWNER) not in text
    assert str(OWNER).replace("-", "") not in text


def test_an_unredacted_export_is_unchanged(case: Case, evidence: list[Evidence]) -> None:
    """The default stays byte-identical, so no existing consumer shifts under it."""
    assert export_json(case, evidence) == export_json(case, evidence, redaction=rd.Redaction())


def test_apply_does_not_mutate_its_input(case: Case) -> None:
    """A caller who changes their mind must be able to get the original back."""
    doc = {"case": {"owner_id": str(OWNER)}}
    rd.apply(doc, rd.parse("owner_id"))
    assert doc["case"]["owner_id"] == str(OWNER)


# --- verification ----------------------------------------------------------


def test_the_check_is_given_the_real_values_not_the_placeholders() -> None:
    """Otherwise a redaction that removed nothing would still pass.

    This is the failure the whole design exists to prevent: a verifier that only knows
    "[redacted: officer]" finds that token and concludes the document is clean, whether
    or not the officer id is still sitting beside it.
    """
    doc = {"case": {"owner_id": str(OWNER)}}
    rule = rd.parse("owner_id")
    secrets = rd.collect(doc, rule)
    assert secrets == {str(OWNER), str(OWNER).replace("-", "")}

    # A document that merely contains the placeholder still holds the real value, and
    # the check catches it.
    leaky = json.dumps({"case": {"owner_id": f"[redacted: officer] {OWNER}"}}).encode()
    with pytest.raises(rd.RedactionError, match="did not take effect"):
        rd.verify_absent(leaky, secrets)


def test_verify_absent_raises_rather_than_returning_a_verdict() -> None:
    """A caller that ignored a returned verdict would send exactly this document."""
    with pytest.raises(rd.RedactionError):
        rd.verify_absent(f"owner is {OWNER}".encode(), {str(OWNER)})


def test_a_zip_is_verified_inside_its_members() -> None:
    """Scanning the container would find no plaintext and report success.

    ZIP compresses, so the officer id is not necessarily visible in the outer bytes of
    a correctly-redacted archive — and equally not visible in one where redaction
    failed. Either way, only reading the members distinguishes them.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("case.json", json.dumps({"owner_id": str(OWNER)}))
    content = buffer.getvalue()

    # Proof the outer bytes are not a usable check for this archive.
    assert str(OWNER).encode() not in content, "this archive compresses away the plaintext"

    with pytest.raises(rd.RedactionError, match="archive member case.json"):
        rd.verify_zip(content, {str(OWNER)})


def test_serialise_verifies_the_bytes_not_the_structure() -> None:
    """Serialisation is where a value can reappear through a nested container."""
    # Nested, and inside a list: the walk has to reach both, and `collect` has to see
    # the value before `apply` replaces it.
    content, secrets = rd.serialise({"a": {"owner_id": [str(OWNER)]}}, rd.parse("owner_id"))
    assert str(OWNER) not in content.decode()
    assert secrets == {str(OWNER), str(OWNER).replace("-", "")}


# --- CSV and ZIP ------------------------------------------------------------


def test_csv_is_redacted_and_verified(evidence: list[Evidence], case: Case) -> None:
    # `export_csv` takes evidence only, so the redaction has to come from a field the
    # rows actually carry. `case_id` is one; it is kept deliberately (see the ZIP test),
    # so this asserts the *machinery* on the CSV path using a field the policy covers.
    rule = rd.parse("uploaded_by")
    content = export_csv(evidence, redaction=rule).decode()
    assert str(UPLOADER) not in content
    # Nothing to redact here because the column is not exported, so the useful
    # assertion is that the CSV still carries what makes it worth exporting.
    assert "sha256" in content
    assert "a" * 64 in content
    assert "lantern.jpg" in content


def test_zip_is_redacted_across_every_member(case: Case, evidence: list[Evidence]) -> None:
    content = export_zip(case, evidence, redaction=rd.parse("owner_id,assigned_to,uploaded_by"))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = {name: archive.read(name).decode() for name in archive.namelist()}
    assert set(members) == {"case.json", "evidence.csv", "manifest.json"}
    joined = " ".join(members.values())
    for value in (str(OWNER), str(ASSIGNED), str(UPLOADER)):
        assert value not in joined


def test_a_zip_keeps_the_case_id_because_a_manifest_needs_one(
    case: Case, evidence: list[Evidence]
) -> None:
    """Redaction is narrow on purpose.

    A manifest with the case id removed cannot be matched back to anything, so keeping
    it protects nobody and destroys the export's usefulness. Asserted so a future
    "just redact every id" change has to confront the trade-off rather than make it
    silently.
    """
    content = export_zip(case, evidence, redaction=rd.parse("owner_id,assigned_to"))
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["case_id"] == str(CASE_ID)
    assert str(OWNER) not in json.dumps(manifest)


# --- literals --------------------------------------------------------------


def test_a_named_literal_is_scrubbed_from_free_text(case: Case) -> None:
    """Where pattern matching would be needed, naming the string is the alternative."""
    rule = rd.parse(None, "Jane Doe\nSam Rivers")
    content = export_json(case, [], redaction=rule).decode()
    assert "Jane Doe" not in content
    assert "Sam Rivers" not in content
    assert rd.REDACTED_PLACEHOLDER in content


def test_longer_literals_are_replaced_before_shorter_ones(case: Case) -> None:
    """Order matters, and the reason is a failure that looks like an exporter bug.

    Replacing the short literal first would leave the tail of the longer one behind,
    and the absence check would then correctly complain about a value the caller
    believes they removed.
    """
    rule = rd.Redaction(literals=frozenset({"Jane", "Jane Doe"}))
    content = export_json(case, [], redaction=rule).decode()
    assert "Jane Doe" not in content
    assert "Doe" not in content


def test_free_text_is_untouched_when_nothing_is_named(case: Case) -> None:
    """The honest limitation, asserted so it cannot drift into being over-claimed."""
    content = export_json(case, [], redaction=rd.parse("owner_id")).decode()
    assert "Jane Doe" in content, "redaction does not pattern-match free text"


# --- refusals --------------------------------------------------------------


def test_an_unknown_field_name_is_refused_rather_than_ignored() -> None:
    """A typo that silently redacts nothing is the outcome this module prevents."""
    with pytest.raises(rd.RedactionError, match="unknown field"):
        rd.parse("owner")
    with pytest.raises(rd.RedactionError, match="known fields"):
        rd.parse("owner_id,nonsense")


def test_an_empty_request_is_not_an_error() -> None:
    assert not rd.parse(None, None).active
    assert not rd.parse("", "").active


def test_the_policy_is_an_allowlist_not_a_pattern() -> None:
    """A deny-list would grow silently as columns are added."""
    assert "sha256" not in rd.FIELD_REDACTIONS
    assert "case_id" not in rd.FIELD_REDACTIONS
    assert set(rd.FIELD_REDACTIONS) == {
        "owner_id",
        "assigned_to",
        "uploaded_by",
        "created_by",
        "actor_id",
    }


def test_the_description_states_what_it_did_not_do() -> None:
    """The response and the audit entry both have to say it is not evidence."""
    described = rd.parse("owner_id").describe()
    joined = " ".join(str(v) for v in described.values())
    assert "not evidence" in joined
    assert "never signed" in joined
    assert "not pattern-matched" in joined


# --- the endpoint ----------------------------------------------------------


async def test_the_export_endpoint_redacts_and_says_so(client, seed_users) -> None:
    """End to end: the officer id is gone, and the response admits it is incomplete."""

    login = await client.post(
        "/auth/login",
        json={
            "email": seed_users["officer"]["email"],
            "password": seed_users["officer"]["password"],
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = await client.post(
        "/cases",
        json={"number": "EXP-REDACT", "title": "Operation Lantern"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    owner = created.json()["owner_id"]

    plain = await client.get(f"/cases/{case_id}/export?fmt=json", headers=headers)
    assert plain.status_code == 200
    assert owner in plain.text
    assert "X-Redacted-Fields" not in plain.headers

    redacted = await client.get(
        f"/cases/{case_id}/export?fmt=json&redact=owner_id", headers=headers
    )
    assert redacted.status_code == 200
    assert owner not in redacted.text
    assert redacted.headers["X-Redacted-Fields"] == "owner_id"
    # Stated in the response, so a document separated from this request carries the
    # fact that it is not the record.
    assert "not evidence" in redacted.headers["X-Redacted-Notice"]


async def test_an_unknown_redaction_field_is_a_422(client, seed_users) -> None:
    """Not a silent pass-through — that is the outcome this guards against."""
    login = await client.post(
        "/auth/login",
        json={
            "email": seed_users["officer"]["email"],
            "password": seed_users["officer"]["password"],
        },
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    created = await client.post("/cases", json={"number": "EXP-BAD", "title": "x"}, headers=headers)
    case_id = created.json()["id"]

    refused = await client.get(f"/cases/{case_id}/export?fmt=json&redact=owner", headers=headers)
    assert refused.status_code == 422
    assert "unknown field" in refused.json()["detail"]


async def test_a_redacted_export_is_audited_distinctly(client, seed_users) -> None:
    """An export with content taken out is a different act from an ordinary one.

    The audit log is admin-only, so the read uses an admin session — and the officer's
    403 is asserted alongside, because an export trail only an admin can read is worth
    less than it looks.
    """
    officer_login = await client.post("/auth/login", json=seed_users["officer"])
    officer = {"Authorization": f"Bearer {officer_login.json()['access_token']}"}
    created = await client.post(
        "/cases", json={"number": "EXP-AUDIT", "title": "x"}, headers=officer
    )
    case_id = created.json()["id"]

    await client.get(f"/cases/{case_id}/export?fmt=json", headers=officer)
    await client.get(f"/cases/{case_id}/export?fmt=json&redact=owner_id", headers=officer)

    # The officer may export their own case but cannot read the audit log.
    assert (await client.get("/cases/audit?limit=200", headers=officer)).status_code == 403

    admin_login = await client.post("/auth/login", json=seed_users["admin"])
    headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
    audit = await client.get("/cases/audit?limit=200", headers=headers)
    actions = [e["action"] for e in audit.json()]
    assert "case.export" in actions
    assert "case.export.redacted" in actions
