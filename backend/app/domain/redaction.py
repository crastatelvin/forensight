"""Redaction for case exports (FI-3).

**The whole point of this module is the verification, not the removal.** A redaction an
operator believes in and that did not take effect is worse than no redaction at all:
it converts an exposure into a concealed one. So every export collects the values it is
about to remove, checks the serialised bytes for those exact values, and refuses to
send the document if any survived.

Checking the *real* values is what makes this work, rather than a token sweep. A
generic "no owner id in the document" check passes just as well on an export where the
redaction silently failed; checking the specific UUIDs that were present before
redaction cannot. So `collect` runs before `apply`, and `verify_absent` is handed what
was actually taken out.

**Redaction is by field name and by explicit literal, never by pattern.** Regex PII
detection is the obvious thing to reach for and it is deliberately not here: a pattern
that matches an email also matches a sha256 quoted in a finding, and a tool reporting
"42 items redacted" without being able to say which 42 is asserting something it cannot
support. Named fields are enumerable and checkable; a pattern over free text is
neither. What this removes is what someone chose by name.

**A redacted export is not evidence and is never signed as a report.** It has had
content taken out of it, so its digest no longer matches the case file. The PAdES-signed
report (P8.B1) is unaffected by anything here, and redacting an export does not touch
it.

**What this does not cover.** Free text inside a case description, a finding body or a
filename passes through untouched unless a caller names it as a literal. Deciding that
a particular name should come out of a particular case is a judgement about a specific
string, and inferring it from a pattern is the failure mode above. `literals` exists so
an examiner can name exactly that string — and because naming it is what makes the
absence checkable afterwards.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from typing import Any, cast

#: Fields this module knows how to redact, and what replaces them.
#:
#: An explicit allowlist rather than "everything shaped like an id". A deny-list would
#: redact whatever a future column happens to be named, and a redaction that silently
#: grows to cover new fields is one nobody has reviewed.
FIELD_REDACTIONS: dict[str, str] = {
    "owner_id": "[redacted: officer]",
    "assigned_to": "[redacted: officer]",
    "uploaded_by": "[redacted: officer]",
    "created_by": "[redacted: officer]",
    "actor_id": "[redacted: officer]",
}

REDACTED_PLACEHOLDER = "[redacted]"


class RedactionError(RuntimeError):
    """A redaction could not be applied, so the export is refused."""


@dataclass(frozen=True)
class Redaction:
    """What to remove from an export.

    `fields` names keys from `FIELD_REDACTIONS`. `literals` are exact strings to scrub
    wherever they appear — a specific person's name or an identifier that occurs in
    free text, where naming it is the only way to check afterwards that it is gone.
    """

    fields: frozenset[str] = field(default_factory=frozenset)
    literals: frozenset[str] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        return bool(self.fields or self.literals)

    def describe(self) -> dict[str, Any]:
        return {
            "redacted_fields": sorted(self.fields),
            "redacted_literals": len(self.literals),
            "note": (
                "free text is not pattern-matched; only named fields and literals the "
                "caller named are removed. A redacted export is not evidence and is "
                "never signed."
            ),
        }


def parse(fields: str | None, literals: str | None = None) -> Redaction:
    """Build a `Redaction` from query parameters, refusing unknown field names.

    An unrecognised name is an error rather than a no-op: asking to redact `owner` and
    receiving a clean export with nothing removed is precisely the outcome this module
    exists to prevent.
    """
    literals_set = frozenset(part for part in (literals or "").split("\n") if part)
    wanted = frozenset(part.strip() for part in (fields or "").split(",") if part.strip())
    if not wanted:
        return Redaction(literals=literals_set)

    unknown = sorted(wanted - set(FIELD_REDACTIONS))
    if unknown:
        raise RedactionError(
            f"cannot redact unknown field(s) {', '.join(unknown)}; known fields: "
            f"{', '.join(sorted(FIELD_REDACTIONS))}"
        )
    return Redaction(fields=wanted, literals=literals_set)


# --- secrets ---------------------------------------------------------------


def _forms(value: Any) -> set[str]:
    """Every text form a value can take in an export.

    The UUID case is why this is not just `str(value)`: it appears hyphenated in JSON
    and as a bare 32-character run in some CSV writers, so checking only the hyphenated
    form would report a clean export while the identifier is still readable in it.

    **A list is flattened rather than stringified.** The first version did
    `str(value)` unconditionally, so a redacted field holding a list produced the
    *repr* of that list — `"['1111…', '2222…']"` — which matches nothing in the
    serialised output. The absence check then passed on an export where the values were
    still present, which is the exact silent-pass this module exists to prevent. Found
    by a test that nested the value in a list.
    """
    if value is None or isinstance(value, bool | int | float):
        return set()
    if isinstance(value, list | tuple | set):
        forms: set[str] = set()
        for item in value:
            forms |= _forms(item)
        return forms
    text = str(value)
    if not text:
        return set()
    return {form for form in (text, text.replace("-", "")) if form}


def collect(document: Any, redaction: Redaction) -> set[str]:
    """Every value the redaction is about to remove, in every form it could appear in.

    Called *before* `apply`, so it sees the original values rather than the
    placeholders that replaced them.
    """
    secrets: set[str] = set()
    if not redaction.active:
        return secrets

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if key in redaction.fields:
                    secrets.update(_forms(item))
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(document)
    for literal in redaction.literals:
        secrets.update(_forms(literal))
    return secrets


# --- application -----------------------------------------------------------


def _scrub(node: Any, redaction: Redaction) -> Any:
    if isinstance(node, dict):
        return {
            key: (FIELD_REDACTIONS[key] if key in redaction.fields else _scrub(item, redaction))
            for key, item in node.items()
        }
    if isinstance(node, list):
        return [_scrub(item, redaction) for item in node]
    if isinstance(node, str):
        # Longest literal first: replacing a short literal that is a substring of a
        # longer one leaves the remainder of the longer one behind, and the absence
        # check would then fail for a reason that looks like an exporter bug.
        for literal in sorted(redaction.literals, key=len, reverse=True):
            if literal:
                node = node.replace(literal, REDACTED_PLACEHOLDER)
        return node
    return node


def apply(document: dict[str, Any], redaction: Redaction) -> dict[str, Any]:
    """Return a redacted copy. The input is never mutated.

    Copying rather than editing in place because the same structure may already be
    bound to something the caller still needs unredacted — and because a redaction that
    mutates its input cannot be undone by a caller who changes their mind.
    """
    if not redaction.active:
        return document
    scrubbed = _scrub(document, redaction)
    # `_scrub` preserves the mapping type it was given, but mypy cannot see that
    # through the recursion; the cast says what is already guaranteed.
    return cast("dict[str, Any]", scrubbed)


# --- verification ----------------------------------------------------------


def verify_absent(content: bytes, secrets: set[str], *, label: str = "export") -> None:
    """Raise unless every value the redaction removed is gone from `content`.

    Raises rather than returning a verdict, because a caller that ignored a failed
    check would send exactly the document this exists to stop. Same rule as the
    provenance guard.
    """
    if not secrets:
        return
    text = content.decode("utf-8", errors="replace")
    survivors = sorted(form for form in secrets if form in text)
    if survivors:
        raise RedactionError(
            f"refusing to send the {label}: redaction did not take effect for "
            f"{len(survivors)} value(s), including {survivors[0][:16]}…"
        )


def verify_zip(content: bytes, secrets: set[str]) -> None:
    """Verify inside every member of a ZIP export.

    Checking the archive's outer bytes is not enough and is actively misleading: ZIP
    compresses its members, so a naive scan of the container finds no plaintext and
    reports success on an archive whose `case.json` still names the officer.
    """
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            verify_absent(archive.read(name), secrets, label=f"archive member {name}")


def serialise(document: dict[str, Any], redaction: Redaction) -> tuple[bytes, set[str]]:
    """Redact, serialise, and verify the serialised bytes.

    Verification is on the bytes that will be sent, not the structure they came from.
    Those are not the same thing: serialisation is where a value can reappear through a
    nested container or a default nobody inspected.

    Returns the bytes and the secrets, so a caller wrapping the result in another
    container (a ZIP) can verify inside it too.
    """
    secrets = collect(document, redaction)
    content = json.dumps(apply(document, redaction), indent=2).encode("utf-8")
    verify_absent(content, secrets)
    return content, secrets
