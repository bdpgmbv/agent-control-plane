"""
LAYER 9 - API: THE APPLICATION
==============================
Start it with:
    ./run.sh          (or: uvicorn sql_copilot.layer9_api.main:app --reload --port 8040)

Then open http://localhost:8040
"""

import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sql_copilot.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from sql_copilot.layer0_shared.metrics import metrics
from sql_copilot.layer1_config.settings import find_beside_project, settings
from sql_copilot.layer3_database.connection import get_database
from sql_copilot.layer9_api import routes

setup_logging("INFO")
log = get_logger("app")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_FOLDER = find_beside_project("ui")


def create_app() -> FastAPI:
    application = FastAPI(
        title="NL to SQL Analytics Copilot",
        description=(
            "Project 4 of 8. Schema retrieval, SQL generation, validation before "
            "execution, a read-only sandbox with a timeout and a row cap, a repair "
            "loop that re-validates, and defences against destructive SQL and "
            "prompt injection from the data itself."
        ),
        version="1.0.0",
    )

    application.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @application.middleware("http")
    async def tag_and_time(request: Request, call_next):
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
        current_request_id.reset(token)
        return response

    application.include_router(routes.router, tags=["copilot"])

    @application.on_event("startup")
    async def on_startup() -> None:
        try:
            database = get_database()
            tables = len(database.list_table_names())
        except FileNotFoundError:
            log.error(
                "The analytics database does not exist. Run: make seed"
            )
            return

        log_event(
            log,
            "startup",
            llm_live=settings.using_real_llm(),
            llm_model=settings.llm_model,
            tables_in_file=tables,
            limits=settings.describe()["limits"],
        )

        if not settings.using_real_llm():
            log.warning(
                "Running OFFLINE: no usable OPENAI_API_KEY. SQL is written from "
                "keyword templates, which only cover the shapes in the benchmark. "
                "Everything that makes this project worth reading - validation, "
                "the read-only sandbox, the timeout, the row cap, the repair loop "
                "and the whole safety suite - runs exactly the same either way."
            )

    if UI_FOLDER.exists():
        application.mount("/static", StaticFiles(directory=str(UI_FOLDER)), name="static")

        @application.get("/", include_in_schema=False)
        async def home() -> FileResponse:
            return FileResponse(str(UI_FOLDER / "index.html"))

    return application


app = create_app()
