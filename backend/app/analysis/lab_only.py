"""Tier-C techniques: the ones this platform refuses to score.

Every other tier here exists to bound how much a number can be believed. Tier C is
the opposite — there is no number, and the design goal is that **nothing in this
codebase can produce one**.

DNA profiling, toxicology, pollen and insect identification, ignitable-liquid
chemistry and accelerant residue are all analytical-forensic disciplines with
published standards (ISO 17025 accreditation, validated methods, peer-reviewed
chemistry behind them). They are also exactly the disciplines where a plausible
looking score from a general-purpose model is most dangerous, because the output
looks like data and lands in a report next to real findings.

So Tier C is not an analyzer that scores conservatively. It is a **catalog entry
with no scoring path at all**: the technique is named so an examiner can find it and
know it is out of scope, and asking the system to score one is refused with an
explanation rather than queued.

The distinction from `unsupported` matters. `unsupported` means *this host cannot
run a real module* — provision the weights and it works, and the check exists in
`JobStatus.UNSUPPORTED`. Tier C means *there is no module, on any host, and
provisioning anything will not change that*. Reporting it as `unsupported` would
imply a fix exists. This refusal is checked before a job row is created, so no
job, no worker message and no result can exist for one — a test asserts exactly
that.

The supported path is the opposite of scoring: an accredited laboratory's signed
report is uploaded as ordinary evidence, hashed into the chain of custody, and
labelled with the technique it answers. ForenSight's job is to carry and
provenance-track that document, not to interpret it.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Refusal prefix. Stable so a caller can match on intent without parsing prose.
REFUSAL_PREFIX = "unsupported by design"


@dataclass(frozen=True)
class LabTechnique:
    """A discipline this platform deliberately does not attempt.

    `why` is written to be *shown to an examiner*, not to justify the decision to a
    developer. Someone who has just uploaded a blood sample and been told no will
    read this field, so it has to say what to do instead.
    """

    name: str
    #: What the discipline actually is, in one line.
    discipline: str
    #: Why there is no score here. Stated as fact about the technique, not as a
    #: statement about this implementation's limitations — "we could not" is a
    #: solvable complaint, "no validated basis exists" is not.
    why: str
    #: What to do instead. Every entry has one; an entry without an alternative
    #: is a dead end for the person reading it.
    instead: str


TECHNIQUES: tuple[LabTechnique, ...] = (
    LabTechnique(
        name="dna-profiling",
        discipline="DNA profiling and STR allele analysis",
        why=(
            "Individualisation from a DNA profile is a legal conclusion resting on "
            "validated chemistry, population statistics and a documented likelihood "
            "ratio. No model output can carry that weight, and a score here would be "
            "an invented number presented beside real allele data."
        ),
        instead=(
            "Submit the sample to an accredited laboratory and attach their signed "
            "report as evidence, labelled with this technique."
        ),
    ),
    LabTechnique(
        name="toxicology",
        discipline="Toxicology and drug identification",
        why=(
            "Substance identification and interpretation of a concentration against "
            "post-mortem and clinical ranges are analytical chemistry with "
            "case-specific clinical interpretation attached."
        ),
        instead=(
            "Obtain the laboratory's report. Interpretation of the finding for this "
            "case belongs with a toxicologist, not with this platform."
        ),
    ),
    LabTechnique(
        name="pollen",
        discipline="Pollen and palynology",
        why=(
            "Palynological identification depends on reference-collection expertise "
            "and, for forensic use, on contested admissibility of pollen evidence. "
            "It is not a classification problem a vision model can be trusted with."
        ),
        instead="Attach the palynologist's report and note the collection location.",
    ),
    LabTechnique(
        name="insect-identification",
        discipline="Entomological species identification",
        why=(
            "Post-mortem interval estimates from insect succession are a debated "
            "inference built on species-level taxonomy and environmental history. "
            "The value a model could add here is negative."
        ),
        instead="Attach the entomology report; treat any interval it gives as an estimate.",
    ),
    LabTechnique(
        name="ignitable-liquid-residue",
        discipline="Ignitable liquid residue and arson-origin chemistry",
        why=(
            "Classifying an accelerant from a fire debris extract is laboratory "
            "chemistry with an ASTM/EN standard behind it. A misclassification here "
            "implicates or exonerates a person."
        ),
        instead="Submit the debris to a laboratory and attach their report.",
    ),
    LabTechnique(
        name="explosive-residue",
        discipline="Explosive and ordnance residue identification",
        why=(
            "Residue identification in this field is destructive, instrument-specific "
            "analytical chemistry. There is no non-destructive input this platform "
            "could accept that would mean anything."
        ),
        instead="Submit to a laboratory accredited for explosives analysis.",
    ),
    LabTechnique(
        name="odor-profile",
        discipline="Canine and human scent-article profiling",
        why=(
            "Scent-article analysis has contested scientific standing and, for "
            "human scent specifically, is not a recognised forensic identification "
            "method. Presenting it as a scored result would misrepresent its status."
        ),
        instead="Record the article's provenance and handling in the chain of custody.",
    ),
)

BY_NAME: dict[str, LabTechnique] = {technique.name: technique for technique in TECHNIQUES}


def is_lab_only(module_name: str) -> bool:
    """Whether `module_name` names a Tier-C technique rather than an analyzer."""
    return module_name in BY_NAME


def refusal(module_name: str) -> str:
    """The explanation shown when someone tries to score a Tier-C technique.

    Raises `KeyError` for a name that is not a Tier-C technique, so a caller cannot
    accidentally hand an examiner a refusal for something that is merely absent —
    an unknown module is a different problem with a different fix.
    """
    technique = BY_NAME[module_name]
    return (
        f"{REFUSAL_PREFIX}: {module_name!r} is a Tier-C technique and is not "
        f"scored by this platform. {technique.discipline} — {technique.why} "
        f"Instead: {technique.instead}"
    )


def catalog() -> list[dict[str, str]]:
    """The Tier-C catalog, for the UI and the report.

    Every entry states what the technique is, why there is no score, and what to do
    instead. An examiner looking for DNA support and finding a bare "unsupported"
    would reasonably conclude the platform is broken.
    """
    return [
        {
            "name": technique.name,
            "discipline": technique.discipline,
            "why": technique.why,
            "instead": technique.instead,
        }
        for technique in TECHNIQUES
    ]


__all__ = [
    "BY_NAME",
    "REFUSAL_PREFIX",
    "LabTechnique",
    "TECHNIQUES",
    "catalog",
    "is_lab_only",
    "refusal",
]
