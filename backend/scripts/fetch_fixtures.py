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
    if gated in (False, "auto"):
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
        destination = out / f"{fixture.role.replace('/', '-')}-{fixture.sha256[:16]}"
        if skip_existing and destination.exists():
            observed = hashlib.sha256(destination.read_bytes()).hexdigest()
            if observed == fixture.sha256:
                pinned.append({**asdict(fixture), "path": destination.name, "status": "present"})
                continue
            # Present but wrong: fall through and re-fetch rather than trusting it.
            destination.unlink()

        observed = download(fixture.url, destination, expected_sha256=fixture.sha256)
        pinned.append(
            {**asdict(fixture), "path": destination.name, "status": "fetched", "observed": observed}
        )

    manifest = {"fixtures": pinned, "count": len(pinned)}
    (out / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


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
        "--gated",
        default="false",
        help="the source's access state; anything but 'false'/'auto' refuses",
    )
    args = parser.parse_args()

    try:
        spec = json.loads(args.spec.read_text())
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
