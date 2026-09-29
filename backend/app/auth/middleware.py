"""Security headers + CSRF middleware (T1.5, T1.6).

- SecurityHeadersMiddleware sets CSP, X-Content-Type-Options, X-Frame-Options,
  Referrer-Policy, and (in prod) HSTS on every response.
- CSRFMiddleware enforces double-submit-cookie CSRF protection for
  cookie-authenticated mutating requests. Bearer-token (header) requests are
  exempt because they are not sent automatically by browsers.
"""
from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, hsts: bool = False) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._hsts = hsts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "no-referrer")
        headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if self._hsts:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains; preload",
            )
        return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Double-submit-cookie CSRF protection for cookie-based mutations."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method not in _SAFE_METHODS:
            has_auth_cookie = request.cookies.get("access_token") is not None
            uses_bearer = request.headers.get("authorization", "").lower().startswith(
                "bearer "
            )
            # Only enforce for cookie-authenticated requests (not bearer/header).
            if has_auth_cookie and not uses_bearer:
                cookie_token = request.cookies.get(CSRF_COOKIE)
                header_token = request.headers.get(CSRF_HEADER)
                if (
                    not cookie_token
                    or not header_token
                    or not secrets.compare_digest(cookie_token, header_token)
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF token missing or invalid"},
                    )
        return await call_next(request)
