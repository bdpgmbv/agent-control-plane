"""
Check that the OpenAI key in .env actually works, before running anything big.

    python scripts/check_key.py

It makes two deliberately tiny calls - one embedding and one chat completion -
and prints what they cost. Total is a small fraction of a cent. Finding out your
key is wrong here is much better than finding out halfway through an evaluation
run.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from rag_assistant.layer0_shared.embeddings import build_embedder  # noqa: E402
from rag_assistant.layer0_shared.llm_client import build_chat_client  # noqa: E402
from rag_assistant.layer1_config.settings import settings  # noqa: E402
from rag_assistant.layer3_storage.factory import build_store  # noqa: E402
from rag_assistant.layer3_storage.index_guard import fingerprint_of  # noqa: E402


def describe_key() -> str:
    """Show enough of the key to recognise it, never enough to use it."""
    key = settings.openai_api_key
    if key == "" or key.startswith("sk-paste"):
        return "(not set - still the placeholder from .env.example)"
    if len(key) < 12:
        return "(set, but suspiciously short)"
    return key[:7] + "..." + key[-4:]


def main() -> None:
    print("")
    print("CONFIGURATION")
    print("  key in .env        : %s" % describe_key())
    print("  LLM_PROVIDER       : %s" % settings.llm_provider)
    print("  LLM_MODEL          : %s" % settings.llm_model)
    print("  EMBEDDING_PROVIDER : %s" % settings.embedding_provider)
    print("  EMBEDDING_MODEL    : %s" % settings.embedding_model)
    print("")

    if not settings.using_real_llm() and not settings.using_real_embeddings():
        print("Result: still running OFFLINE. Paste your key into .env and run this again.")
        print("")
        return

    total_cost = 0.0

    # --- embeddings ---
    print("TESTING EMBEDDINGS ...")
    try:
        embedder = build_embedder()
        result = embedder.embed(["a short test sentence"])
        total_cost = total_cost + result.cost_usd
        print("  OK   model=%s  dimensions=%d  tokens=%d  cost=$%.8f"
              % (result.model, len(result.vectors[0]), result.tokens, result.cost_usd))
    except Exception as error:
        print("  FAILED: %s" % error)
        print("")
        print("Common causes: the key is wrong, has no credit, or EMBEDDING_MODEL is misspelled.")
        return

    # --- chat ---
    print("TESTING THE CHAT MODEL ...")
    try:
        chat = build_chat_client()
        reply = chat.complete(
            system_prompt="Reply with exactly the word: ready",
            user_prompt="Say it.",
        )
        total_cost = total_cost + reply.cost_usd
        print("  OK   model=%s  reply=%r  tokens=%d+%d  cost=$%.8f  latency=%dms"
              % (reply.model, reply.text[:30], reply.prompt_tokens,
                 reply.completion_tokens, reply.cost_usd, reply.latency_ms))
    except Exception as error:
        print("  FAILED: %s" % error)
        print("")
        print("Common causes: the key is wrong, has no credit, or LLM_MODEL is misspelled.")
        return

    print("")
    print("Total cost of this check: $%.8f" % total_cost)
    print("")

    # --- does the index need rebuilding? ---
    store = build_store()
    stored = store.read_index_fingerprint()
    current = fingerprint_of(build_embedder())

    if stored != "" and stored != current and store.count_chunks() > 0:
        print("ACTION NEEDED")
        print("  The index was built with : %s" % stored)
        print("  You are now using        : %s" % current)
        print("  Old vectors cannot be compared with new ones. Rebuild it:")
        print("      python scripts/reindex.py")
    else:
        print("The index matches the configured embedder. Nothing else to do.")
    print("")


if __name__ == "__main__":
    main()
