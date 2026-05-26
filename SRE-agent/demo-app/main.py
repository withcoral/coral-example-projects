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
import time

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
    try:
        return await call_next(request)
    except Exception as exc:
        push_dd_counter(
            "hello_service.errors",
            tags=[
                f"service:{SERVICE_NAME}",
                f"endpoint:{request.url.path}",
                f"exception:{type(exc).__name__}",
            ],
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
