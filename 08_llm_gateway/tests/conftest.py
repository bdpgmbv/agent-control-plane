"""
Shared test setup.

Forced offline at the top, before any app module is imported, because settings
are read at import time and python-dotenv does not overwrite variables that are
already set. The whole suite runs with no key, no network and no cost.

The limits are set absurdly high here so that a test about caching is not
quietly measuring the rate limiter. Tests about limits set their own.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["OPENAI_API_KEY"] = ""
os.environ["SQLITE_PATH"] = ":memory:"
os.environ["REQUESTS_PER_MINUTE"] = "100000"
os.environ["BURST"] = "100000"
os.environ["DAILY_BUDGET_USD"] = "1000"

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from llm_gateway.layer3_storage.store import GatewayStore
from llm_gateway.layer9_api.gateway import Gateway


@pytest.fixture
def store():
    made = GatewayStore(":memory:")
    yield made
    made.close()


@pytest.fixture
def gateway(store):
    made = Gateway(store, offline=True)
    # Generous, so a test about something else is never measuring the limiter.
    made.limiter.requests_per_minute = 1e9
    made.limiter.burst = 1e9
    made.limiter.daily_budget_usd = 1e9
    yield made
    made.offline_provider.mend()
