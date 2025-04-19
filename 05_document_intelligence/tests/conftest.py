"""
Shared test setup.

The environment is forced offline HERE, at the top of conftest, before any app
module is imported. That ordering matters: settings.py reads the environment at
import time, and python-dotenv does not overwrite variables that are already set,
so this wins over whatever is in .env.

The whole suite therefore runs with no API key, no network and no cost, on a
machine that has never been configured. A test suite that only passes when
someone has topped up an account is a test suite that will be skipped.
"""

import os

os.environ["LLM_PROVIDER"] = "offline"
os.environ["OPENAI_API_KEY"] = ""
os.environ["SQLITE_PATH"] = ":memory:"
os.environ["REQUIRE_API_KEY"] = "false"

import sys
from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from doc_intelligence.layer3_ingest.step1_load import load_from_path
from doc_intelligence.layer3_ingest.step2_clean import clean_document_text
from doc_intelligence.layer8_review.step1_store import DocumentStore
from doc_intelligence.layer9_api.pipeline import DocumentPipeline

# A fixed "today" so date checks do not change their answer overnight.
TODAY = date(2026, 9, 25)

SAMPLES = PROJECT_ROOT / "samples"


@pytest.fixture
def store():
    made = DocumentStore(":memory:")
    yield made
    made.close()


@pytest.fixture
def pipeline(store):
    return DocumentPipeline(store=store, offline=True, today=TODAY)


@pytest.fixture
def sample_text():
    def read(name):
        return clean_document_text(load_from_path(SAMPLES / name).text).text
    return read


class FakeReply:
    def __init__(self, text):
        self.text = text
        self.input_tokens = 100
        self.output_tokens = 20
        self.model = "fake"


class FakeClient:
    """
    Stands in for OpenAIChatClient so the model paths can be tested without a key.

    It records what it was asked, which is how the tests check the thing that
    actually matters about those paths: that the model is only called when the
    deterministic path could not answer.
    """

    def __init__(self, replies=None, image_reply=""):
        self.replies = list(replies or [])
        self.image_reply = image_reply
        self.prompts = []
        self.image_calls = 0

    def complete(self, system_prompt, user_prompt, max_tokens=1200):
        self.prompts.append(user_prompt)
        if len(self.replies) == 0:
            return FakeReply("")
        return FakeReply(self.replies.pop(0))

    def read_image(self, system_prompt, user_prompt, image_base64, media_type,
                   max_tokens=1500):
        self.image_calls = self.image_calls + 1
        return FakeReply(self.image_reply)
