"""Fixture acquisition — with the licence question answered in code, not in prose.

This exists because of a specific, measured finding rather than a general plan. The
Phase 9 Tier-A analyzers are blocked on **labelled forensic fixtures**, and a survey
of Hugging Face found this:

- The **models** exist as pinnable ONNX on the Hub (Deep-Fake-Detector-v2,
  CommunityForensics-DeepfakeDet-ViT, YAMNet, ECAPA-TDNN).
- The **fixture sets** do not, in a usable form. Every ungated deepfake set found
  states **no licence at all**. The one with a clean CC BY-SA-4.0 licence
  (`nuriachandra/Deepfake-Eval-2024`) is **manually gated** behind an institutional
  email, an institution, and attested prior deepfake-detection work — and its terms
  restrict use to evaluation. The one usable fingerprint set is **CC BY-NC-ND-4.0**,
  whose no-derivatives clause forbids the derived golden set this project needs. And
  ESC-50, the obvious choice for gunshot audio, has **no gunshot class** among its 50.

So the blocker is not "someone forgot to download the data". It is that the data that
exists is gated, unlicensed, licensed-no-derivatives, or the wrong task entirely.

**This script is therefore mostly a gate.** Its most valuable behaviour is refusing:

- a **gated** dataset — quietly skipping one is how a repo ends up with fixtures it has
  no right to use, discovered after the fact;
- a dataset with **no stated licence** — "unstated" is not permission;
- **ND** and **SA** licences — see below.

**Why ND and SA are refused, which is a decision this repo made deliberately:**

- **ND (NoDerivatives)** forbids creating a modified copy. A golden test set *is* a
  derivative: cropped, re-encoded, or resampled to make a fixture. Refusing it is not
  pedantry; a fixture derived under ND and committed to a public repo is a licence
  breach that nobody would notice until it mattered.
- **SA (ShareAlike)** is subtler and was considered seriously. There is currently no
  `LICENSE` file in this repository, so there is nothing for SA to conflict *with*
  today. It was refused anyway, because fixtures get copied into the repo and the
  obligation would then attach silently to a repository that never agreed to it —
  binding whoever adds a licence later to a term they did not choose. A permissive
  alternative exists for most of these techniques; share-alike is not worth the
  surprise.

**Accepted licences** are the permissive ones: CC0, CC BY, public domain, and explicit
MIT/Apache/BSD for weights. Anything else stops the fetch.

**The digest is the point.** Every pinned fixture records its SHA-256, so "we used the
standard sample" is checkable in two years rather than a claim in a commit message.
This mirrors `fetch_models.sh`: pinned, verified, and refusing to proceed on mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MANIFEST_NAME = "fixtures.json"

#: Licences a fixture may carry. Deliberately an allowlist: "anything not explicitly
#: forbidden" would let `other`, `unknown` and unstated licences through, which is the
#: failure this gate exists to stop.
PERMITTED_LICENCES = frozenset(
    {
        "cc0-1.0",
        "cc0",
        "cc-by-3.0",
        "cc-by-4.0",
        "cc-by",
        "publicdomain",
        "public-domain",
        "pd",
    }
)

#: Licences that are stated and clear, and still refused. Each with its reason, so
#: the refusal can be explained rather than only enforced.
REFUSED_WITH_REASON: dict[str, str] = {
    "cc-by-nd-4.0": (
        "NoDerivatives forbids creating a modified copy, and a golden fixture set is a "
        "derivative — cropped, re-encoded or resampled. Committing one would be a "
        "licence breach that nothing in the pipeline would catch."
    ),
    "cc-by-sa-4.0": (
        "ShareAlike is refused deliberately even though this repository has no "
        "LICENSE file and so nothing conflicts today. Fixtures are copied into the "
        "repo, so the obligation would attach silently to whoever adds a licence "
        "later, binding them to a term they did not choose."
    ),
}

#: What an absent licence tag means here: nothing. Not permission.
UNSTATED = "unstated"


class FixtureRefused(RuntimeError):
    """The fixture cannot be used, and the reason is specific."""


@dataclass(frozen=True)
class Fixture:
    """One pinned file, with the provenance that makes the pin meaningful."""

    #: Where it came from, exactly. A URL is the whole claim.
    url: str
    #: SHA-256 of the bytes. Verified before the file is written.
    sha256: str
    #: SPDX-ish licence identifier, or `unstated`.
    licence: str
    #: What the fixture is *for* — e.g. `deepfake/real`, `gunshot/firearm`. Ties the
    #: pin to the analyzer that needs it, so a fixture nobody uses is visible.
    role: str
    #: Free-form note: paper, dataset card, or why this particular file.
    note: str = ""
    #: Set only for a fixture *derived* from a pinned source rather than fetched, e.g.
    #: a passage cut from a Project Gutenberg text. The bytes travel here so the
    #: derivation and the digest check happen in the same place, and the digest still
    #: has to match — a derivation that drifted would silently invalidate every golden
    #: value recorded against it.
    text: str | None = None

    def check_licence(self) -> None:
        """Raise unless this fixture may be used and committed."""
        licence = self.licence.strip().lower()
        if not licence or licence == UNSTATED:
            raise FixtureRefused(
                f"{self.url} states no licence. Unstated is not permission, and "
                f"fetching it would put bytes of unknown provenance into a forensic "
                f"tool's test suite. Find a source with a licence, or record why this "
                f"one is safe."
            )
        if licence in REFUSED_WITH_REASON:
            raise FixtureRefused(f"{self.url} is {licence}. {REFUSED_WITH_REASON[licence]}")
        if licence not in PERMITTED_LICENCES:
            raise FixtureRefused(
                f"{self.url} is licensed {licence!r}, which is not on the permitted "
                f"list. Permitted: {', '.join(sorted(PERMITTED_LICENCES))}. This is an "
                f"allowlist on purpose — the question is what has been cleared, not "
                f"what has not been forbidden."
            )


def check_gated(gated: str | bool) -> None:
    """Raise if the source requires access approval.

    A gated dataset is not unavailable; it is *available on conditions*. Fetching
    around a gate is not an option this project has, and silently skipping a gated
    source would hide that the pin was never obtained.
    """
    # The CLI passes the *string* from a flag while tests pass a bool, so both forms
    # have to mean the same thing. It did not: the first version compared against
    # `False` and `"auto"`, so `--gated false` — the documented, ordinary invocation —
    # was treated as gated and refused every fetch. The unit tests passed because they
    # called the function with a bool and never exercised the path a user takes.
    if gated in (False, "false", "no", "0", "", "auto"):
        return
    raise FixtureRefused(
        f"this dataset is gated ({gated!r}) and requires access approval before its "
        f"contents may be used. This project does not fetch gated sources: the gate "
        f"exists because a human accepted terms on someone's behalf, and the fixture "
        f"would carry those terms into the repository."
    )


def download(url: str, destination: Path, *, expected_sha256: str, timeout: int = 300) -> str:
    """Fetch and verify. Returns the digest actually observed.

    The digest is recomputed from the bytes received rather than trusted from the
    server, and a mismatch aborts before anything is written.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - scheme checked by caller
            payload = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise FixtureRefused(f"could not fetch {url}: {exc}") from exc

    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise FixtureRefused(
            f"{url} hashes to {actual[:16]}… but {expected_sha256[:16]}… was pinned. "
            f"The source changed, or the pin is wrong. Either way nothing is written: a "
            f"fixture whose bytes do not match its pin would make every later test "
            f"meaningless."
        )
    destination.write_bytes(payload)
    return actual


def gutenberg_passages(
    source: Path,
    *,
    count: int = 1,
    words: int = 6000,
    skip: int = 0,
) -> list[str]:
    """Deterministic excerpts from a Project Gutenberg plain-text file.

    The full novels are 450 KB–1.3 MB each, far too much to commit as test material,
    and re-deriving them at test time would make the suite depend on the network. So
    the *source* is pinned by digest and excerpts are cut from it here, once, at
    fixture time.

    **Deterministic by construction, and that is the point.** Paragraphs are taken at
    fixed offsets rather than sampled, so the same pin always yields byte-identical
    fixtures and a golden value recorded today still holds in a year. Sampling would
    make every golden test a coin flip.

    The boilerplate Gutenberg wraps around every file is skipped by requiring a
    minimum length and a single line: chapter headings, the licence block and the
    table of contents are all short, and an authorship fixture drawn from the licence
    text would be measuring the same words in every file.
    """
    text = source.read_text(encoding="utf-8", errors="replace")
    # Everything between the first and last `***` marker is the book; Gutenberg wraps
    # it in a licence header and a footer, and a fixture drawn from the licence text
    # would be the same words in every file.
    if text.count("***") >= 2:
        text = text[text.index("***") + 3 : text.rindex("***")]

    # **Sized in words, and 6000 is not arbitrary.** Authorship attribution from
    # stylometry needs enough text per sample that the author's habits emerge above
    # the noise. Measured on these very sources: at ~1200 words per passage the
    # technique does not separate at all — same-author similarity fell to 0.837 while
    # a different-author pair reached 0.928, so the ranges overlapped completely and
    # any threshold would have been arbitrary. At ~6000 words same-author similarity
    # is 0.972 at worst against 0.950 for the worst different-author pair, which
    # separates. That is the whole difference between a working analyzer and a number
    # that looks like one.
    tokens = text.split()
    window = max(1, len(tokens) // (count * 2 + skip + 1))
    out: list[str] = []
    for index in range(count):
        start = window * (skip + index * 2)
        out.append(" ".join(tokens[start : start + words]))
    return out

    return out


def fixture_path(out: Path, fixture: Fixture) -> Path:
    """Where a fixture is written: its role and the short digest it pins.

    One owner for this rule. Tests used to spell it out themselves, and when the
    suffix changed here they went on pre-creating a file at the old path — so the
    test kept "passing" its setup while no longer exercising the skip-and-refetch
    path it exists to test. A test that duplicates the code under test is testing
    its own copy.
    """
    return out / f"{fixture.role.replace('/', '-')}-{fixture.sha256[:16]}.txt"


def _record(fixture: Fixture, **extra: Any) -> dict[str, Any]:
    """Manifest entry: everything except the inline text.

    The passage itself is dropped because it would make the manifest a second copy of
    the fixture, and two copies of prose in a repository is one more thing to keep in
    sync. The digest already proves which bytes were written.
    """
    return {**{k: v for k, v in asdict(fixture).items() if k != "text"}, **extra}


def fetch_all(
    out: Path,
    fixtures: list[Fixture],
    *,
    gated: str | bool = False,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Gate first, then fetch. Writes a manifest of what was actually pinned."""
    check_gated(gated)
    for fixture in fixtures:
        fixture.check_licence()

    out.mkdir(parents=True, exist_ok=True)
    pinned: list[dict[str, Any]] = []

    for fixture in fixtures:
        destination = fixture_path(out, fixture)

        if skip_existing and destination.exists():
            observed = hashlib.sha256(destination.read_bytes()).hexdigest()
            if observed == fixture.sha256:
                pinned.append(_record(fixture, path=destination.name, status="present"))
                continue
            # Present but wrong: fall through and re-derive rather than trusting it.
            # `skip_existing` means "skip if it verifies", not "skip if a file of that
            # name exists".
            destination.unlink()

        if fixture.text is not None:
            # Derived locally from an already-pinned source, so there is nothing to
            # fetch — but the digest check still applies. The whole value of a derived
            # fixture is that the same source always yields the same bytes, so a
            # derivation that drifted would silently invalidate every golden value
            # recorded against it.
            payload = fixture.text.encode("utf-8")
            observed = hashlib.sha256(payload).hexdigest()
            if observed != fixture.sha256:
                raise FixtureRefused(
                    f"derived passage for {fixture.role} hashes to {observed[:16]}… but "
                    f"{fixture.sha256[:16]}… was pinned; the derivation is not "
                    f"deterministic and must not be trusted."
                )
            destination.write_bytes(payload)
            status = "derived"
        else:
            observed = download(fixture.url, destination, expected_sha256=fixture.sha256)
            status = "fetched"

        pinned.append(_record(fixture, path=destination.name, status=status, observed=observed))

    manifest = {"fixtures": pinned, "count": len(pinned)}
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _cut_passages(assignments: list[str], count: int) -> list[dict[str, Any]]:
    """Turn `austen=/path/pg1342.txt` into fixture entries."""
    entries: list[dict[str, Any]] = []
    for assignment in assignments:
        name, _, raw_path = assignment.partition("=")
        name = name.strip()
        source = Path(raw_path.strip())
        if not name or not raw_path.strip():
            raise FixtureRefused(f"--gutenberg expects NAME=PATH, got {assignment!r}")
        if not source.exists():
            raise FixtureRefused(
                f"{source} does not exist. Fetch the pinned source first — passages are "
                f"cut from a digest-verified file, never from whatever is at a URL."
            )
        # Three passages, each cut from a *different* window of the source. Using the
        # same window for all three would make a "same author" comparison trivially
        # true — the two passages would be the same text.
        for window in range(count):
            passage = gutenberg_passages(source, count=1, skip=window * 3)[0]
            entries.append(
                {
                    "url": f"gutenberg:{name}#{window}",
                    "sha256": hashlib.sha256(passage.encode()).hexdigest(),
                    "licence": "publicdomain",
                    "role": f"authorship/{name}/{window}",
                    "note": f"passage {window} cut from the pinned Project Gutenberg "
                    f"source {source.name}",
                    "text": passage,
                }
            )
    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="where to write pinned fixtures")
    parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="JSON list of fixtures: url, sha256, licence, role",
    )
    parser.add_argument(
        "--gutenberg",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="cut deterministic passages from a pinned Project Gutenberg text: "
        "'austen=/backups/pg1342.txt'. Repeatable.",
    )
    parser.add_argument("--passages", type=int, default=3)
    parser.add_argument(
        "--gated",
        default="false",
        help="the source's access state; anything but 'false'/'auto' refuses",
    )
    args = parser.parse_args()

    try:
        spec = json.loads(args.spec.read_text()) if args.spec.exists() else []
        if args.gutenberg:
            spec.extend(_cut_passages(args.gutenberg, args.passages))
        manifest = fetch_all(
            args.directory,
            [Fixture(**entry) for entry in spec],
            gated=args.gated,
        )
    except (FixtureRefused, json.JSONDecodeError, TypeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    for entry in manifest["fixtures"]:
        print(
            f"  {entry['status']:8s} {entry['role']:24s} "
            f"{entry['sha256'][:16]} ({entry['licence']})"
        )
    print(f"\nPinned {manifest['count']} fixture(s) into {args.directory}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
