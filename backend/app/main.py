"""Application factory: builds the FastAPI app around an injected Container."""
from __future__ import annotations

import logging
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .container import Container, build_container
from .domain import (AuthenticationFailed, Conflict, DomainError, Forbidden, InvalidAction, NotFound,
                     TooManyAttempts)

log = logging.getLogger("faultlines")

STATUS = {NotFound: 404, Forbidden: 403, InvalidAction: 400, Conflict: 409,
          AuthenticationFailed: 401, TooManyAttempts: 429}

MAX_BODY_BYTES = 16 * 1024
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # robots.txt asks crawlers to stay away; this header also keeps any page that is
    # reached anyway out of search indexes.
    "X-Robots-Tag": "noindex, nofollow",
}


def _reject(status: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail})


def _content_length(request: Request) -> int:
    try:
        return int(request.headers.get("content-length") or 0)
    except ValueError:
        return MAX_BODY_BYTES + 1  # malformed header: treat as too large


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()
    settings = container.settings
    app = FastAPI(title="Fault Lines", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.container = container

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            client = request.client.host if request.client else "unknown"
            if not container.rate_limiter.allow(f"ip:{client}"):
                response = _reject(429, "Too many requests")
                response.headers["Retry-After"] = str(settings.rate_limit_window_seconds)
            elif request.method in UNSAFE_METHODS and not request.headers.get("content-type", "").startswith(
                    "application/json"):
                # Only JSON bodies are accepted, which also blocks classic cross-site form posts.
                response = _reject(415, "Content-Type must be application/json")
            elif _content_length(request) > MAX_BODY_BYTES:
                response = _reject(413, "Request body too large")
            else:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    @app.exception_handler(DomainError)
    def domain_error(_: Request, exc: DomainError):
        return _reject(STATUS.get(type(exc), 400), str(exc))

    @app.exception_handler(sqlite3.Error)
    def db_error(_: Request, exc: sqlite3.Error):
        log.error("Database error: %s", type(exc).__name__)  # details stay in the server log
        return _reject(500, "Internal error")

    app.include_router(router)
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
    return app


app = create_app()
