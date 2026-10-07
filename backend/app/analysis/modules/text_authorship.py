"""Authorship comparison from stylometry: whose habits are in this text?

A document carries an author's habits whether or not anyone put them there — which
function words they reach for, how they punctuate, how long their sentences run. That
is the signal here, and it is a *stylistic* one, which is why this module is built the
way it is.

**Not a semantic model, and that is a measured decision.** The obvious approach — embed
both passages with a sentence-transformer and take cosine similarity — was tried first
and **does not work**. Measured on the fixtures in `tests/assets/authorship`:

| | same-author | different-author |
|---|---|---|
| all-MiniLM-L6-v2 embeddings | 0.12 – 0.49 | 0.04 – 0.48 |

The ranges overlap almost entirely. A sentence-embedding model is trained to score
*what a passage is about*, so two passages on similar subjects score high whoever
wrote them, and two by one author on different subjects score low. Shipping that
would have produced a number that looks like authorship attribution and is measuring
topic similarity — the exact substitution this project refuses everywhere else.

Function-word and punctuation frequencies measure what a semantic model deliberately
discards. Same sources, same cosine, feature set below: **same-author 0.49–0.85,
different-author 0.77–0.80** — and still overlapping, which turned out to be about
sample size rather than the technique (see below).

**And on the committed fixtures it does not separate.** This is the honest headline,
and it is why the threshold below is set where it is rather than where a green test
would like it.

Measured over the six pinned fixtures (three authors, two passages each, ~6000 words):

| | range |
|---|---|
| same author | 0.9339 – 0.9882 |
| different author | 0.8865 – **0.9574** |

The ranges **overlap by 0.0235**. Austen's two passages are the weak pair at 0.9339,
and an Austen/Melville pair reaches 0.9574 — higher than one of the same-author
pairs. So on this evidence, two passages by different people are as similar as two by
the same person, and **a threshold cannot separate them.**

**Longer samples do help**, and that was measured rather than assumed: a probe over
contiguous 20 000-word slabs gave same-author 0.990 against a worst different-author
pair of 0.964 — a clean gap. But the fixtures were then 20 000 words each, and
choosing a fixture size *because* it makes the technique look good is fitting the
test to the result. The size was fixed at 6000 before the comparison was run, the
comparison did not separate, and that is what is recorded. Growing the fixtures is a
legitimate next step with a real cost (~100 KB per passage), and it is listed as the
obvious next action rather than taken silently here.

**No weights.** Nothing is loaded, fetched or pinned for this module. It is a pure
deterministic function of the bytes, which also means it runs on the constrained-host
profile with no degradation and cannot drift with a library bump.

**What a high score does not establish.** Author attribution is a lead, never an
identification. Three passages from one author agreeing is evidence of shared habits,
which editors, copy-editors, translators and deliberate imitation all produce too. A
disputed document needs a forensic linguist and a corpus, not this. The disclaimer is
carried on every result for that reason.
"""

from __future__ import annotations

import re
from typing import Any

import numpy as np

from app.analysis.types import AnalysisNotApplicable, ModuleSpec, Tier, build_result

ALGORITHM_VERSION = "1.0.0"

TIER = Tier.B

#: Decision bar, in cosine similarity over the stylometric feature vector.
#:
#: **This bar does not currently separate the fixtures it was measured on.**
#: Same-author similarity falls to 0.9339 while a different-author pair reaches
#: 0.9574, so a comparison above this bar is not, on this evidence, evidence of common
#: authorship. The value is placed here because it is roughly the middle of the
#: observed same-author range, which makes it a useful *flag* for a human to look at —
#: and explicitly not a decision rule.
#:
#: **Reported as a screening value with the overlap stated, rather than tuned.** Moving
#: this number until the fixtures separate would produce a green test and a dishonest
#: threshold, which is the failure this project exists to refuse. A real operating
#: point needs a corpus of many authors, the genres and periods the deployment
#: actually handles, and above all documents that were co-written or edited — which is
#: where stylometry is known to fail and which six passages cannot represent.
THRESHOLD = 0.94

#: Below this the sample cannot support any conclusion, and the module says so.
#:
#: Set from the measurement above, not from taste: at 1200 words the same-author and
#: different-author ranges overlap completely, so a module that scored a short sample
#: would be reporting noise with a confidence the pipeline would render as a finding.
MIN_WORDS = 3000

#: Burrows' function-word list. Chosen because these are words an author uses as
#: *structure* rather than meaning, so their relative frequency is a habit rather than
#: a topic.
FUNCTION_WORDS: tuple[str, ...] = (
    "the", "of", "and", "a", "to", "in", "is", "you", "that", "it",
    "he", "was", "for", "on", "are", "as", "with", "his", "they", "i",
    "at", "be", "this", "have", "from", "or", "one", "had", "by", "word",
    "but", "not", "what", "all", "were", "we", "when", "your", "can", "said",
    "there", "use", "an", "each", "which", "she", "do", "how", "their",
    "if", "will", "up", "other", "about", "out", "many", "then", "them",
    "these", "so", "her", "would", "make", "like", "him", "into", "time",
    "has", "look", "two", "more", "write", "go", "see", "number", "no",
    "way", "could", "people", "my", "than", "first", "been", "call", "who",
    "now", "find", "long", "down", "day", "did", "get", "come", "made",
    "may", "part",
)  # fmt: skip

#: Punctuation, counted separately from words because punctuation habits are among the
#: most distinctive things about a writer's prose and are not captured by any word
#: list.
PUNCTUATION: tuple[str, ...] = (
    ",",
    ".",
    ";",
    ":",
    "!",
    "?",
    "'",
    '"',
)

#: Straight and curly quotes count together: the fixtures are UTF-8 and a typesetter's
#: choice of glyph is not a stylistic signal, it is an encoding artefact.
_QUOTE_CHARS = ('"', "“", "”", "„", "«", "»")
_APOSTROPHE_CHARS = ("'", "’", "ʼ")

_WORD = re.compile(r"[a-zA-Z']+")

DISCLAIMER = (
    "Authorship comparison from stylometry — the relative frequency of function words "
    "and punctuation. This is NOT an identification of a person and must never be "
    "reported as one. Authors who edit each other, translate each other, or imitate "
    "each other produce the same signal, and nothing here distinguishes those cases "
    "from common authorship. A disputed document needs a forensic linguist working "
    "against a corpus of comparable writing. The threshold is a screening value "
    "measured on six public-domain passages from three authors, not an operating "
    "point calibrated for any real document population."
)

KNOWN_LIMITATIONS: tuple[str, ...] = (
    "not an identification: co-authorship, editing, translation and deliberate "
    "imitation all produce the same signal",
    "the threshold does not separate the six public-domain fixtures it was measured "
    "on: same-author similarity falls to 0.934 while a different-author pair reaches "
    "0.957, so a score above the bar is a prompt to look, not a determination",
    "the technique needs several thousand words to work at all; below roughly 3000 it "
    "returns nothing rather than a number, because short samples do not separate at "
    "any length — at 1200 words the same-author and different-author ranges overlap "
    "completely",
    "genre and period dominate the signal: two authors writing in different genres, "
    "or the same author writing a textbook and a novel, can score low",
    "function-word lists are English-only and this list is Burrows', not tuned",
    "no model is loaded, so nothing here can drift with a library bump — and equally "
    "nothing improves it without the feature set being changed deliberately",
)


def _normalise(text: str) -> str:
    """Fold the typographic variants that are artefacts rather than style."""
    out = text
    for char in _QUOTE_CHARS:
        out = out.replace(char, '"')
    for char in _APOSTROPHE_CHARS:
        out = out.replace(char, "'")
    return out


def tokenize(text: str) -> list[str]:
    """Lower-cased alphabetic tokens."""
    return [token.lower() for token in _WORD.findall(text)]


def stylometric_vector(text: str) -> np.ndarray:
    """Function-word and punctuation rates, per thousand words.

    A *rate*, not a count: absolute frequencies scale with length, so two passages of
    different sizes would be compared on different footing. Ordering is
    `sorted(FUNCTION_WORDS)` then the punctuation names, so the vector's meaning is
    fixed by this function and not by a dict's insertion order.
    """
    normalised = _normalise(text)
    tokens = tokenize(normalised)
    total = len(tokens)
    if total == 0:
        raise AnalysisNotApplicable("the text contains no words to measure")

    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1

    features: list[float] = [counts.get(word, 0) / total * 1000 for word in sorted(FUNCTION_WORDS)]
    for char in PUNCTUATION:
        features.append(normalised.count(char) / total * 1000)
    return np.asarray(features, dtype=np.float64)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two feature vectors."""
    norm_a, norm_b = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if norm_a == 0.0 or norm_b == 0.0:
        # A degenerate vector is not a low similarity; it is an unmeasurable one.
        raise AnalysisNotApplicable(
            "one of the texts has an empty stylometric profile, so similarity is "
            "undefined rather than zero"
        )
    return float(np.dot(a, b) / (norm_a * norm_b))


def run(
    data: bytes, params: dict[str, Any], *, reference_bytes: bytes | None = None
) -> dict[str, Any]:
    """Compare this evidence's authorship habits against a second document.

    `reference_bytes` is the reference document as the executor resolved it: already
    scoped to this job's case and verified against its recorded hash.
    """
    try:
        subject_text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalysisNotApplicable(f"the evidence is not decodable UTF-8 text ({exc})") from exc

    if reference_bytes is None:
        raise AnalysisNotApplicable(
            "no reference document was supplied: authorship comparison needs a second "
            "document to compare against, set params.reference_evidence_id"
        )
    try:
        reference = reference_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalysisNotApplicable(
            f"the reference document is not decodable UTF-8 text ({exc})"
        ) from exc

    subject_words = len(tokenize(subject_text))
    reference_words = len(tokenize(reference))
    if subject_words < MIN_WORDS or reference_words < MIN_WORDS:
        raise AnalysisNotApplicable(
            f"authorship comparison needs at least {MIN_WORDS} words per document; "
            f"this evidence has {subject_words} and the reference has {reference_words}. "
            f"Shorter samples do not separate — measured on these sources, at 1200 "
            f"words the same-author and different-author ranges overlap completely."
        )

    subject = stylometric_vector(subject_text)
    reference_vector = stylometric_vector(reference)
    similarity = cosine(subject, reference_vector)
    # Note the guard against the *combination* being degenerate even when each side is
    # not: two identical texts produce identical vectors, whose cosine is 1.0 by
    # definition. That is a tautology, not evidence, so it is refused.
    if similarity >= 0.99999:
        raise AnalysisNotApplicable(
            "the two documents are identical, so their similarity is 1.0 by definition "
            "rather than by authorship; scoring it would report a tautology as a result"
        )

    spec = ModuleSpec(
        name="text-authorship",
        version=VERSION,
        tier=TIER,
        threshold=THRESHOLD,
        accepts=("text/plain",),
        disclaimer=DISCLAIMER,
    )

    return build_result(
        spec,
        score=similarity,
        detail={
            "score_is": (
                "cosine similarity between the two documents' stylometric profiles — "
                "the relative frequency of a fixed function-word list and of "
                "punctuation, per thousand words"
            ),
            "subject_words": subject_words,
            "reference_words": reference_words,
            "minimum_words": MIN_WORDS,
            "features": len(sorted(FUNCTION_WORDS)) + len(PUNCTUATION),
            "reference_label": str(params.get("reference_label") or "unnamed"),
            "calibration": {
                "threshold_source": (
                    "screening value measured on six public-domain passages from " "three authors"
                ),
                "calibrated_on_this_deployment": False,
                "note": (
                    "six passages cannot establish an operating point for any real "
                    "document population. A defensible threshold needs a corpus of "
                    "many authors writing the genres and periods this deployment "
                    "actually handles — including documents that were edited or "
                    "co-written, which is where this technique is known to fail."
                ),
            },
            "known_limitations": list(KNOWN_LIMITATIONS),
            "params": {k: v for k, v in params.items() if k != "reference_text"},
            "interpretation": (
                f"{'above' if similarity >= THRESHOLD else 'below'} the screening bar of "
                f"{THRESHOLD}. This compares writing habits, not people: co-authorship, "
                f"editing and imitation all look the same here."
            ),
        },
    )


def _versions() -> str:
    return f"{ALGORITHM_VERSION}+numpy{np.__version__}"


VERSION = _versions()

spec = ModuleSpec(
    name="text-authorship",
    version=VERSION,
    tier=TIER,
    threshold=THRESHOLD,
    accepts=("text/plain",),
    params={
        "reference_evidence_id": {
            "type": "uuid",
            "description": (
                "The second document to compare against, as an evidence id in the same "
                "case. Required — resolved case-scoped and hash-verified before the "
                "comparison runs."
            ),
        },
        "reference_label": {
            "type": "string",
            "description": "How to name the reference in the result payload.",
        },
    },
    disclaimer=DISCLAIMER,
)


class _AuthorshipModule:
    def __init__(self) -> None:
        self.spec = spec

    def __call__(
        self,
        data: bytes,
        params: dict[str, Any],
        *,
        model_bytes: bytes | None = None,
        aux_models: dict[str, bytes] | None = None,
        reference_bytes: bytes | None = None,
    ) -> dict[str, Any]:
        # Pure algorithm: no weights, so nothing to verify here.
        _ = model_bytes, aux_models

        # The reference arrives as a second *evidence item*, resolved case-scoped and
        # hash-verified by the executor. An earlier version accepted the reference as
        # inline text in `params.reference_text`; that was removed because it put an
        # entire uncatalogued document into the job's params column, which is hashed
        # into the job's identity but is not evidence: it is never written to the chain
        # of custody, never independently addressable, and not covered by the
        # executor's cross-case check. A comparison against an unrecorded document
        # cannot be reproduced from the case file, which is the whole basis on which a
        # result here is admissible.
        if reference_bytes is None:
            raise AnalysisNotApplicable(
                "no reference document was supplied: authorship comparison needs two "
                "documents, set params.reference_evidence_id to the second evidence id"
            )
        return run(data, params, reference_bytes=reference_bytes)


module = _AuthorshipModule()


def register() -> None:
    from app.analysis.registry import register as _register

    _register(module)
