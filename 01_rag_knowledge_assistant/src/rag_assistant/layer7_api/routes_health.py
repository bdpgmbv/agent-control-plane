"""
LAYER 7 - API: HEALTH AND CONFIGURATION
=======================================
/api/health tells a load balancer whether to send traffic here.
/api/config tells the UI what this deployment is actually configured to do,
so the screen can show "running offline, no API key" instead of pretending.

Neither endpoint ever returns a secret.
"""

from fastapi import APIRouter

from rag_assistant.layer1_config.settings import settings
from rag_assistant.layer3_storage.factory import get_store
from rag_assistant.layer7_api.dependencies import get_cache

router = APIRouter()


@router.get("/api/health")
async def health() -> dict:
    """Is the process up and is storage reachable?"""
    store_ok = True
    store_error = ""
    chunk_count = 0

    try:
        chunk_count = get_store().count_chunks()
    except Exception as error:
        store_ok = False
        store_error = str(error)

    if store_ok:
        status_text = "ok"
    else:
        status_text = "degraded"

    return {
        "status": status_text,
        "storage_reachable": store_ok,
        "storage_error": store_error,
        "chunks_indexed": chunk_count,
    }


@router.get("/api/config")
async def configuration() -> dict:
    """What this deployment is configured to do. Shown in the UI header."""
    description = settings.describe()
    description["cache"] = get_cache().describe()
    description["retrieval"] = {
        "chunk_size_words": settings.chunk_size_words,
        "chunk_overlap_words": settings.chunk_overlap_words,
        "vector_top_k": settings.vector_top_k,
        "keyword_top_k": settings.keyword_top_k,
        "rerank_top_k": settings.rerank_top_k,
        "min_relevance_score": settings.min_relevance_score,
    }
    description["require_api_key"] = settings.require_api_key
    return description
