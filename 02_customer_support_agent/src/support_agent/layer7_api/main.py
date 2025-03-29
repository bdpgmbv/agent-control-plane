"""
LAYER 7 - API: THE APPLICATION
==============================
Start it with:
    ./run.sh          (or: uvicorn support_agent.layer7_api.main:app --reload --port 8020)

Then open http://localhost:8020

The middleware gives every request an id and every conversation turn a
conversation id, both of which appear in every log line that request produces.
"""

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from support_agent.layer0_shared.logging_setup import (
    current_conversation_id,
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from support_agent.layer0_shared.metrics import metrics
from support_agent.layer1_config.settings import find_beside_project, settings
from support_agent.layer3_storage.database import get_database
from support_agent.layer3_storage.seed_data import seed
from support_agent.layer7_api import routes_admin, routes_chat, routes_health

setup_logging("INFO")
log = get_logger("app")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_FOLDER = find_beside_project("ui")


def create_app() -> FastAPI:
    application = FastAPI(
        title="AI Customer Support Agent",
        description=(
            "Project 2 of 8. Tool calling with permissions, idempotent writes, "
            "human approval for money, intent routing, escalation, PII redaction "
            "and a full audit log."
        ),
        version="1.0.0",
    )

    application.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @application.middleware("http")
    async def tag_and_time(request: Request, call_next):
        request_id = new_request_id()
        request_token = current_request_id.set(request_id)
        conversation_token = current_conversation_id.set("-")
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as error:
            metrics.increment("failures_total")
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            current_request_id.reset(request_token)
            current_conversation_id.reset(conversation_token)
            return JSONResponse(
                status_code=500,
                content={"detail": "Internal error: %s" % error, "request_id": request_id},
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-ms"] = str(elapsed_ms)

        if request.url.path.startswith("/api/"):
            metrics.observe("http_latency_ms", elapsed_ms)
            log_event(
                log,
                "http.request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                latency_ms=elapsed_ms,
            )

        current_request_id.reset(request_token)
        current_conversation_id.reset(conversation_token)
        return response

    application.include_router(routes_health.router, tags=["health"])
    application.include_router(routes_chat.router, tags=["chat"])
    application.include_router(routes_admin.router, tags=["admin"])

    @application.on_event("startup")
    async def on_startup() -> None:
        database = get_database()

        # An empty database makes the demo look broken, so seed it on first run.
        order_count = database.connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        if order_count == 0:
            seed(database)

        log_event(
            log,
            "startup",
            llm_live=settings.using_real_llm(),
            llm_model=settings.llm_model,
            orders=database.connection.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"],
        )

        if not settings.using_real_llm():
            log.warning(
                "Running OFFLINE: no usable OPENAI_API_KEY. The agent loop, tools, "
                "permissions, idempotency, escalation and audit are all fully "
                "exercised; only the planning is rule-based. Add a key for real "
                "tool calling."
            )

    if UI_FOLDER.exists():
        application.mount("/static", StaticFiles(directory=str(UI_FOLDER)), name="static")

        @application.get("/", include_in_schema=False)
        async def home() -> FileResponse:
            return FileResponse(str(UI_FOLDER / "index.html"))

    return application


app = create_app()
