"""
LAYER 8 - API: THE APPLICATION
==============================
Start it with:
    ./run.sh          (or: uvicorn research_agent.layer8_api.main:app --reload --port 8030)

Then open http://localhost:8030
"""

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from research_agent.layer0_shared.logging_setup import get_logger, log_event, setup_logging
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer1_config.settings import find_beside_project, settings
from research_agent.layer3_sources.registry import get_sources
from research_agent.layer8_api import routes

setup_logging("INFO")
log = get_logger("app")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_FOLDER = find_beside_project("ui")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Agentic Research System",
        description=(
            "Project 3 of 8. A planner decomposes a question, parallel workers "
            "research the parts against a shared budget, evidence is deduplicated "
            "and conflicts surfaced, and a synthesis step writes a cited report "
            "that says what it did not manage to cover."
        ),
        version="1.0.0",
    )

    application.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @application.middleware("http")
    async def time_requests(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            metrics.increment("failures_total")
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": "Internal error: %s" % error})

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response.headers["X-Response-Time-ms"] = str(elapsed_ms)
        return response

    application.include_router(routes.router, tags=["research"])

    @application.on_event("startup")
    async def on_startup() -> None:
        descriptions: list[dict] = []
        for source in get_sources():
            descriptions.append(source.describe())

        log_event(
            log,
            "startup",
            llm_live=settings.using_real_llm(),
            llm_model=settings.llm_model,
            sources=descriptions,
            budgets=settings.describe()["budgets"],
        )

        if not settings.using_real_llm():
            log.warning(
                "Running OFFLINE: no usable OPENAI_API_KEY. Planning, parallel "
                "workers, budgets, deduplication, conflict detection, citation "
                "checking and the evaluation suite all run. Similarity is measured "
                "by words rather than meaning, which misses conflicts that are "
                "worded differently. Add a key for the full behaviour."
            )

    if UI_FOLDER.exists():
        application.mount("/static", StaticFiles(directory=str(UI_FOLDER)), name="static")

        @application.get("/", include_in_schema=False)
        async def home() -> FileResponse:
            return FileResponse(str(UI_FOLDER / "index.html"))

    return application


app = create_app()
