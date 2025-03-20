"""
TESTS: SHARED SETUP
===================
Everything here runs BEFORE the application is imported, because layer 1 reads
its configuration once at import time.

The point of this file: the whole test suite runs with no API key, no Postgres,
no Redis and no network. A test suite you cannot run offline is a test suite you
stop running.
"""

import os
import tempfile
from pathlib import Path

# --- force offline, isolated settings before any app module is imported ---
TEMPORARY_FOLDER = Path(tempfile.mkdtemp(prefix="rag-tests-"))

os.environ["LLM_PROVIDER"] = "offline"
os.environ["EMBEDDING_PROVIDER"] = "offline"
os.environ["STORAGE_BACKEND"] = "sqlite"
os.environ["SQLITE_PATH"] = str(TEMPORARY_FOLDER / "test.db")
os.environ["CACHE_BACKEND"] = "memory"
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["API_KEYS"] = "test-admin:admin:public|internal|secret,test-user:user:public"
os.environ["MIN_RELEVANCE_SCORE"] = "0.35"

import pytest  # noqa: E402

from rag_assistant.layer0_shared.cache import build_cache  # noqa: E402
from rag_assistant.layer0_shared.embeddings import build_embedder  # noqa: E402
from rag_assistant.layer0_shared.llm_client import build_chat_client  # noqa: E402
from rag_assistant.layer0_shared.metrics import metrics  # noqa: E402
from rag_assistant.layer3_storage.factory import build_store, set_store  # noqa: E402
from rag_assistant.layer4_ingestion.step4_pipeline import IngestionPipeline  # noqa: E402
from rag_assistant.layer7_api.assistant_service import AssistantService  # noqa: E402

SAMPLES_FOLDER = Path(__file__).resolve().parents[1] / "samples"

SAMPLE_TAGS = {
    "refund_policy.md": "public",
    "shipping_policy.md": "public",
    "security_faq.md": "internal",
    "salary_bands.md": "secret",
}


@pytest.fixture()
def store():
    """A clean database for each test."""
    fresh = build_store()
    fresh.reset()
    set_store(fresh)
    metrics.reset()
    yield fresh
    fresh.reset()


@pytest.fixture()
def embedder():
    return build_embedder()


@pytest.fixture()
def chat_client():
    return build_chat_client()


@pytest.fixture()
def ingestion(store, embedder):
    return IngestionPipeline(store=store, embedder=embedder)


@pytest.fixture()
def seeded_store(store, ingestion):
    """A database already loaded with the four sample documents."""
    for path in sorted(SAMPLES_FOLDER.iterdir()):
        if path.name in SAMPLE_TAGS:
            ingestion.ingest_file(path, access_tag=SAMPLE_TAGS[path.name])
    return store


@pytest.fixture()
def service(seeded_store, embedder, chat_client):
    return AssistantService(
        store=seeded_store,
        embedder=embedder,
        chat_client=chat_client,
        cache=build_cache(),
    )


@pytest.fixture()
def api_client(seeded_store):
    """A FastAPI test client sharing the seeded database."""
    from fastapi.testclient import TestClient

    from rag_assistant.layer7_api.dependencies import reset_shared_objects
    from rag_assistant.layer7_api.main import create_app

    reset_shared_objects()
    application = create_app()
    with TestClient(application) as client:
        yield client
    reset_shared_objects()


ADMIN_HEADERS = {"X-API-Key": "test-admin"}
USER_HEADERS = {"X-API-Key": "test-user"}
