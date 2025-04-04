"""
LAYER 8 - API: THE ENDPOINTS
============================
Thin. Everything interesting happened in layers 4 to 7.

The one design note worth making: /api/research runs the whole thing and returns
when it is done, which can take twenty-five seconds with a real model. That is
honest rather than ideal - the budget's time limit is what stops it being worse.
A production version would return a run id immediately and stream progress; the
trace object is already shaped for that.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, status

from research_agent.layer0_shared.logging_setup import get_logger
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer1_config.settings import ApiKeyRecord, settings
from research_agent.layer2_models.schemas import ResearchRequest, ResearchResponse
from research_agent.layer3_sources.registry import get_sources
from research_agent.layer8_api.research_service import ResearchService

router = APIRouter()
log = get_logger(__name__)

_service: ResearchService | None = None

OPEN_ACCESS = ApiKeyRecord(key="open", role="admin")


def get_service() -> ResearchService:
    """Built once: it holds the model client and the embedder."""
    global _service
    if _service is None:
        _service = ResearchService()
    return _service


def reset_service() -> None:
    global _service
    _service = None


async def require_caller(x_api_key: str | None = Header(default=None)) -> ApiKeyRecord:
    if not settings.require_api_key:
        return OPEN_ACCESS

    if x_api_key is None or x_api_key.strip() == "":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key header.")

    record = settings.find_api_key(x_api_key.strip())
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown API key.")
    return record


@router.post("/api/research", response_model=ResearchResponse)
async def research(
    request: ResearchRequest,
    caller: ApiKeyRecord = Depends(require_caller),
) -> ResearchResponse:
    """Research one question and return a cited report."""
    if request.question.strip() == "":
        raise HTTPException(status_code=400, detail="The question is empty.")

    if len(request.question) > 500:
        raise HTTPException(status_code=400, detail="The question is too long (500 characters maximum).")

    try:
        return get_service().research(request)
    except Exception as error:
        metrics.increment("failures_total")
        log.exception("research run failed")
        raise HTTPException(status_code=500, detail="The research run failed: %s" % error) from error


@router.get("/api/health")
async def health() -> dict:
    source_descriptions: list[dict] = []
    for source in get_sources():
        source_descriptions.append(source.describe())

    return {"status": "ok", "sources": source_descriptions}


@router.get("/api/config")
async def configuration() -> dict:
    description = settings.describe()

    # Say plainly whether similarity is measured by meaning or by words, because
    # it changes how much the deduplication and conflict detection can be trusted.
    if settings.using_real_llm():
        description["similarity_measured_by"] = "meaning (embeddings)"
    else:
        description["similarity_measured_by"] = "words (no API key)"

    return description


@router.get("/api/sources")
async def list_sources() -> dict:
    """
    Every document in the corpus.

    Worth showing: a reader who can see the sources can check that a conflict the
    report surfaced is a real conflict, and that a citation points where it says.
    """
    from research_agent.layer3_sources.local_corpus import LocalCorpusSource

    documents: list[dict] = []
    for source in get_sources():
        if not isinstance(source, LocalCorpusSource):
            continue

        for document in source.documents:
            documents.append(
                {
                    "document_id": document.document_id,
                    "title": document.title,
                    "source_name": document.source_name,
                    "source_type": document.source_type.value,
                    "credibility": document.credibility(),
                    "published_date": document.published_date,
                    "url": document.url,
                    "topics": document.topics,
                    "body": document.body,
                }
            )

    return {"documents": documents, "count": len(documents)}


@router.get("/api/metrics")
async def read_metrics() -> dict:
    return metrics.snapshot()
