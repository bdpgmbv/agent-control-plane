"""
LAYER 9 - THE HTTP API
======================
FastAPI over the service, plus the interface in ui/.

The endpoint worth noticing is `POST /api/runs/{id}/simulate-crash`. It kills
the worker thread without letting it tidy up, exactly as SIGKILL would, so the
recovery behaviour can be watched rather than read about. A workflow engine
whose crash recovery you have to take on trust is a workflow engine nobody will
trust.
"""

import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from enterprise_workflow.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from enterprise_workflow.layer1_config.settings import SETTINGS
from enterprise_workflow.layer2_models.schemas import (
    ApprovalDecision,
    StartRunRequest,
)
from enterprise_workflow.layer5_steps.step3_onboarding import ONBOARDING_STEPS, WORKFLOWS
from enterprise_workflow.layer9_api.service import WorkflowService


# The interface is shipped alongside the package, not inside it: the container
# copies it to /srv/ui and a source checkout has it at the project root. Look in
# the working directory first, then next to the source.
def find_ui_directory() -> Path:
    candidates = [Path.cwd() / "ui", Path(__file__).resolve().parents[3] / "ui"]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


UI_DIRECTORY = find_ui_directory()

app = FastAPI(
    title="Enterprise Multi-Agent Workflow",
    description="durable state, resumable runs, human approval, compensation",
    version="1.0.0",
)

setup_logging()
LOGGER = get_logger(__name__)

SERVICE = WorkflowService()
SERVICE.start_worker()


@app.middleware("http")
async def log_requests(request, call_next):
    request_id = new_request_id()
    token = current_request_id.set(request_id)
    started = time.time()
    try:
        response = await call_next(request)
    except Exception as error:
        log_event(LOGGER, "request failed", method=request.method,
                  path=request.url.path, seconds=round(time.time() - started, 3),
                  error="%s: %s" % (type(error).__name__, error))
        current_request_id.reset(token)
        raise

    log_event(LOGGER, "request", method=request.method, path=request.url.path,
              status=response.status_code, seconds=round(time.time() - started, 3))
    response.headers["X-Request-Id"] = request_id
    current_request_id.reset(token)
    return response


@app.get("/api/health")
def health() -> dict:
    counts: dict = {}
    for run in SERVICE.store.list_runs(limit=500):
        counts[run.state.value] = counts.get(run.state.value, 0) + 1

    return {
        "ok": True,
        "mode": "offline" if SERVICE.offline else "live",
        "model": None if SERVICE.offline else SETTINGS.llm_model,
        "worker_running": SERVICE.worker_is_running(),
        "database": SETTINGS.sqlite_path,
        "settings": {
            "lease_seconds": SETTINGS.lease_seconds,
            "max_attempts": SETTINGS.max_attempts,
            "approval_required_above": SETTINGS.approval_required_above,
            "approval_expiry_hours": SETTINGS.approval_expiry_hours,
        },
        "workflows": list(WORKFLOWS.keys()),
        "steps": ONBOARDING_STEPS,
        "runs_by_state": counts,
        "pending_approvals": len(SERVICE.queue.pending()),
    }


@app.post("/api/runs")
def start_run(request: StartRunRequest) -> dict:
    if request.workflow_name not in WORKFLOWS:
        raise HTTPException(status_code=400,
                            detail="there is no workflow called %r" % request.workflow_name)

    text = str(request.input.get("request_text", "")).strip()
    if text == "":
        raise HTTPException(status_code=400,
                            detail="the request text is empty, so there is nothing to read")

    run = SERVICE.start_run(request.workflow_name, request.input)
    SERVICE.start_worker()
    return {"run_id": run.run_id, "state": run.state.value}


@app.get("/api/runs")
def list_runs(limit: int = 50, state: str = "") -> dict:
    rows = []
    for run in SERVICE.store.list_runs(limit=limit, state=state):
        steps = SERVICE.store.get_steps(run.run_id)
        done = 0
        for step in steps:
            if step.state.value in ("succeeded", "skipped"):
                done = done + 1
        fields = run.context.get("fields") or {}
        rows.append({
            "run_id": run.run_id,
            "workflow_name": run.workflow_name,
            "state": run.state.value,
            "person": fields.get("full_name") or "",
            "steps_done": done,
            "steps_total": len(steps),
            "error": run.error,
            "created_at": run.created_at,
        })
    return {"runs": rows}


@app.get("/api/runs/{run_id}")
def one_run(run_id: str) -> dict:
    view = SERVICE.view(run_id)
    if view is None:
        raise HTTPException(status_code=404, detail="no run called %r" % run_id)
    return view.model_dump(mode="json")


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str) -> dict:
    run = SERVICE.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no run called %r" % run_id)
    if run.state.is_finished():
        raise HTTPException(status_code=400,
                            detail="this run already finished as %s" % run.state.value)

    requested = SERVICE.store.request_cancel(run_id)
    return {"ok": requested,
            "message": "cancellation requested; it takes effect between steps"
                       if requested else "this run cannot be cancelled now"}


@app.post("/api/runs/{run_id}/simulate-crash")
def simulate_crash(run_id: str) -> dict:
    """
    Stop the worker without letting it finish or tidy up.

    The closest thing to SIGKILL that a thread allows. The in-flight step keeps
    its lease until it expires, and then any worker may take it - which is the
    same recovery path a real crash uses. `scripts/crash_test.py` does this to
    an actual process; this is here so it can be watched.
    """
    run = SERVICE.store.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="no run called %r" % run_id)

    SERVICE.stop_worker()
    SERVICE.store.add_event(run_id, "worker.killed",
                            detail={"note": "the worker was stopped mid-run"})
    return {
        "ok": True,
        "message": ("the worker is stopped. Any step it held keeps its lease for "
                    "%.0f seconds; after that another worker may take it."
                    % SETTINGS.lease_seconds),
        "lease_seconds": SETTINGS.lease_seconds,
    }


@app.post("/api/worker/start")
def start_worker() -> dict:
    SERVICE.start_worker()
    return {"ok": True, "running": SERVICE.worker_is_running()}


@app.post("/api/worker/stop")
def stop_worker() -> dict:
    SERVICE.stop_worker()
    return {"ok": True, "running": SERVICE.worker_is_running()}


@app.get("/api/approvals")
def pending_approvals() -> dict:
    cards = []
    for card in SERVICE.queue.pending():
        cards.append({
            "approval_id": card.approval_id,
            "run_id": card.run_id,
            "step_name": card.step_name,
            "question": card.question,
            "detail": card.detail,
            "waiting_hours": card.waiting_hours,
            "expires_in_hours": card.expires_in_hours,
            "person": card.person,
        })
    return {"approvals": cards}


@app.post("/api/approvals/{approval_id}/decide")
def decide_approval(approval_id: str, decision: ApprovalDecision) -> JSONResponse:
    result = SERVICE.queue.decide(approval_id, decision.approved,
                                  decision.decided_by, decision.note)
    SERVICE.start_worker()
    return JSONResponse(
        status_code=200 if result.ok else 400,
        content={"ok": result.ok, "message": result.message,
                 "approval_id": result.approval_id})


@app.post("/api/reset")
def reset() -> dict:
    SERVICE.store.clear()
    return {"ok": True, "message": "every run, approval and event removed"}


@app.get("/")
def home():
    index = UI_DIRECTORY / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "the interface is missing"}, status_code=404)
    return FileResponse(index)


if UI_DIRECTORY.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIRECTORY)), name="ui")
