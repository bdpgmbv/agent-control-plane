"""
TESTS: SHARED SETUP
===================
Everything runs offline: no API key, no network, no bill. The rule-based model
drives the real pipeline, so planning, parallel workers, budgets, deduplication,
conflict detection and report verification are all genuinely exercised.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["BUDGET_MAX_TOKENS"] = "120000"
os.environ["BUDGET_MAX_TOOL_CALLS"] = "40"
os.environ["BUDGET_MAX_SECONDS"] = "60"
os.environ["BUDGET_MAX_DEPTH"] = "2"
os.environ["BUDGET_MAX_PARALLEL_WORKERS"] = "4"
os.environ["BUDGET_MAX_SUBQUESTIONS"] = "5"
os.environ["REQUIRE_API_KEY"] = "false"

import pytest  # noqa: E402

from research_agent.layer0_shared.budget import Budget  # noqa: E402
from research_agent.layer0_shared.llm_client import OfflineResearchModel  # noqa: E402
from research_agent.layer0_shared.metrics import metrics  # noqa: E402
from research_agent.layer0_shared.similarity import ClaimSimilarityEngine  # noqa: E402
from research_agent.layer3_sources.local_corpus import LocalCorpusSource  # noqa: E402
from research_agent.layer8_api.research_service import ResearchService  # noqa: E402


@pytest.fixture()
def budget():
    metrics.reset()
    return Budget(max_tokens=10000, max_tool_calls=10, max_seconds=30, max_depth=2)


@pytest.fixture()
def corpus():
    return LocalCorpusSource()


@pytest.fixture()
def engine():
    """The word-based engine, which is what runs with no API key."""
    return ClaimSimilarityEngine(embedder=None)


@pytest.fixture()
def model():
    return OfflineResearchModel()


@pytest.fixture()
def service():
    metrics.reset()
    return ResearchService(chat_client=OfflineResearchModel(), embedder=None)


@pytest.fixture()
def api_client():
    from fastapi.testclient import TestClient

    from research_agent.layer8_api.main import create_app
    from research_agent.layer8_api.routes import reset_service

    reset_service()
    with TestClient(create_app()) as client:
        yield client
    reset_service()
