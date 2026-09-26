# ForenSight — Future Improvements

These are plan-consistent enhancements that extend the intent of `DESIGN_PLAN.md` without contradicting it. They are **not** part of the core P0-P10 migration path; they are candidate features to schedule after (or alongside) the phases they depend on. Each entry lists rationale, dependencies, design notes, and test cases so it can be promoted into the implementation plan when approved.

> Status legend: [ ] proposed  •  [~] approved / scheduled  •  [x] implemented

---

## FI-1. Case timeline auto-assembly
**Status:** [ ] proposed
**Rationale:** The agent builds a chronological timeline from evidence metadata + findings, giving investigators an at-a-glance narrative of the case.
**Depends on:** P2 (`timeline_events` table, evidence metadata), P5 (agent + structured case-memory tools).
**Design notes:**
- New agent tool `assemble_timeline(case_id)` reads evidence timestamps (EXIF/created_at), findings, and audit events, then writes ordered `timeline_events` rows.
- Conflicting/uncertain timestamps flagged, not silently ordered (honesty clause).
- Timeline is a derived view the human can edit; every auto-entry cites its source (provenance).
**Test cases:**
- FI1.T1 Given evidence with known EXIF timestamps + findings, timeline is produced in correct chronological order.
- FI1.T2 Evidence with missing/ambiguous timestamp is flagged "uncertain", not fabricated.
- FI1.T3 Each timeline entry carries a provenance reference (evidence/finding id).
- FI1.T4 Human edit to a timeline entry is preserved and audited; re-run does not silently overwrite it.

---

## FI-2. Duplicate / near-duplicate evidence detection
**Status:** [ ] proposed
**Rationale:** Reduce noise and storage by detecting identical and visually near-identical evidence on ingest.
**Depends on:** P2 (content-hash ingest pipeline).
**Design notes:**
- Exact duplicates already collapse via SHA-256 content-hash (P2).
- Add **perceptual hash** (pHash/dHash for images, chromaprint for audio) computed on ingest and stored alongside the content-hash.
- On upload, compare pHash against existing evidence in the same case/org; surface "possible duplicate of X (distance d)" to the user — never auto-delete.
**Test cases:**
- FI2.T1 Byte-identical file dedupes to the same object (existing P2 behavior confirmed).
- FI2.T2 Re-compressed / resized copy of an image is flagged as near-duplicate above a tuned threshold.
- FI2.T3 Unrelated image is NOT flagged (false-positive guard).
- FI2.T4 Near-duplicate detection never deletes; it only surfaces a suggestion the human confirms.

---

## FI-3. Redaction tool for exports
**Status:** [ ] proposed
**Rationale:** Enforce PII minimization (already in DESIGN_PLAN 6 Data) when sharing reports/evidence externally — blur faces, redact text/regions.
**Depends on:** P6 (face detection for auto-suggested face blur), P8 (report/export pipeline).
**Design notes:**
- Redaction produces a **new derived artifact**; the original WORM evidence is never modified (chain-of-custody preserved).
- Face auto-suggest uses the P6 face detector; the human confirms/adjusts regions before export.
- Redacted export records what was redacted (region metadata, not the content) in the audit trail.
**Test cases:**
- FI3.T1 Original evidence bytes/hash unchanged after redaction; redacted copy is a separate object.
- FI3.T2 Auto-suggested face regions cover detected faces; human can add/remove regions.
- FI3.T3 Redacted text/regions are not recoverable from the exported artifact (flattened, not overlaid).
- FI3.T4 Redaction event audited with region metadata and operator.

---

## FI-4. Multi-case entity linking
**Status:** [ ] proposed
**Rationale:** Surface the same person / phone / location / vehicle across multiple cases to reveal connections.
**Depends on:** P2 (`entities` table), P5 (pgvector + agent).
**Design notes:**
- Entities (person, phone, email, location, vehicle, account) are extracted/curated per case.
- Linking uses exact keys (phone/email) + embedding similarity (names/faces via pgvector), scoped by org and respecting RBAC — a user only sees links to cases they may access.
- Presented as suggested links with confidence; human confirms. No automatic cross-case data leakage.
**Test cases:**
- FI4.T1 Same phone/email across two accessible cases produces a link; different values do not.
- FI4.T2 Embedding-based name/face link respects a tuned threshold (no spurious links).
- FI4.T3 RBAC: a user cannot see or infer links into cases they are not authorized for.
- FI4.T4 Suggested links require human confirmation and are audited.

---

## FI-5. Agent "second-opinion" reviewer mode
**Status:** [ ] proposed
**Rationale:** A read-only agent pass critiques findings before human review, aligning with the Reflector node and the Reviewer role.
**Depends on:** P5 (agent, Reflector), P6 (real analyzers to critique), P3 (Reviewer role).
**Design notes:**
- Reviewer-mode agent runs strictly read-only: it cannot mutate state, only produce a critique report (weak evidence, missing analysis, over-stated confidence, tier violations).
- Explicitly checks the honesty clause: flags any finding phrased as "identity" when the module only supports "visual similarity".
- Output is advisory, attached to the case for the human reviewer; never auto-applied.
**Test cases:**
- FI5.T1 Reviewer agent has no write tools; any attempted mutation is blocked and audited.
- FI5.T2 A finding overstating certainty (Tier-B labeled as identity) is flagged by the critique.
- FI5.T3 A missing corroborating analysis is suggested (e.g., face match without image-forensics check).
- FI5.T4 Critique is attached read-only to the case and visible to the Reviewer role.

---

## Promotion process
When a future improvement is approved, move it from here into `IMPLEMENTATION_PLAN.md` under (or after) its dependency phase, and add its build task + test cases to `CHECKLIST.md`. Update the Status marker here to [~] scheduled, then [x] implemented.
