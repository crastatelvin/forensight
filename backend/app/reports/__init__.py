"""Signed case reports: PAdES signatures, a SHA-256 manifest, a provenance appendix (P8).

`sign_pdf` and `verify_pdf` are async: pyHanko's blocking entry points call
`asyncio.run()` internally and raise if called from a running loop, so a request
handler has to use the async ones.
"""

from app.reports.signing import (
    SigningUnavailable,
    VerifyResult,
    sign_pdf,
    verify_pdf,
)

__all__ = ["SigningUnavailable", "VerifyResult", "sign_pdf", "verify_pdf"]
