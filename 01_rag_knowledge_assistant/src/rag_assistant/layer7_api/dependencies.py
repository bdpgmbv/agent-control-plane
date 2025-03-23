"""
LAYER 7 - API: SHARED OBJECTS
=============================
The store, the embedder, the model client and the cache are all expensive to
create, so we create them once when the process starts and hand the same
instances to every request.

Everything is behind a function rather than a module-level variable so the tests
can replace any piece with a temporary one.
"""

from rag_assistant.layer0_shared.cache import build_cache
from rag_assistant.layer0_shared.embeddings import build_embedder
from rag_assistant.layer0_shared.llm_client import build_chat_client
from rag_assistant.layer3_storage.factory import get_store
from rag_assistant.layer4_ingestion.step4_pipeline import IngestionPipeline
from rag_assistant.layer7_api.assistant_service import AssistantService

_embedder = None
_chat_client = None
_cache = None
_service: AssistantService | None = None
_ingestion: IngestionPipeline | None = None


def get_embedder():
    global _embedder
    if _embedder is None:
        _embedder = build_embedder()
    return _embedder


def get_chat_client():
    global _chat_client
    if _chat_client is None:
        _chat_client = build_chat_client()
    return _chat_client


def get_cache():
    global _cache
    if _cache is None:
        _cache = build_cache()
    return _cache


def get_service() -> AssistantService:
    global _service
    if _service is None:
        _service = AssistantService(
            store=get_store(),
            embedder=get_embedder(),
            chat_client=get_chat_client(),
            cache=get_cache(),
        )
    return _service


def get_ingestion() -> IngestionPipeline:
    global _ingestion
    if _ingestion is None:
        _ingestion = IngestionPipeline(store=get_store(), embedder=get_embedder())
    return _ingestion


def reset_shared_objects() -> None:
    """Forget every cached instance. Used by the tests between cases."""
    global _embedder, _chat_client, _cache, _service, _ingestion
    _embedder = None
    _chat_client = None
    _cache = None
    _service = None
    _ingestion = None
