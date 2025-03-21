"""
LAYER 5 - RETRIEVAL, STEP 1: QUERY REWRITING
============================================
The question a person types is usually not the best search query.

    person types : "can i get my money back after a month?"
    document says: "Customers may request a refund within 30 days"

Almost no words overlap. Rewriting bridges that gap by searching for several
phrasings and pooling the results.

Two implementations again:

  Live  - ask the model for alternative phrasings.
  Offline - rule based: strip the question words, then expand known synonyms.
            Free, instant, and it fixes the most common vocabulary mismatches.
"""

import json

from rag_assistant.layer0_shared.logging_setup import get_logger
from rag_assistant.layer0_shared.text_tools import to_keywords
from rag_assistant.layer0_shared.vocabulary import SYNONYM_GROUPS
from rag_assistant.layer1_config.settings import settings

log = get_logger(__name__)

REWRITE_SYSTEM_PROMPT = """You rewrite a user's question into search queries for a document search engine.

Rules:
- Produce 2 alternative phrasings that use the formal vocabulary a policy document would use.
- Keep every specific detail (numbers, product names, dates).
- Do not answer the question.
- Reply with JSON only, in the form {"queries": ["...", "..."]}"""

def expand_with_synonyms(question: str) -> str:
    """Add the formal words a document is likely to use. Never removes anything."""
    keywords = to_keywords(question)

    additions: list[str] = []
    for everyday_words, formal_words in SYNONYM_GROUPS:
        matched = False
        for keyword in keywords:
            if keyword in everyday_words:
                matched = True
                break
        if not matched:
            continue
        for formal_word in formal_words:
            if formal_word not in additions and formal_word not in keywords:
                additions.append(formal_word)

    if len(additions) == 0:
        return question
    return question + " " + " ".join(additions)


def keywords_only(question: str) -> str:
    """Just the meaningful words. Helps the keyword search, which hates filler."""
    return " ".join(to_keywords(question))


def rewrite_offline(question: str) -> list[str]:
    """The no-network rewriter."""
    variants: list[str] = []

    expanded = expand_with_synonyms(question)
    if expanded != question:
        variants.append(expanded)

    bare = keywords_only(question)
    if bare != "" and bare.lower() != question.lower():
        variants.append(bare)

    return variants


def rewrite_with_model(question: str, chat_client, usage=None) -> list[str]:
    """Ask the model for alternative phrasings. Falls back to the offline rules."""
    try:
        result = chat_client.complete(
            system_prompt=REWRITE_SYSTEM_PROMPT,
            user_prompt=question,
            json_mode=True,
        )
        if usage is not None:
            usage.add_model_call(
                "query_rewrite", result.prompt_tokens, result.completion_tokens, result.cost_usd
            )
        parsed = json.loads(result.text)
        raw_queries = parsed.get("queries", [])

        variants: list[str] = []
        for item in raw_queries:
            if isinstance(item, str) and item.strip() != "":
                variants.append(item.strip())
        if len(variants) > 0:
            return variants[:3]
    except Exception as error:
        # A rewrite failure must never fail the request: we still have the
        # original question, which is a perfectly good query.
        log.warning("query rewrite failed, using offline rules: %s", error)

    return rewrite_offline(question)


def build_query_variants(question: str, chat_client, usage=None) -> list[str]:
    """
    The list of queries to actually search with.
    The original question is always first, so it always has the strongest voice.
    """
    queries: list[str] = [question]

    if not settings.enable_query_rewrite:
        return queries

    if chat_client.is_live:
        extra = rewrite_with_model(question, chat_client, usage)
    else:
        extra = rewrite_offline(question)

    for variant in extra:
        if variant not in queries:
            queries.append(variant)

    return queries
