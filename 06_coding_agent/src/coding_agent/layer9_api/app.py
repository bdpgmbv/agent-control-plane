"""
LAYER 9 - THE HTTP API
======================
FastAPI over the runner, plus the single-page interface in web/.

Tasks are run in a background thread with their progress written to a small
in-memory registry, because a live benchmark takes half a minute and a browser
should not sit on an open request for it. The registry is deliberately simple -
a dict and a lock - and it is explicitly not a job queue. If this needed to
survive a restart it would need a database, which is project 07.
"""

import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from coding_agent.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from coding_agent.layer1_config.settings import SETTINGS, find_beside_project
from coding_agent.layer9_api.runner import run_task
from coding_agent.layer10_evaluation.step1_benchmark import render, score
from coding_agent.layer10_evaluation.tasks import BENCHMARK, all_task_ids, find

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_DIRECTORY = find_beside_project("ui")

app = FastAPI(
    title="AI Coding Agent",
    description="repository + issue -> patch, sandboxed, tested and verified",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
#  Request logging
# ---------------------------------------------------------------------------
# Every request gets an id, and that id appears on every line logged while it is
# being handled. Without it, concurrent requests interleave in the log and there
# is no way to tell which line belongs to which - which is exactly when you need
# the log, because the problem only shows up under load.
setup_logging()
LOGGER = get_logger(__name__)


@app.middleware("http")
async def log_requests(request, call_next):
    request_id = new_request_id()
    token = current_request_id.set(request_id)
    started = time.time()
    try:
        response = await call_next(request)
    except Exception as error:
        log_event(LOGGER, "request failed",
                  method=request.method, path=request.url.path,
                  seconds=round(time.time() - started, 3),
                  error="%s: %s" % (type(error).__name__, error))
        current_request_id.reset(token)
        raise

    log_event(LOGGER, "request",
              method=request.method, path=request.url.path,
              status=response.status_code,
              seconds=round(time.time() - started, 3))
    response.headers["X-Request-Id"] = request_id
    current_request_id.reset(token)
    return response


class RunRequest(BaseModel):
    task_ids: list[str] = []
    live: bool = False


class RunRegistry:
    """Somewhere for a background run to put its progress."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.runs: dict = {}

    def create(self, task_ids: list[str], live: bool) -> str:
        run_id = "run_" + uuid.uuid4().hex[:10]
        with self.lock:
            self.runs[run_id] = {
                "run_id": run_id,
                "status": "running",
                "live": live,
                "task_ids": list(task_ids),
                "done": 0,
                "total": len(task_ids),
                "results": [],
                "report": None,
                "error": "",
            }
        return run_id

    def add_result(self, run_id: str, result) -> None:
        with self.lock:
            entry = self.runs.get(run_id)
            if entry is None:
                return
            entry["results"].append(result.model_dump(mode="json"))
            entry["done"] = entry["done"] + 1

    def finish(self, run_id: str, report_text: str, summary: dict) -> None:
        with self.lock:
            entry = self.runs.get(run_id)
            if entry is None:
                return
            entry["status"] = "finished"
            entry["report"] = report_text
            entry["summary"] = summary

    def fail(self, run_id: str, message: str) -> None:
        with self.lock:
            entry = self.runs.get(run_id)
            if entry is None:
                return
            entry["status"] = "failed"
            entry["error"] = message

    def get(self, run_id: str) -> dict | None:
        with self.lock:
            entry = self.runs.get(run_id)
            if entry is None:
                return None
            return dict(entry)


REGISTRY = RunRegistry()


def execute_run(run_id: str, task_ids: list[str], live: bool) -> None:
    from coding_agent.layer2_models.schemas import AgentResult

    results: list[AgentResult] = []
    try:
        for task_id in task_ids:
            result = run_task(task_id, offline=not live)
            results.append(result)
            REGISTRY.add_result(run_id, result)

        report = score(results, offline=not live, seconds=0.0)
        REGISTRY.finish(run_id, render(report), report.summary.model_dump(mode="json"))
    except Exception as error:
        REGISTRY.fail(run_id, "%s: %s" % (type(error).__name__, error))


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "offline" if SETTINGS.is_offline() else "live",
        "model": None if SETTINGS.is_offline() else SETTINGS.llm_model,
        "budget": {
            "max_iterations": SETTINGS.max_iterations,
            "max_tokens_per_task": SETTINGS.max_tokens_per_task,
            "max_seconds_per_task": SETTINGS.max_seconds_per_task,
            "test_timeout_seconds": SETTINGS.test_timeout_seconds,
        },
        "context": {
            "max_files_in_context": SETTINGS.max_files_in_context,
            "max_file_characters": SETTINGS.max_file_characters,
        },
        "protected_globs": SETTINGS.protected_globs,
        "tasks": len(BENCHMARK),
    }


@app.get("/api/tasks")
def list_tasks() -> dict:
    rows = []
    for entry in BENCHMARK:
        broken_files = []
        for path, _old, _new in entry["break"]:
            if path not in broken_files:
                broken_files.append(path)
        rows.append({
            "task_id": entry["task_id"],
            "title": entry["title"],
            "issue": entry["issue"],
            "target_tests": entry["target_tests"],
            "files_broken": broken_files,
        })
    return {"tasks": rows}


@app.get("/api/tasks/{task_id}")
def one_task(task_id: str) -> dict:
    entry = find(task_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="no task called %r" % task_id)
    return {
        "task_id": entry["task_id"],
        "title": entry["title"],
        "issue": entry["issue"],
        "target_tests": entry["target_tests"],
    }


@app.post("/api/runs")
def start_run(request: RunRequest) -> dict:
    task_ids = request.task_ids
    if len(task_ids) == 0:
        task_ids = all_task_ids()

    known = all_task_ids()
    for task_id in task_ids:
        if task_id not in known:
            raise HTTPException(status_code=400, detail="no task called %r" % task_id)

    if request.live and SETTINGS.is_offline():
        raise HTTPException(
            status_code=400,
            detail="a live run was asked for, but there is no OPENAI_API_KEY in .env",
        )

    run_id = REGISTRY.create(task_ids, request.live)
    thread = threading.Thread(target=execute_run, args=(run_id, task_ids, request.live),
                              daemon=True)
    thread.start()
    return {"run_id": run_id, "tasks": len(task_ids), "live": request.live}


@app.get("/api/runs/{run_id}")
def run_status(run_id: str) -> dict:
    entry = REGISTRY.get(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="no run called %r" % run_id)
    return entry


@app.get("/")
def home():
    index = UI_DIRECTORY / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "the web interface is missing"}, status_code=404)
    return FileResponse(index)


if UI_DIRECTORY.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIRECTORY)), name="ui")
