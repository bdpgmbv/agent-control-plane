"""
LAYER 7 - API: THE APPLICATION
==============================
Puts the whole system together and serves it over HTTP, plus the web UI.

Start it with:
    ./run.sh
    (or: uvicorn rag_assistant.layer7_api.main:app --reload --port 8010)

Then open http://localhost:8010

The middleware is worth reading. Every request gets an id, that id appears in
every log line the request produces, and it comes back in the response header.
When a user says "the answer was wrong at 14:32", that id is how you find the
exact retrieval trace instead of guessing.
"""

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from rag_assistant.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer1_config.settings import find_beside_project, settings
from rag_assistant.layer3_storage.factory import get_store
from rag_assistant.layer7_api import (
    routes_ask,
    routes_documents,
    routes_evaluate,
    routes_health,
    routes_metrics,
)

setup_logging("INFO")
log = get_logger("app")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_FOLDER = find_beside_project("ui")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Production RAG Knowledge Assistant",
        description=(
            "Project 1 of 8. Hybrid retrieval, reranking, enforced citations, "
            "honest refusal, role-based access control, caching and a measurable "
            "evaluation suite."
        ),
        version="1.0.0",
    )

    # The UI is served from the same origin, so this is only for local tools.
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.middleware("http")
    async def add_request_id_and_metrics(request: Request, call_next):
        """Tag every request, time it, and never let an error escape untyped."""
        request_id = new_request_id()
        token = current_request_id.set(request_id)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as error:
            metrics.increment("failures_total")
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            current_request_id.reset(token)
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

        current_request_id.reset(token)
        return response

    application.include_router(routes_health.router, tags=["health"])
    application.include_router(routes_documents.router, tags=["documents"])
    application.include_router(routes_ask.router, tags=["ask"])
    application.include_router(routes_metrics.router, tags=["metrics"])
    application.include_router(routes_evaluate.router, tags=["evaluation"])

    @application.on_event("startup")
    async def on_startup() -> None:
        """Open storage once, and say clearly what mode we are running in."""
        store = get_store()
        log_event(
            log,
            "startup",
            storage=store.name,
            chunks_indexed=store.count_chunks(),
            llm_live=settings.using_real_llm(),
            llm_model=settings.llm_model,
            embeddings_live=settings.using_real_embeddings(),
        )

        if not settings.using_real_llm():
            log.warning(
                "Running in OFFLINE mode: no usable OPENAI_API_KEY found. "
                "The pipeline is fully functional but answers come from the "
                "built-in extractive fallback. Paste a key into .env for real answers."
            )

    # --- the web UI ---
    if UI_FOLDER.exists():
        application.mount("/static", StaticFiles(directory=str(UI_FOLDER)), name="static")

        @application.get("/", include_in_schema=False)
        async def home() -> FileResponse:
            return FileResponse(str(UI_FOLDER / "index.html"))

    return application


app = create_app()
