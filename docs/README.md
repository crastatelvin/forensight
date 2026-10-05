# docs/ — Documentation

| Document | Covers |
|---|---|
| [`threat-model.md`](threat-model.md) | Assets, trust boundaries, controls, open risks |
| [`runbook.md`](runbook.md) | Operating the stack; common failures and their fixes |
| [`secrets.md`](secrets.md) | How secrets are supplied today; Vault/SOPS upgrade path |
| [`admin-guide.md`](admin-guide.md) | Roles, user management, day-to-day usage |

Architecture and the phased roadmap live at the repo root:
[`DESIGN_PLAN.md`](../DESIGN_PLAN.md), [`IMPLEMENTATION_PLAN.md`](../IMPLEMENTATION_PLAN.md),
[`CHECKLIST.md`](../CHECKLIST.md).

The runbook, threat model, and admin guide were drafted in Phase 0 and revised
during the Phase 0–3 hardening pass. Ops and load-testing detail (backups,
PITR, Locust, air-gapped egress) arrives with Phase 10, since most of it depends
on components that do not exist yet.