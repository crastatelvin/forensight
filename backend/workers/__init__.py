"""Analysis worker processes (Phase 4).

Kept inside `backend/` because the worker imports `app.*` — the same models,
registry, and executor the API uses. A top-level `workers/` package would need
the API on its import path anyway, and duplicating the domain layer between API
and worker is how the two drift apart.
"""
