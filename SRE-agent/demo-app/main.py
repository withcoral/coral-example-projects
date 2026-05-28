"""Hello-service: a deliberately buggy FastAPI app for SRE-agent demos.

The bug: `/greet?name=<name>` calls `.upper()` on the result of a dict lookup,
without handling the None case when the name isn't in the lookup. Real users
hitting `/greet` with unrecognised names get 500s, Sentry captures the
AttributeError with a full stack trace, and a counter metric flows to Datadog
so a monitor can fire.

This is the kind of bug everyone has shipped at some point — happy-path code
that wasn't tested against unknown input.
"""
from __future__ import annotations

import logging
import os
import socket
import time
import traceback

import httpx
import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("hello-service")

SENTRY_DSN = os.getenv("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        send_default_pii=True,
        environment=os.getenv("APP_ENV", "production"),
        release=os.getenv("APP_RELEASE"),
        traces_sample_rate=0.0,
    )

DD_API_KEY = os.getenv("DD_API_KEY")
DD_SITE = os.getenv("DD_SITE", "datadoghq.com")
SERVICE_NAME = os.getenv("SERVICE_NAME", "hello-service")
APP_ENV = os.getenv("APP_ENV", "production")
HOSTNAME = socket.gethostname()


def push_dd_counter(metric: str, tags: list[str]) -> None:
    """Best-effort push of a count=1 metric to Datadog. Never raises."""
    if not DD_API_KEY:
        return
    payload = {
        "series": [
            {
                "metric": metric,
                "points": [[int(time.time()), 1]],
                "type": "count",
                "tags": tags,
            }
        ]
    }
    try:
        httpx.post(
            f"https://api.{DD_SITE}/api/v1/series",
            headers={"DD-API-KEY": DD_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=2.0,
        )
    except Exception:
        logger.warning("dd metric push failed", exc_info=True)


def push_dd_log(message: str, *, level: str = "info", extra: dict | None = None) -> None:
    """Best-effort push of a single structured log entry to Datadog's HTTP
    log intake. No agent needed -- this is direct POST to the public
    https://http-intake.logs.<site>/api/v2/logs endpoint. Never raises."""
    if not DD_API_KEY:
        return
    payload = {
        "ddsource": "python",
        "service": SERVICE_NAME,
        "ddtags": f"env:{APP_ENV},service:{SERVICE_NAME}",
        "hostname": HOSTNAME,
        "message": message,
        "status": level,
        **(extra or {}),
    }
    try:
        httpx.post(
            f"https://http-intake.logs.{DD_SITE}/api/v2/logs",
            headers={"DD-API-KEY": DD_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=2.0,
        )
    except Exception:
        logger.warning("dd log push failed", exc_info=True)


# Known users and their preferred display name.
# In a real app this would come from a database.
USERS = {
    "alice": "Alice",
    "bob": "Bob",
    "carol": "Carol",
}


app = FastAPI(title="hello-service")


@app.middleware("http")
async def observe_errors(request: Request, call_next):
    """Emit a structured log entry per request (with status, duration, and
    -- on error -- exception type/message/stack) to Datadog's HTTP log
    intake. Also push the error counter that drives the alert monitor."""
    started = time.perf_counter()
    method = request.method
    path = request.url.path
    try:
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000.0
        # Skip health checks from the log stream -- they're noise.
        if path != "/healthz":
            push_dd_log(
                f"{method} {path} {response.status_code} in {duration_ms:.0f}ms",
                level="info",
                extra={
                    "http": {
                        "method": method,
                        "path": path,
                        "status_code": response.status_code,
                    },
                    "duration_ms": round(duration_ms, 1),
                },
            )
        return response
    except Exception as exc:
        duration_ms = (time.perf_counter() - started) * 1000.0
        push_dd_counter(
            "hello_service.errors",
            tags=[
                f"service:{SERVICE_NAME}",
                f"endpoint:{path}",
                f"exception:{type(exc).__name__}",
            ],
        )
        push_dd_log(
            f"{method} {path} raised {type(exc).__name__}: {exc}",
            level="error",
            extra={
                "http": {
                    "method": method,
                    "path": path,
                    "status_code": 500,
                },
                "duration_ms": round(duration_ms, 1),
                "error": {
                    "kind": type(exc).__name__,
                    "message": str(exc),
                    "stack": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                },
                "query_params": dict(request.query_params),
            },
        )
        raise


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "hello world"}


@app.get("/greet")
def greet(name: str = "alice") -> dict[str, str]:
    display = USERS.get(name)
    return {"message": f"Hello, {display.upper()}!"}


@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(status_code=500, content={"error": "internal error"})
