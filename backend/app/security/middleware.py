"""Security middleware for FastAPI application.

Implements:
- Rate limiting (in-memory token bucket)
- Request size limits
- CORS configuration
- Security headers
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory rate limiter using token bucket algorithm.

    For production, replace with Redis-based rate limiting.
    """

    def __init__(self, app, requests_per_minute: int = 30) -> None:
        super().__init__(app)
        self._rate = requests_per_minute
        self._buckets: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Use client IP for rate limiting
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        window = 60.0  # 1 minute window

        # Clean old entries
        self._buckets[client_ip] = [
            t for t in self._buckets[client_ip] if now - t < window
        ]

        if len(self._buckets[client_ip]) >= self._rate:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
            )

        self._buckets[client_ip].append(now)
        response = await call_next(request)
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with bodies exceeding the configured limit."""

    def __init__(self, app, max_bytes: int = 1024 * 1024) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": "Request body too large"},
            )
        response = await call_next(request)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
        )
        return response
