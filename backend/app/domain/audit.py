"""Append-only, hash-chained audit log helpers (T2.5).

The chain links each entry to the previous via SHA-256. Verification recomputes
each entry_hash; any tampered row (or reordering) breaks the chain.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_HASH = "0" * 64


def canonical_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_entry_hash(
    *,
    prev_hash: str,
    actor_id: str | None,
    action: str,
    target: str,
    payload: dict[str, Any],
) -> str:
    material = "|".join(
        [
            prev_hash,
            actor_id or "",
            action,
            target,
            canonical_payload(payload),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
