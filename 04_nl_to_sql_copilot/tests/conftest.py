"""
TESTS: SHARED SETUP
===================
Everything runs offline against the real warehouse file, opened read-only.

The database is not mocked. The whole point of this project is that writes fail
at the database level, and a mock cannot demonstrate that - it would only
demonstrate that the mock refuses writes.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["REQUIRE_API_KEY"] = "false"
os.environ["MAX_ROWS"] = "500"
os.environ["QUERY_TIMEOUT_SECONDS"] = "5"
os.environ["MAX_JOINS"] = "6"
os.environ["MAX_REPAIR_ATTEMPTS"] = "2"

import pytest  # noqa: E402

from sql_copilot.layer0_shared.llm_client import OfflineSqlGenerator  # noqa: E402
from sql_copilot.layer0_shared.metrics import metrics  # noqa: E402
from sql_copilot.layer1_config.settings import settings  # noqa: E402
from sql_copilot.layer3_database.connection import (  # noqa: E402
    build_writable_connection,
    get_database,
)
from sql_copilot.layer3_database.schema_sql import ALLOWED_TABLES  # noqa: E402
from sql_copilot.layer3_database.seed_data import build_everything  # noqa: E402
from sql_copilot.layer9_api.copilot_service import CopilotService  # noqa: E402


def ensure_warehouse_exists() -> None:
    """Build the database once, if the test run needs it."""
    path = settings.sqlite_file()
    if path.exists():
        return
    connection = build_writable_connection(path)
    build_everything(connection)
    connection.close()


ensure_warehouse_exists()


@pytest.fixture()
def database():
    metrics.reset()
    return get_database()


@pytest.fixture()
def allowed_tables():
    return ALLOWED_TABLES


@pytest.fixture()
def service(database):
    metrics.reset()
    return CopilotService(database=database, model=OfflineSqlGenerator())


@pytest.fixture()
def api_client(database):
    from fastapi.testclient import TestClient

    from sql_copilot.layer9_api.main import create_app
    from sql_copilot.layer9_api.routes import reset_service

    reset_service()
    with TestClient(create_app()) as client:
        yield client
    reset_service()
