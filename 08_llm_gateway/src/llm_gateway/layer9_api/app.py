"""
LAYER 9 - THE HTTP API
======================
The gateway endpoint, the dashboard behind it, and the experiment controls.

`POST /v1/chat` is the only endpoint an application ever calls. Everything else
exists so a person can see what that endpoint has been doing - which is the
whole argument for having a gateway at all. Seven projects in this series each
reported a cost and a latency; this is the thing that would have produced those
numbers if they had all gone through one door.

`PATCH /v1/traces/{id}/score` is how a grade arrives after the fact. Serving and
grading are separate because the useful success metrics usually are: "did the
customer reply" is a good one and the answer comes tomorrow.
"""

import time

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from llm_gateway.layer0_shared.logging_setup import (
    current_request_id,
    get_logger,
    log_event,
    new_request_id,
    setup_logging,
)
from llm_gateway.layer1_config.settings import SETTINGS, find_beside_project
from llm_gateway.layer2_models.schemas import (
    ChatRequest,
    Experiment,
    Variant,
)
from llm_gateway.layer9_api.gateway import Gateway

UI_DIRECTORY = find_beside_project("ui")

app = FastAPI(
    title="LLM Gateway",
    description="one door for every model call: routing, fallback, cache, limits, cost, A/B",
    version="1.0.0",
)

setup_logging()
LOGGER = get_logger(__name__)
GATEWAY = Gateway()


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


def owner_for(api_key: str) -> str:
    """
    Turn an API key into the name the costs are recorded against.

    An unknown key is refused rather than defaulting to an anonymous bucket: a
    gateway whose costs cannot be attributed is a gateway that cannot answer the
    only question anybody asks it.
    """
    owners = SETTINGS.api_key_owners()
    if api_key in owners:
        return owners[api_key]
    raise HTTPException(status_code=401,
                        detail="send a known key in the X-API-Key header")


# ================================================================
#  The gateway
# ================================================================

# response_model=None because this returns either a dict or a JSONResponse
# depending on the outcome, and FastAPI otherwise tries to build a response
# model out of the return annotation - which mypy wanted widened and FastAPI
# wanted narrow. Saying "there is no single response model" satisfies both
# honestly, rather than lying to one of them.
@app.post("/v1/chat", response_model=None)
def chat(request: ChatRequest,
         x_api_key: str = Header(default="demo-key")) -> dict | JSONResponse:
    owner = owner_for(x_api_key)

    if request.prompt.strip() == "":
        raise HTTPException(status_code=400, detail="the prompt is empty")

    response = GATEWAY.handle(request, owner)

    if response.outcome.value == "refused":
        return JSONResponse(status_code=429,
                            content=response.model_dump(mode="json"))
    if response.outcome.value == "failed":
        return JSONResponse(status_code=502,
                            content=response.model_dump(mode="json"))
    return response.model_dump(mode="json")


class Score(BaseModel):
    score: float


@app.patch("/v1/traces/{request_id}/score")
def score_trace(request_id: str, body: Score) -> dict:
    if not GATEWAY.store.set_score(request_id, body.score):
        raise HTTPException(status_code=404, detail="no trace with that id")
    return {"ok": True, "request_id": request_id, "score": body.score}


@app.get("/v1/traces")
def list_traces(limit: int = 60, owner: str = "", experiment: str = "") -> dict:
    rows = []
    for trace in GATEWAY.store.recent_traces(limit, owner, experiment):
        rows.append(trace.model_dump(mode="json"))
    return {"traces": rows}


@app.get("/v1/traces/{request_id}")
def one_trace(request_id: str) -> dict:
    trace = GATEWAY.store.get_trace(request_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="no trace with that id")
    return trace.model_dump(mode="json")


# ================================================================
#  The dashboard
# ================================================================

@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "mode": "offline" if GATEWAY.offline else "live",
        "models": sorted(GATEWAY.providers.keys()),
        "routes": GATEWAY.routes.names(),
        "settings": {
            "cache_enabled": SETTINGS.cache_enabled,
            "semantic_threshold": SETTINGS.semantic_threshold(),
            "requests_per_minute": SETTINGS.requests_per_minute,
            "daily_budget_usd": SETTINGS.daily_budget_usd,
            "confidence_level": SETTINGS.confidence_level,
            "minimum_samples_per_arm": SETTINGS.minimum_samples_per_arm,
        },
        "cache_entries": GATEWAY.store.cache_size(),
    }


@app.get("/api/dashboard")
def dashboard(hours: float = 24.0) -> dict:
    totals = GATEWAY.store.totals(hours)
    requests = totals.get("requests", 0) or 0
    cache_hits = totals.get("cache_hits", 0) or 0

    return {
        "hours": hours,
        "totals": totals,
        "cache_hit_rate": round(cache_hits / requests, 4) if requests > 0 else 0.0,
        "latency": GATEWAY.store.latency_percentiles(hours),
        "by_model": GATEWAY.store.by_model(hours),
        "by_owner": GATEWAY.store.by_owner(hours),
    }


@app.get("/api/routes")
def routes() -> dict:
    rows = []
    for route in GATEWAY.routes.routes:
        rows.append(route.model_dump(mode="json"))
    return {"routes": rows}


# ================================================================
#  Experiments
# ================================================================

class NewExperiment(BaseModel):
    name: str
    question: str = ""
    variants: list[Variant] = []


@app.post("/api/experiments")
def create_experiment(body: NewExperiment) -> dict:
    if len(body.variants) != 2:
        raise HTTPException(
            status_code=400,
            detail=("this platform compares exactly two variants; comparing more "
                    "needs a correction for multiple testing that is not "
                    "implemented here"))
    experiment = Experiment(name=body.name, question=body.question,
                            variants=body.variants)
    GATEWAY.store.save_experiment(experiment)
    return {"ok": True, "experiment": experiment.model_dump(mode="json")}


@app.get("/api/experiments")
def list_experiments() -> dict:
    rows = []
    for experiment in GATEWAY.store.list_experiments():
        rows.append(experiment.model_dump(mode="json"))
    return {"experiments": rows}


@app.get("/api/experiments/{name}/results")
def experiment_results(name: str) -> dict:
    return GATEWAY.experiments.results(name).model_dump(mode="json")


class Simulation(BaseModel):
    """Run N graded requests through an experiment, so the numbers exist to look at."""

    requests: int = 60


@app.post("/api/experiments/{name}/simulate")
def simulate(name: str, body: Simulation,
             x_api_key: str = Header(default="demo-key")) -> dict:
    """
    Send traffic through an experiment and grade it.

    The questions are arithmetic word problems with checkable answers, so the
    grading is objective rather than a judgement. That is what makes the A/B
    machinery demonstrable offline - and it is also the honest way to run a real
    experiment wherever a checkable metric exists.
    """
    from llm_gateway.layer10_evaluation.dataset import grade, question_at

    experiment = GATEWAY.store.get_experiment(name)
    if experiment is None:
        raise HTTPException(status_code=404, detail="no experiment called %r" % name)

    owner_for(x_api_key)            # the caller still has to be known
    count = max(1, min(2000, body.requests))

    # A deliberate internal batch is not external traffic, so it runs under its
    # own name with a bucket sized for the run. The limits are not switched off
    # - the budget still applies and refusals are still counted and reported -
    # but a rate limit meant for one caller's ordinary traffic would otherwise
    # refuse three quarters of a measurement batch and make the experiment look
    # like it had no data, which is a confusing way to demonstrate a rate limit.
    simulation_owner = "simulation"
    bucket = GATEWAY.limiter.bucket_for(simulation_owner)
    bucket.capacity = float(count + 10)
    bucket.tokens = float(count + 10)

    graded = 0
    refused = 0
    failed = 0

    for index in range(count):
        # Vary the numbers so the cache does not answer every one of them, which
        # would make both arms identical and the comparison meaningless.
        prompt, expected = question_at(index, 10 + index)

        response = GATEWAY.handle(
            ChatRequest(prompt=prompt, task="general", experiment=name,
                        no_cache=True, max_tokens=200), simulation_owner)

        if response.outcome.value == "ok":
            GATEWAY.store.set_score(response.request_id, grade(response.text, expected))
            graded = graded + 1
        elif response.outcome.value == "refused":
            refused = refused + 1
        else:
            failed = failed + 1

    return {"ok": True, "sent": count, "graded": graded, "refused": refused,
            "failed": failed,
            "results": GATEWAY.experiments.results(name).model_dump(mode="json")}


@app.post("/api/reset")
def reset() -> dict:
    GATEWAY.store.clear()
    GATEWAY.limiter.buckets = {}
    return {"ok": True, "message": "every trace, cache entry and experiment removed"}


@app.get("/")
def home():
    index = UI_DIRECTORY / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "the interface is missing"}, status_code=404)
    return FileResponse(index)


if UI_DIRECTORY.exists():
    app.mount("/ui", StaticFiles(directory=str(UI_DIRECTORY)), name="ui")
