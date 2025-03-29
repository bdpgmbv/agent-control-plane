"""
Check that the OpenAI key in .env works, before running anything that costs money.

    python scripts/check_key.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from support_agent.layer0_shared.llm_client import build_chat_client  # noqa: E402
from support_agent.layer1_config.settings import settings  # noqa: E402
from support_agent.layer2_models.schemas import Message  # noqa: E402


def describe_key() -> str:
    """Show enough of the key to recognise it, never enough to use it."""
    key = settings.openai_api_key
    if key == "" or key.startswith("sk-paste"):
        return "(not set - still the placeholder)"
    if len(key) < 12:
        return "(set, but suspiciously short)"
    return key[:7] + "..." + key[-4:]


def main() -> None:
    print("")
    print("  key in .env  : %s" % describe_key())
    print("  LLM_PROVIDER : %s" % settings.llm_provider)
    print("  LLM_MODEL    : %s" % settings.llm_model)
    print("")

    if not settings.using_real_llm():
        print("Still OFFLINE. The rule-based planner will drive the agent loop.")
        print("Paste your key into .env and run this again for real tool calling.")
        print("")
        return

    client = build_chat_client()
    try:
        reply = client.respond(
            system_prompt="Reply with exactly the word: ready",
            messages=[Message(role="user", content="Say it.")],
            tools=None,
        )
        print("  OK  model=%s  reply=%r  tokens=%d+%d  cost=$%.8f  latency=%dms"
              % (reply.model, reply.text[:20], reply.prompt_tokens,
                 reply.completion_tokens, reply.cost_usd, reply.latency_ms))
        print("")
        print("Real tool calling is available. Try: make run")
    except Exception as error:
        print("  FAILED: %s" % error)
        print("")
        print("Usual causes: the key is wrong, it has no credit, or LLM_MODEL is misspelled.")
    print("")


if __name__ == "__main__":
    main()
