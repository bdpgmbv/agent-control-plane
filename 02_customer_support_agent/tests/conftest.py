"""
TESTS: SHARED SETUP
===================
Everything here runs before the application is imported, because layer 1 reads
its configuration once at import time.

The whole suite runs offline: no API key, no network, no bill. The rule-based
planner drives the real agent loop, so tool permissions, validation, retries,
idempotency, escalation and the audit log are all genuinely exercised.
"""

import os
import tempfile
from pathlib import Path

TEMPORARY_FOLDER = Path(tempfile.mkdtemp(prefix="support-tests-"))

os.environ["LLM_PROVIDER"] = "offline"
os.environ["SQLITE_PATH"] = str(TEMPORARY_FOLDER / "test.db")
os.environ["REQUIRE_API_KEY"] = "true"
os.environ["API_KEYS"] = (
    "alice-key:customer:CUST-1001,bob-key:customer:CUST-1002,staff-key:agent_human:*"
)
os.environ["REFUND_AUTO_APPROVE_LIMIT"] = "50.00"
os.environ["REFUND_HARD_LIMIT"] = "2000.00"
os.environ["MAX_TOOL_STEPS"] = "5"
os.environ["TOOL_MAX_ATTEMPTS"] = "3"
os.environ["TOOL_RETRY_BASE_SECONDS"] = "0.01"     # keep the tests fast
os.environ["ESCALATE_AFTER_TOOL_FAILURES"] = "2"
os.environ["ESCALATE_AFTER_TURNS"] = "12"
os.environ["REDACT_PII_IN_LOGS"] = "true"

import pytest  # noqa: E402

from support_agent.layer0_shared.llm_client import OfflineRulePlanner  # noqa: E402
from support_agent.layer0_shared.metrics import metrics  # noqa: E402
from support_agent.layer1_config.settings import settings  # noqa: E402
from support_agent.layer3_storage.database import Database, set_database  # noqa: E402
from support_agent.layer3_storage.seed_data import seed  # noqa: E402
from support_agent.layer4_tools.base import ToolContext  # noqa: E402
from support_agent.layer4_tools.executor import ToolExecutor  # noqa: E402
from support_agent.layer6_agent.agent import SupportAgent  # noqa: E402

ALICE_HEADERS = {"X-API-Key": "alice-key"}
BOB_HEADERS = {"X-API-Key": "bob-key"}
STAFF_HEADERS = {"X-API-Key": "staff-key"}


@pytest.fixture()
def database():
    """A clean database with the demo data, for every test."""
    fresh = Database(settings.sqlite_file())
    fresh.initialise()
    fresh.reset_agent_data()
    seed(fresh, fresh=True)
    set_database(fresh)
    metrics.reset()
    yield fresh
    fresh.reset_agent_data()


@pytest.fixture()
def alice():
    return settings.find_api_key("alice-key")


@pytest.fixture()
def bob():
    return settings.find_api_key("bob-key")


@pytest.fixture()
def staff():
    return settings.find_api_key("staff-key")


@pytest.fixture()
def planner():
    return OfflineRulePlanner()


@pytest.fixture()
def agent(database, planner):
    return SupportAgent(database=database, chat_client=planner)


@pytest.fixture()
def executor(database):
    return ToolExecutor(database)


@pytest.fixture()
def alice_context(database, alice):
    return ToolContext(
        caller=alice,
        customer_id="CUST-1001",
        conversation_id="conv_test",
        database=database,
    )


@pytest.fixture()
def api_client(database):
    from fastapi.testclient import TestClient

    from support_agent.layer7_api.dependencies import reset_shared_objects
    from support_agent.layer7_api.main import create_app

    reset_shared_objects()
    application = create_app()
    with TestClient(application) as client:
        yield client
    reset_shared_objects()


def all_tool_names() -> list[str]:
    from support_agent.layer4_tools.registry import ALL_TOOLS

    names: list[str] = []
    for tool in ALL_TOOLS:
        names.append(tool.name)
    return names
