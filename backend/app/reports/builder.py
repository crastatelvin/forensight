"""Rendering the signed case report (P8).

ReportLab rather than an HTML-to-PDF pipeline, for one concrete reason: WeasyPrint
and its relatives need Cairo, Pango and a stack of system libraries, and this
project ships as a slim Python container. ReportLab is pip-only and Pillow is
already a dependency, so a report is buildable anywhere the API already runs.

**The honesty clause reaches all the way into the typesetting.** Every claim the
report makes is checked before it is drawn, and the failure modes are all "say the
weaker true thing":

- A finding with no citations is printed under "NOT SOURCED" and is visually set
  apart from sourced findings. It is never laid out in a way that implies analysis
  backs it.
- A result the analyzer refused to measure prints as "could not be measured", never
  as `0.0000`. `not_applicable` travels through to the page.
- A Tier-C result appears only under attached laboratory results, in its own
  section, and is never counted among the findings — an external lab's conclusion is
  not this system's finding, and presenting it in a findings list would transfer
  authorship.
- The signature block states what the signature does *not* establish: it says
  nothing about when signing happened, and nothing about who the signer is until a
  reader supplies a trust root.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.reports.repository import ReportData, summary_counts

#: Printed on the cover and repeated in the footer of every page. The generator's
#: version is part of it because a report's meaning depends on the code that made
#: it — a reader holding two reports needs to know whether the same rules produced
#: them.
GENERATOR = "ForenSight 0.1.0"

INK = colors.HexColor("#111418")
MUTED = colors.HexColor("#4a5158")
RULE = colors.HexColor("#c9ced4")
ALERT = colors.HexColor("#8a1c1c")
WARN = colors.HexColor("#7a5200")
ACCENT = colors.HexColor("#1f4e79")
PAPER = colors.HexColor("#ffffff")
BAND = colors.HexColor("#f2f4f6")

#: Long hex strings are broken explicitly rather than left to wrap. A digest that
#: flows across lines can be joined, but a reader cannot tell where one line ends
#: and the next begins, so a single dropped character goes unnoticed — and a digest
#: that cannot be checked exactly is not a digest, it is decoration.
HEX_STYLE = '<font face="Courier" size="7">{0}</font>'

#: Hex characters per line when a digest is broken up. Sixteen fits the narrowest
#: column in the report twice over and divides 64 evenly, so no line is short.
HEX_CHUNK = 16


@dataclass(frozen=True)
class SignerInfo:
    """What the report knows about its own signature."""

    subject: str
    fingerprint: str


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    mono = {"fontName": "Courier", "fontSize": 7, "leading": 9, "textColor": INK}
    return {
        "title": ParagraphStyle(
            "t", parent=base["Title"], fontSize=22, leading=26, textColor=INK, spaceAfter=2
        ),
        "subtitle": ParagraphStyle(
            "st", parent=base["Normal"], fontSize=10, textColor=MUTED, spaceAfter=14
        ),
        "h1": ParagraphStyle(
            "h1",
            parent=base["Heading1"],
            fontSize=14,
            leading=17,
            textColor=ACCENT,
            spaceBefore=16,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base["Heading2"],
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "b",
            parent=base["Normal"],
            fontSize=9,
            leading=12.5,
            textColor=INK,
            alignment=TA_LEFT,
            spaceAfter=6,
        ),
        "small": ParagraphStyle(
            "s",
            parent=base["Normal"],
            fontSize=7.5,
            leading=10,
            textColor=MUTED,
            spaceAfter=4,
        ),
        "alert": ParagraphStyle(
            "al",
            parent=base["Normal"],
            fontSize=8.5,
            leading=11.5,
            textColor=ALERT,
            spaceAfter=4,
        ),
        "mono": ParagraphStyle("m", **mono),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=7.5, leading=9.5),
        "cell_head": ParagraphStyle(
            "ch",
            parent=base["Normal"],
            fontSize=7.5,
            leading=9.5,
            textColor=INK,
            fontName="Helvetica-Bold",
        ),
    }


def _esc(text: object) -> str:
    """Escape for ReportLab's mini-HTML.

    Every piece of case-supplied text passes through here — filenames, finding
    bodies, error strings. ReportLab's `Paragraph` parses a `<` in a filename as a
    tag, so an evidence file called `<script>` would either break the page or, worse,
    be interpreted as markup that this application then writes into a signed legal
    document.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _wrap_hash(value: str) -> str:
    """A digest as monospace, broken into fixed 16-character lines.

    Broken by this function rather than by ReportLab's own wrapping for two reasons:
    a reader can see exactly where a line ends, and `KeepTogether` can be applied so
    the value never lands on a different page from its label — which is what happened
    to the manifest digest before this, and a digest with its label on the previous
    page is genuinely harder to check.
    """
    if len(value) <= HEX_CHUNK:
        return HEX_STYLE.format(_esc(value))
    lines = [value[i : i + HEX_CHUNK] for i in range(0, len(value), HEX_CHUNK)]
    return "<br/>".join(HEX_STYLE.format(_esc(line)) for line in lines)


def _table(rows: list[list[Any]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.25, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [PAPER, BAND]),
            ]
        )
    )
    return table


def _dt(value: dt.datetime | None, *, fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """A timestamp, or an honest "unknown".

    Not defensive padding. A report that cannot be produced at all is worse than one
    that says a time is unknown: a missing timestamp is itself a finding about the
    record, and silently crashing tells the reader nothing. The database sets these
    with a server default, so this path should be unreachable — which is exactly why
    it needs saying rather than assuming.
    """
    if value is None:
        return "unknown"
    return value.strftime(fmt)


def _score_phrase(score: float, threshold: float, not_applicable: bool) -> str:
    """A score's reading, in words.

    "cannot be measured" is a separate branch and not a formatting nicety: an
    analyzer that declined to measure has produced *no* number, and rendering it as
    `0.0000` next to a threshold is the exact substitution this project exists to
    prevent. The database carries `not_applicable` for this and it is honoured here.
    """
    if not_applicable:
        return "could not be measured — this technique does not apply to the file"
    if score >= threshold:
        return f"{score:.4f}, at or above the threshold of {threshold:g} — investigate"
    return f"{score:.4f}, below this module's threshold of {threshold:g}"


def build_pdf(data: ReportData, signer: SignerInfo | None) -> bytes:
    """Render the report. `signer` None renders an unsigned report for testing.

    Callers in the API never pass `None` — an unsigned report is refused at the
    endpoint. The parameter exists so the *layout* can be tested without a key, and
    the cover says plainly which of the two it is rather than looking the same.
    """
    import io

    s = _styles()
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"Case report {data.case.number}",
        author=GENERATOR,
        subject="Forensic case report",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="body")

    def decorate(canvas: Any, doc_: BaseDocTemplate) -> None:
        """Header and footer on every page.

        The footer repeats the case number and the signature status rather than a
        page count alone: a page separated from the report still needs to say what
        it belongs to and whether anyone vouched for it.
        """
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc_.leftMargin, A4[1] - 13 * mm, f"Case {data.case.number}")
        status = f"signature: {_esc(signer.subject)}" if signer else "UNSIGNED"
        canvas.drawRightString(A4[0] - doc_.rightMargin, A4[1] - 13 * mm, status[:90])
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.4)
        canvas.line(doc_.leftMargin, A4[1] - 15 * mm, A4[0] - doc_.rightMargin, A4[1] - 15 * mm)
        canvas.line(doc_.leftMargin, 14 * mm, A4[0] - doc_.rightMargin, 14 * mm)
        canvas.drawString(doc_.leftMargin, 10 * mm, f"{GENERATOR} · {data.case.number}")
        canvas.drawRightString(A4[0] - doc_.rightMargin, 10 * mm, f"page {doc_.page}")
        canvas.restoreState()

    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=decorate)])

    story: list[Any] = []
    story += _cover(data, signer, s)
    story += _findings(data, s)
    if data.lab_results:
        story += _lab_results(data, s)
    story += _evidence_manifest(data, s)
    story += _analyses(data, s)
    story += _provenance(data, s)

    doc.build(story)
    return buffer.getvalue()


# --- cover ----------------------------------------------------------------


def _cover(data: ReportData, signer: SignerInfo | None, s: dict[str, ParagraphStyle]) -> list[Any]:
    case = data.case
    counts = summary_counts(data)
    generated = _dt(dt.datetime.now(dt.UTC))
    out: list[Any] = [
        Paragraph("Case report", s["title"]),
        Paragraph(
            f"{_esc(case.number)} &middot; generated {_esc(generated)}",
            s["subtitle"],
        ),
        Paragraph(f"<b>{_esc(case.title)}</b>", s["h1"]),
    ]

    if case.description:
        out.append(Paragraph(_esc(case.description), s["body"]))

    rows: list[list[Any]] = [
        [Paragraph("Field", s["cell_head"]), Paragraph("Value", s["cell_head"])],
        [Paragraph("Case number", s["cell"]), Paragraph(_esc(case.number), s["cell"])],
        [Paragraph("Status", s["cell"]), Paragraph(_esc(case.status), s["cell"])],
        [Paragraph("Priority", s["cell"]), Paragraph(_esc(case.priority), s["cell"])],
        [
            Paragraph("Opened", s["cell"]),
            Paragraph(_dt(case.created_at), s["cell"]),
        ],
        [
            Paragraph("Evidence items", s["cell"]),
            Paragraph(str(counts["evidence"]), s["cell"]),
        ],
        [
            Paragraph("Findings", s["cell"]),
            Paragraph(
                f"{counts['findings']} "
                f"({counts['sourced_findings']} sourced, "
                f"{counts['unsourced_findings']} not sourced)",
                s["cell"],
            ),
        ],
        [
            Paragraph("Analyzer results", s["cell"]),
            Paragraph(str(counts["analyses"]), s["cell"]),
        ],
    ]
    if counts["lab_results"]:
        rows.append(
            [
                Paragraph("Attached lab results", s["cell"]),
                Paragraph(str(counts["lab_results"]), s["cell"]),
            ]
        )
    out += [_table(rows, [42 * mm, 118 * mm]), Spacer(1, 8)]

    out += [
        Paragraph("What this report is", s["h2"]),
        Paragraph(
            "This report lists automated signals computed from the evidence on this "
            "case, together with the evidence digests they were computed from. It "
            "records what the system measured and what it could not. "
            "<b>It is not a determination of authenticity, authorship or identity</b>, "
            "and no signal in it should be read as one. Every score carries the "
            "threshold it was compared against, the analyzer version that produced "
            "it, and that analyzer's own statement of what it can support.",
            s["body"],
        ),
    ]

    unsourced = counts["unsourced_findings"]
    if unsourced:
        total = counts["findings"]
        # Pluralised deliberately: "1 of the 3 findings ... rest ... are marked" is
        # the kind of thing that makes a legal document read carelessly.
        plural = unsourced != 1
        out += [
            Spacer(1, 6),
            Paragraph(
                f"{unsourced} of the {total} finding{'' if total == 1 else 's'} on "
                f"this case "
                + ("rests" if not plural else "rest")
                + " on no analyzer result and "
                + ("is" if not plural else "are")
                + " marked NOT SOURCED below. "
                + (
                    "It is recorded here because a case record is not ours to prune, "
                    "and it is separated from sourced findings so that nothing in its "
                    "layout implies analysis supports it."
                    if not plural
                    else "They are recorded here because a case record is not ours to "
                    "prune, and they are separated from sourced findings so that "
                    "nothing in their layout implies analysis supports them."
                ),
                s["alert"],
            ),
        ]

    # Extended rather than appended: `_signature_block` returns a list, and putting
    # that list in as a *single* story element is a nested list, which ReportLab
    # accepts silently and then trips over when it tries to lay it out.
    out += [Spacer(1, 10)]
    out += _signature_block(data, signer, s)
    out.append(PageBreak())
    return out


def _signature_block(
    data: ReportData, signer: SignerInfo | None, s: dict[str, ParagraphStyle]
) -> list[Any]:
    """The signature statement, including what it does not prove."""
    if signer is None:
        return [
            Paragraph("Signature: NONE", s["h2"]),
            Paragraph(
                "<b>This document is not signed.</b> Its content can be altered "
                "without detection. Do not rely on it as a record.",
                s["alert"],
            ),
        ]

    out: list[Any] = [
        Paragraph("Integrity", s["h2"]),
        Paragraph(
            "This document carries a PAdES baseline-B signature over the whole file. "
            "Any change to any byte after signing will be detected by verification: "
            "the signature covers the entire document, not a page or a section of it.",
            s["body"],
        ),
        Table(
            [
                [
                    Paragraph("Signed by", s["cell_head"]),
                    Paragraph(_esc(signer.subject), s["cell"]),
                ],
                [
                    Paragraph("Certificate SHA-256", s["cell_head"]),
                    Paragraph(_wrap_hash(signer.fingerprint), s["cell"]),
                ],
                [
                    Paragraph("Case manifest SHA-256", s["cell_head"]),
                    Paragraph(_wrap_hash(data.manifest_digest), s["cell"]),
                ],
            ],
            colWidths=[42 * mm, 118 * mm],
            hAlign="LEFT",
            style=TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -2), 0.25, RULE),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            ),
        ),
        Spacer(1, 6),
        Paragraph("What the signature does not establish", s["h2"]),
        Paragraph(
            "It does not independently establish <b>when</b> the document was "
            "signed. The signature carries a signing time, but that is the signing "
            "machine's own statement of its own clock: no timestamp authority "
            "counter-signed it, so it is what the signer says rather than proof. A "
            "machine whose clock was wrong would report a wrong time and this "
            "signature would still verify. It does not establish <b>who</b> signed it "
            "to anyone who has not been given this certificate as a trust root — "
            "self-signed is the normal deployment here, so until you trust the "
            "certificate above you know the bytes are unchanged but not who vouched "
            "for them. Compare the certificate SHA-256 with the one your lab holds.",
            s["small"],
        ),
    ]
    return out


# --- findings -------------------------------------------------------------


def _findings(data: ReportData, s: dict[str, ParagraphStyle]) -> list[Any]:
    out: list[Any] = [Paragraph("Findings", s["h1"])]
    if not data.findings:
        out.append(
            Paragraph(
                "No findings have been recorded on this case. That is not a "
                "statement that the evidence is unaltered — it means nobody has "
                "written anything down.",
                s["body"],
            )
        )
        return out

    for index, finding in enumerate(data.findings, start=1):
        block: list[Any] = [
            Paragraph(f"{index}. {_esc(finding.title)}", s["h2"]),
            Paragraph(
                f"status {_esc(finding.status)} &middot; recorded by "
                f"{_esc(finding.operator)} on {_dt(finding.created_at)}",
                s["small"],
            ),
        ]
        if finding.body:
            block.append(Paragraph(_esc(finding.body), s["body"]))

        # Lab results are not this system's findings, so they are not counted here.
        # They are set apart and pointed at their own section.
        analytic = [c for c in finding.citations if not c.is_lab_result]
        lab = [c for c in finding.citations if c.is_lab_result]

        if not finding.citations:
            block += [
                Paragraph(
                    "NOT SOURCED &mdash; this finding rests on no analyzer result. "
                    f"It is an assertion by {_esc(finding.operator)}, recorded for "
                    "completeness of the record. Nothing in this system supports or "
                    "contradicts it, and no analysis was run to check it.",
                    s["alert"],
                )
            ]
        else:
            block.append(Paragraph("Source", s["h2"]))
            block.append(_citation_table(analytic, s))
            if lab:
                block.append(
                    Paragraph(
                        f"This finding also rests on {len(lab)} attached laboratory "
                        f"result(s), listed under <i>Attached laboratory results</i>. "
                        f"Those conclusions come from an accredited laboratory, not "
                        f"from this system.",
                        s["small"],
                    )
                )
        out.append(KeepTogether(block))
        out.append(Spacer(1, 8))
    return out


def _citation_table(citations: list[Any], s: dict[str, ParagraphStyle]) -> Table:
    """The provenance of one finding, in the order a reader needs it.

    Analyzer, then what it measured and against what, then which file, then that
    file's digest, then the analyzer's version and its own limitation. A reader who
    wants to check the claim has everything, and in the order they check it.
    """
    rows: list[list[Any]] = [
        [
            Paragraph(h, s["cell_head"])
            for h in ("Analyzer", "Result", "Evidence", "SHA-256 of evidence", "Analyzer version")
        ]
    ]
    for c in citations:
        rows.append(
            [
                Paragraph(f"{_esc(c.module)}<br/>Tier {_esc(c.tier)}", s["cell"]),
                Paragraph(
                    f"<b>{_esc(_score_phrase(c.score, c.threshold, c.not_applicable))}</b>"
                    + (f"<br/>{_esc(c.disclaimer)}" if c.disclaimer else ""),
                    s["cell"],
                ),
                Paragraph(_esc(c.filename), s["cell"]),
                Paragraph(_wrap_hash(c.evidence_sha256), s["cell"]),
                Paragraph(_esc(c.model_version), s["cell"]),
            ]
        )
    widths = [24 * mm, 58 * mm, 30 * mm, 34 * mm, 22 * mm]
    return _table(rows, widths)


# --- lab results ----------------------------------------------------------


def _lab_results(data: ReportData, s: dict[str, ParagraphStyle]) -> list[Any]:
    """Attached laboratory results, kept out of the findings list.

    Tier C is not produced by any analyzer here — DESIGN_PLAN cuts DNA, toxicology,
    pollen, entomology, odor, explosives and arson chemistry as AI analyzers because
    none can be done for real from a web upload. A result in this tier is an external
    document attached to the case. Presenting one inside the findings list would
    transfer authorship of that conclusion to this system, which is precisely the
    misrepresentation the tiering exists to prevent.
    """
    out: list[Any] = [
        Paragraph("Attached laboratory results", s["h1"]),
        Paragraph(
            "The conclusions below are those of an external accredited laboratory, "
            "attached to this case as results. <b>They are not this system's "
            "findings and were not computed here.</b> They are reproduced so the case "
            "record is complete, and they are attributed to the laboratory that "
            "issued them.",
            s["body"],
        ),
    ]
    rows: list[list[Any]] = [
        [
            Paragraph(h, s["cell_head"])
            for h in ("Source document", "Subject", "Result", "Attached", "Recorded")
        ]
    ]
    for c in data.lab_results:
        rows.append(
            [
                Paragraph(_esc(c.filename), s["cell"]),
                Paragraph(_esc(c.evidence_sha256[:16]) + "&hellip;", s["cell"]),
                Paragraph(
                    f"<b>{_esc(_score_phrase(c.score, c.threshold, c.not_applicable))}</b>"
                    + (f"<br/>{_esc(c.disclaimer)}" if c.disclaimer else ""),
                    s["cell"],
                ),
                Paragraph(_dt(c.cited_at), s["cell"]),
                Paragraph(_esc(c.module), s["cell"]),
            ]
        )
    out.append(_table(rows, [34 * mm, 26 * mm, 62 * mm, 26 * mm, 22 * mm]))
    return out


# --- evidence manifest ----------------------------------------------------


def _evidence_manifest(data: ReportData, s: dict[str, ParagraphStyle]) -> list[Any]:
    out: list[Any] = [
        Paragraph("Evidence manifest", s["h1"]),
        Paragraph(
            "Every file on this case with its SHA-256 as recorded at intake. The "
            "manifest digest below is computed over all of these rows, so adding or "
            "removing a file changes it. It covers <i>all</i> evidence on the case, "
            "not only the items a finding cites: a digest over the cited subset "
            "would let an uncited file be added without the manifest changing.",
            s["body"],
        ),
    ]
    rows: list[list[Any]] = [
        [
            Paragraph(h, s["cell_head"])
            for h in ("File", "SHA-256", "Bytes", "Type", "Intake", "Cited")
        ]
    ]
    for row in sorted(data.manifest, key=lambda r: r.filename):
        rows.append(
            [
                Paragraph(_esc(row.filename), s["cell"]),
                Paragraph(_wrap_hash(row.sha256), s["cell"]),
                Paragraph(f"{row.size_bytes:,}", s["cell"]),
                Paragraph(_esc(row.content_type), s["cell"]),
                Paragraph(_dt(row.uploaded_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
                Paragraph("yes" if row.cited else "&mdash;", s["cell"]),
            ]
        )
    out.append(_table(rows, [34 * mm, 40 * mm, 18 * mm, 24 * mm, 26 * mm, 12 * mm]))
    out.append(Spacer(1, 6))
    out.append(
        KeepTogether(
            [
                Paragraph("Manifest digest (SHA-256 over all rows)", s["h2"]),
                Paragraph(_wrap_hash(data.manifest_digest), s["body"]),
                Paragraph(
                    "Recomputable by anyone: the digest is taken over a canonical "
                    "JSON serialisation of the rows above, sorted by filename then "
                    f"digest, with no whitespace. It is printed as {HEX_CHUNK} "
                    "characters per line — join the lines to compare it against your "
                    "own. A reader who disagrees with a row can prove it by "
                    "recomputing rather than taking this report's word.",
                    s["small"],
                ),
            ]
        )
    )
    out.append(PageBreak())
    return out


# --- analyses -------------------------------------------------------------


def _analyses(data: ReportData, s: dict[str, ParagraphStyle]) -> list[Any]:
    out: list[Any] = [
        Paragraph("Analyzer results", s["h1"]),
        Paragraph(
            "Every result on this case, including those no finding cites — a result "
            "that was computed and not used is part of the record. Each carries its "
            "own threshold and the analyzer's own statement of what it can support.",
            s["body"],
        ),
    ]
    if not data.results:
        out.append(
            Paragraph(
                "No analysis has been run on this case. No measurement has been made "
                "and none can be inferred.",
                s["body"],
            )
        )
        return out

    rows: list[list[Any]] = [
        [
            Paragraph(h, s["cell_head"])
            for h in ("Analyzer", "Result", "Threshold", "Tier", "Version", "Computed")
        ]
    ]
    for r in sorted(data.results, key=lambda r: (r.module, r.created_at)):
        not_applicable = str(r.payload.get("status", "")) == "not_applicable"
        rows.append(
            [
                Paragraph(_esc(r.module), s["cell"]),
                Paragraph(
                    f"<b>{_esc(_score_phrase(r.score, r.threshold, not_applicable))}</b>"
                    + (f"<br/>{_esc(r.disclaimer)}" if r.disclaimer else ""),
                    s["cell"],
                ),
                Paragraph(f"{r.threshold:g}", s["cell"]),
                Paragraph(_esc(r.tier), s["cell"]),
                Paragraph(_esc(r.model_version), s["cell"]),
                Paragraph(_dt(r.created_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
            ]
        )
    out.append(_table(rows, [24 * mm, 66 * mm, 18 * mm, 12 * mm, 26 * mm, 22 * mm]))
    return out


# --- provenance appendix --------------------------------------------------


def _provenance(data: ReportData, s: dict[str, ParagraphStyle]) -> list[Any]:
    """Who did what: chain of custody, agent runs, and human approvals."""
    out: list[Any] = [
        Paragraph("Provenance appendix", s["h1"]),
        Paragraph(
            "The record of how this case reached its current state. It exists so a "
            "reader can tell the difference between what a person concluded and what "
            "a machine suggested, and between a change that was authorised and one "
            "that was merely possible.",
            s["body"],
        ),
    ]

    out.append(Paragraph("Chain of custody", s["h2"]))
    if data.custody:
        names = {str(e.id): e.filename for e in data.evidence}
        rows: list[list[Any]] = [
            [Paragraph(h, s["cell_head"]) for h in ("When (UTC)", "By", "File", "Action", "Detail")]
        ]
        for row in data.custody:
            rows.append(
                [
                    Paragraph(_dt(row.created_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
                    # A chain of custody records who handled the item. A timestamp with
                    # no name beside it is a log, not a chain — it says when, not who
                    # was accountable.
                    Paragraph(_esc(data.custody_operators.get(str(row.id), "unknown")), s["cell"]),
                    Paragraph(_esc(names.get(str(row.evidence_id), "&mdash;")), s["cell"]),
                    Paragraph(_esc(row.action), s["cell"]),
                    Paragraph(_esc(row.detail), s["cell"]),
                ]
            )
        out.append(_table(rows, [24 * mm, 30 * mm, 26 * mm, 26 * mm, 54 * mm]))
    else:
        out.append(Paragraph("No custody entries recorded.", s["body"]))

    out.append(Paragraph("Automated runs", s["h2"]))
    if data.runs:
        rows = [
            [
                Paragraph(h, s["cell_head"])
                for h in ("Objective", "Status", "Step", "Model", "Started")
            ]
        ]
        for run in data.runs:
            rows.append(
                [
                    Paragraph(_esc(run.objective), s["cell"]),
                    Paragraph(_esc(run.status), s["cell"]),
                    Paragraph(_esc(run.step), s["cell"]),
                    Paragraph(_esc(run.model), s["cell"]),
                    Paragraph(_dt(run.created_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
                ]
            )
        out.append(_table(rows, [58 * mm, 26 * mm, 20 * mm, 24 * mm, 30 * mm]))
    else:
        out.append(Paragraph("No automated runs on this case.", s["body"]))

    approved = data.human_approved_calls
    out.append(Paragraph("Actions authorised by a person", s["h2"]))
    if approved:
        out.append(
            Paragraph(
                "These changes were proposed by the automated agent and applied only "
                "after a person approved them. The arguments are hashed so the record "
                "binds to what was approved rather than to a description of it.",
                s["body"],
            )
        )
        # The audit chain is the source for *who* approved; the tool-call row is the
        # source for the result hash. Neither alone carries both, so the two are
        # joined on the action id.
        by_action = {str(c.approved_action_id): c for c in approved}
        rows = [
            [
                Paragraph(h, s["cell_head"])
                for h in ("Approved by", "When", "Tool", "Arguments (SHA-256)", "Result (SHA-256)")
            ]
        ]
        for view in data.approvals or []:
            call = by_action.get(view.action_id)
            tool = view.tool or (call.tool if call else "")
            args_hash = view.args_hash or (call.args_hash if call else "")
            rows.append(
                [
                    Paragraph(_esc(view.operator), s["cell"]),
                    Paragraph(_dt(view.approved_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
                    Paragraph(_esc(tool), s["cell"]),
                    Paragraph(_wrap_hash(args_hash), s["cell"]),
                    Paragraph(
                        _wrap_hash(call.result_hash) if call and call.result_hash else "&mdash;",
                        s["cell"],
                    ),
                ]
            )
        out.append(_table(rows, [34 * mm, 24 * mm, 30 * mm, 42 * mm, 34 * mm]))
    else:
        out.append(
            Paragraph(
                "No automated action on this case required a person's approval. Had "
                "one been proposed and approved, it would appear here with the "
                "person who approved it.",
                s["body"],
            )
        )

    out.append(Paragraph("Tool calls", s["h2"]))
    if data.tool_calls:
        rows = [
            [
                Paragraph(h, s["cell_head"])
                for h in ("Tool", "Outcome", "Arguments (SHA-256)", "When")
            ]
        ]
        for call in data.tool_calls:
            outcome = "ok" if call.ok else "failed"
            if call.approved_action_id:
                outcome = "ok, human-approved"
            elif call.error:
                outcome = f"failed: {_esc(call.error[:80])}"
            rows.append(
                [
                    Paragraph(_esc(call.tool), s["cell"]),
                    Paragraph(outcome, s["cell"]),
                    Paragraph(_wrap_hash(call.args_hash), s["cell"]),
                    Paragraph(_dt(call.created_at, fmt="%Y-%m-%d %H:%M"), s["cell"]),
                ]
            )
        out.append(_table(rows, [34 * mm, 56 * mm, 46 * mm, 24 * mm]))
    else:
        out.append(Paragraph("No tool calls recorded.", s["body"]))

    out += [
        Spacer(1, 12),
        Paragraph(
            f"End of report. Case {_esc(data.case.number)}, generated by "
            f"{_esc(GENERATOR)}. The manifest digest above identifies the evidence "
            f"set this report describes.",
            s["small"],
        ),
    ]
    return out
