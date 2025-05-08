"""
Shared test setup.

Forced offline at the top of this file, before any app module is imported,
because settings are read at import time and python-dotenv does not overwrite
variables that are already set. The whole suite runs with no key, no network and
no cost.

Every store here is `:memory:`. A workflow engine's tests that share a database
file would interfere with each other in exactly the way the engine is designed
to make safe - which would make the failures impossible to read.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["OPENAI_API_KEY"] = ""
os.environ["SQLITE_PATH"] = ":memory:"

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from enterprise_workflow.layer3_database.store import WorkflowStore
from enterprise_workflow.layer5_steps.step2_external import FAILURES
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY, WORKFLOWS
from enterprise_workflow.layer6_engine.step1_engine import Engine, EngineSettings
from enterprise_workflow.layer7_recovery.step1_compensate import Compensator

REQUEST = """Hiring Ada Lovelace as a backend engineer.
Her email will be ada.lovelace@example.com and she starts 2026-11-03.
Salary is 95000. She will need a headset.
Thanks, Grace Hopper"""

EXPENSIVE_REQUEST = REQUEST.replace("She will need a headset.",
                                    "She will need a laptop, a desk and a chair.")

TODAY = "2026-09-25"


def base_input(**extra) -> dict:
    run_input = {"request_text": REQUEST, "today_override": TODAY}
    for key, value in extra.items():
        run_input[key] = value
    return run_input


@pytest.fixture(autouse=True)
def reset_failure_budgets():
    """The simulated outages are process-wide; each test starts from none."""
    FAILURES.reset()
    yield
    FAILURES.reset()


@pytest.fixture
def store():
    made = WorkflowStore(":memory:")
    yield made
    made.close()


@pytest.fixture
def settings():
    # No real waiting in tests. The backoff curve is tested directly instead.
    return EngineSettings(lease_seconds=30.0, max_attempts=3,
                          retry_base_seconds=0.0, retry_max_seconds=0.0,
                          approval_required_above=2000.0)


@pytest.fixture
def engine(store, settings):
    compensator = Compensator(store, REGISTRY, None, settings)
    return Engine(store, REGISTRY, WORKFLOWS, settings, client=None,
                  worker_id="test-worker", compensator=compensator)


@pytest.fixture
def engine_without_cleanup(store, settings):
    """An engine that does not compensate, for testing failure on its own."""
    return Engine(store, REGISTRY, WORKFLOWS, settings, client=None,
                  worker_id="test-worker")
