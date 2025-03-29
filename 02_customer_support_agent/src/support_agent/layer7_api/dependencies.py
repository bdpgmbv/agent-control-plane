"""
LAYER 7 - API: SHARED OBJECTS
=============================
Built once at start-up and reused, because opening a database connection and an
HTTP client on every request is waste you notice at load.

Everything is behind a function so the tests can swap any piece.
"""

from support_agent.layer0_shared.llm_client import build_chat_client
from support_agent.layer3_storage.database import get_database
from support_agent.layer6_agent.agent import SupportAgent

_chat_client = None
_agent: SupportAgent | None = None


def get_chat_client():
    global _chat_client
    if _chat_client is None:
        _chat_client = build_chat_client()
    return _chat_client


def get_agent() -> SupportAgent:
    global _agent
    if _agent is None:
        _agent = SupportAgent(database=get_database(), chat_client=get_chat_client())
    return _agent


def reset_shared_objects() -> None:
    global _chat_client, _agent
    _chat_client = None
    _agent = None
