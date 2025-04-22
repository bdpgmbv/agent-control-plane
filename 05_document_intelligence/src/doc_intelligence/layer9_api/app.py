"""
LAYER 9 - THE HTTP API
======================
FastAPI over the pipeline, plus the single-page interface in web/.

Two endpoints deserve a note.

`POST /api/process/samples` runs the whole sample folder in one go, which is how
the interface shows the batch numbers - straight-through rate, errors caught,
cost. Those are the numbers a finance team would actually ask about, and they only
mean anything across a batch. A single document cannot have a straight-through
rate.

`POST /api/reviews/{id}/decide` is where a correction goes in, and it returns
which issues the correction fixed and which are still outstanding. That return
value is the feature: a reviewer finds out immediately whether their fix made the
invoice add up, instead of saving and hoping.
"""

import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from doc_intelligence.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from doc_intelligence.layer1_config.settings import SETTINGS, find_beside_project
from doc_intelligence.layer2_models.schemas import ProcessTextRequest, ReviewDecision
from doc_intelligence.layer3_ingest.step1_load import UnsupportedDocument
from doc_intelligence.layer8_review.step1_store import DocumentStore
from doc_intelligence.layer8_review.step2_queue import apply_review_decision
from doc_intelligence.layer9_api.pipeline import DocumentPipeline

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_DIRECTORY = find_beside_project("ui")

app = FastAPI(
    title="Document Intelligence Pipeline",
    description="classify, extract, validate, and decide what a person needs to see",
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

store = DocumentStore(SETTINGS.sqlite_path)
pipeline = DocumentPipeline(store=store)


def check_api_key(x_api_key: str = Header(default="")) -> str:
    """
    Optional key checking, off by default so the demo runs with no setup.

    When REQUIRE_API_KEY is on, the role attached to the key is returned and
    used to decide who may resolve a review. A pipeline that lets anyone approve
    a payment is not a pipeline anyone should run.
    """
    if not SETTINGS.require_api_key:
        return "reviewer"

    roles = SETTINGS.api_key_roles()
    if x_api_key not in roles:
        raise HTTPException(status_code=401, detail="send a valid X-API-Key header")
    return roles[x_api_key]


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "offline" if pipeline.is_offline() else "live",
        "model": SETTINGS.llm_model if not pipeline.is_offline() else None,
        "gates": {
            "auto_approve_confidence": SETTINGS.auto_approve_confidence,
            "min_required_field_confidence": SETTINGS.min_required_field_confidence,
            "always_review_above_amount": SETTINGS.always_review_above_amount,
            "arithmetic_tolerance": SETTINGS.arithmetic_tolerance,
        },
        "counts": store.counts_by_decision(),
        "pending_reviews": len(store.pending_reviews()),
    }


@app.get("/api/samples")
def list_samples() -> dict:
    directory = Path(SETTINGS.samples_directory)
    names = []
    if directory.exists():
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.name.startswith("."):
                names.append(path.name)
    return {"samples": names}


@app.get("/api/samples/{name}")
def read_sample(name: str) -> dict:
    # Resolve and confirm the file is really inside the samples folder, so a
    # name like "../.env" cannot be used to read the API key.
    directory = Path(SETTINGS.samples_directory).resolve()
    path = (directory / name).resolve()
    if directory not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="no sample called %r" % name)
    return {"name": name, "text": path.read_text(encoding="utf-8", errors="replace")}


@app.post("/api/process/text")
def process_text(request: ProcessTextRequest, role: str = Depends(check_api_key)) -> dict:
    if request.text.strip() == "":
        raise HTTPException(status_code=400, detail="there is no text to process")
    result = pipeline.process_text(request.text, request.filename)
    return {"result": result.model_dump(mode="json")}


@app.post("/api/process/upload")
async def process_upload(file: UploadFile = File(...),
                         role: str = Depends(check_api_key)) -> dict:
    payload = await file.read()
    if len(payload) == 0:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")
    try:
        result = pipeline.process_upload(file.filename or "upload", payload)
    except UnsupportedDocument as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"result": result.model_dump(mode="json")}


@app.post("/api/process/samples")
def process_samples(role: str = Depends(check_api_key)) -> dict:
    directory = Path(SETTINGS.samples_directory)
    if not directory.exists():
        raise HTTPException(status_code=404, detail="there is no samples folder")

    results, summary = pipeline.process_directory(directory)

    rows = []
    for result in results:
        reason = ""
        if len(result.decision_reasons) > 0:
            reason = result.decision_reasons[0]
        rows.append({
            "document_id": result.document_id,
            "filename": result.filename,
            "document_type": result.document_type.value,
            "confidence": result.confidence,
            "decision": result.decision.value,
            "errors": len(result.errors()),
            "warnings": len(result.warnings()),
            "reason": reason,
            "model_calls": result.model_calls,
            "cost_usd": result.cost_usd,
            "seconds": result.seconds,
        })

    return {"summary": summary.model_dump(mode="json"), "documents": rows}


@app.get("/api/documents")
def recent_documents(limit: int = 50) -> dict:
    return {"documents": store.recent_documents(limit)}


@app.get("/api/documents/{document_id}")
def one_document(document_id: str) -> dict:
    result = store.load_result(document_id)
    if result is None:
        raise HTTPException(status_code=404, detail="no document %r" % document_id)
    return {
        "result": result.model_dump(mode="json"),
        "audit": store.audit_for(document_id),
    }


@app.get("/api/reviews")
def pending_reviews(limit: int = 100) -> dict:
    items = store.pending_reviews(limit)
    rows = []
    for item in items:
        rows.append(item.model_dump(mode="json"))
    return {"reviews": rows}


@app.post("/api/reviews/{review_id}/decide")
def decide_review(review_id: str, decision: ReviewDecision,
                  role: str = Depends(check_api_key)) -> JSONResponse:
    if SETTINGS.require_api_key and role != "reviewer":
        raise HTTPException(
            status_code=403,
            detail="the '%s' role may process documents but not resolve reviews" % role,
        )

    outcome = apply_review_decision(
        store, review_id, decision.action,
        actor=role if SETTINGS.require_api_key else "reviewer",
        corrections=decision.corrections,
        note=decision.note,
        revalidate=pipeline.revalidate,
    )

    body = {
        "ok": outcome.ok,
        "message": outcome.message,
        "fixed_issues": outcome.fixed_issues,
        "remaining_issues": outcome.remaining_issues,
    }
    if outcome.result is not None:
        body["result"] = outcome.result.model_dump(mode="json")
    if outcome.review is not None:
        body["review"] = outcome.review.model_dump(mode="json")

    status = 200 if outcome.ok else 400
    return JSONResponse(status_code=status, content=body)


@app.post("/api/reset")
def reset(role: str = Depends(check_api_key)) -> dict:
    """Wipe the store so a demo can be run again from nothing."""
    store.clear()
    return {"ok": True, "message": "every document, review and audit entry removed"}


@app.get("/")
def home():
    index = UI_DIRECTORY / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "the web interface is missing"}, status_code=404)
    return FileResponse(index)


if UI_DIRECTORY.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIRECTORY)), name="ui")
