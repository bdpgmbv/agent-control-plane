"""
LAYER 5 - RETRIEVAL, STEP 5: RERANKING
======================================
Fusion gives us up to 25 candidates. Only about 5 fit in the prompt. Choosing
the right 5 is what reranking does.

How it differs from search: search compares the question to a SUMMARY of the
passage (a vector, or a bag of words). Reranking looks at the question and the
passage TOGETHER and asks "does this actually answer it?". That costs more, so it
only ever runs on a shortlist - which is why it comes last.

--------------------------------------------------------------------------------
ONE IMPORTANT DESIGN RULE: the reranker must produce an ABSOLUTE score.
--------------------------------------------------------------------------------
Search produces a RANKING: "these are the best five we have". A ranking can
never tell you that all five are useless, because something is always best.

Reranking produces a JUDGEMENT: "this one is 0.8 relevant, that one is 0.05".
That is what lets the next layer say "I don't know" instead of confidently
answering from irrelevant text. Ranks are relative; honesty needs absolutes.

Two implementations:

  Live    - the model scores every candidate from 0 to 10 in a single call.
  Offline - two kinds of absolute evidence, and we take whichever is stronger:
              lexical  - how much of the query's vocabulary the passage covers
              semantic - the raw cosine, rescaled with the embedder's
                         calibration pair (see layer0_shared/embeddings.py)
            Lexical coverage is weighted by how RARE each word is, so matching
            "policy" in a corpus of policies counts for almost nothing. See
            term_weights.py for why that mattered.
            "Either kind of evidence is enough" is the right rule for hybrid
            search: a passage may match by wording OR by meaning.
"""

import json

from rag_assistant.layer0_shared.embeddings import rescale_similarity
from rag_assistant.layer0_shared.logging_setup import get_logger
from rag_assistant.layer0_shared.text_tools import shorten, to_stems
from rag_assistant.layer2_models.schemas import ScoredChunk
from rag_assistant.layer5_retrieval.term_weights import CorpusTermWeights

log = get_logger(__name__)

RERANK_SYSTEM_PROMPT = """You score how well each numbered passage answers the user's question.

Scoring guide:
  10 = contains the complete answer
   7 = contains most of the answer
   4 = related topic but does not actually answer it
   0 = irrelevant

Judge only what the passage says. Do not use outside knowledge.
Score every passage you are given.
Reply with JSON only: {"scores": [{"id": 1, "score": 8}, {"id": 2, "score": 0}]}"""

# The extra signals are hints, not evidence. They MULTIPLY the evidence rather
# than adding to it, which is the whole point:
#
#   additive       0.0 evidence + 0.18 of hints = 0.18, which can clear a
#                  threshold. Hints have invented relevance out of nothing.
#   multiplicative 0.0 evidence x 1.18 = 0.0. A hint can only amplify evidence
#                  that already exists.
#
# This is what finally stopped the system answering questions it had no source
# for. Getting it wrong is subtle, because the scores still look reasonable.
AGREEMENT_BONUS = 0.08
HEADING_BONUS_WEIGHT = 0.10


def lexical_coverage(
    question: str,
    passage_text: str,
    term_weights: CorpusTermWeights,
) -> float:
    """
    How much of the QUESTION's information the passage covers.

    ------------------------------------------------------------------------
    WHY THIS USES THE USER'S OWN QUESTION AND NOT THE REWRITTEN QUERIES
    ------------------------------------------------------------------------
    Rewriting and scoring have different jobs:

        rewriting -> FINDING candidates. Extra phrasings widen the net, which
                     raises recall. More candidates is good.
        scoring   -> JUDGING candidates. Here the question is "how well does
                     this passage answer what the person actually asked?"

    Scoring the expanded query broke that. The rewrite added
    "salary compensation band" to a question about engineer pay; the passage
    "orders below 50 dollars pay a flat fee" then matched all three of those
    words through the same single word "pay", and coverage jumped from 0.13 to
    0.60 on a passage about shipping fees.

    So: rewrite to find, then judge against the real question, and let
    synonym-aware matching (see term_weights.py) bridge the vocabulary gap. The
    rewrite is not wasted - it is what put the right passage on the shortlist.
    """
    return term_weights.weighted_coverage(to_stems(question), to_stems(passage_text))


def heuristic_rerank(
    question: str,
    candidates: list[ScoredChunk],
    relevance_floor: float,
    relevance_ceiling: float,
    term_weights: CorpusTermWeights,
    semantic_trust: float = 1.0,
) -> list[ScoredChunk]:
    """Rerank with no model call, using absolute evidence only."""
    for candidate in candidates:
        lexical_evidence = lexical_coverage(question, candidate.chunk.text, term_weights)

        raw_cosine = candidate.vector_score
        if raw_cosine is None:
            semantic_evidence = 0.0
        else:
            rescaled = rescale_similarity(raw_cosine, relevance_floor, relevance_ceiling)
            semantic_evidence = semantic_trust * rescaled

        # Either kind of evidence is enough on its own.
        if lexical_evidence > semantic_evidence:
            evidence = lexical_evidence
        else:
            evidence = semantic_evidence

        # Hints. Both searches agreeing is a good sign; so is a matching heading.
        multiplier = 1.0
        if candidate.vector_rank is not None and candidate.keyword_rank is not None:
            multiplier = multiplier + AGREEMENT_BONUS

        heading = candidate.chunk.metadata.get("heading", "")
        if isinstance(heading, str) and heading != "":
            heading_coverage = lexical_coverage(question, heading, term_weights)
            multiplier = multiplier + (HEADING_BONUS_WEIGHT * heading_coverage)

        score = evidence * multiplier
        if score > 1.0:
            score = 1.0

        candidate.rerank_score = round(score, 4)

    # `or 0.0` because rerank_score is optional on the model: a candidate
    # that was never reranked sorts last instead of raising TypeError
    # part-way through a comparison.
    candidates.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
    return candidates


def model_rerank(question: str, candidates: list[ScoredChunk], chat_client, usage=None) -> list[ScoredChunk]:
    """Rerank with one model call covering the whole shortlist."""
    if len(candidates) == 0:
        return candidates

    lines: list[str] = []
    position = 0
    for candidate in candidates:
        position = position + 1
        lines.append(f"[{position}] {shorten(candidate.chunk.text, 600)}")

    user_prompt = "Question: " + question + "\n\nPassages:\n" + "\n\n".join(lines)

    result = chat_client.complete(
        system_prompt=RERANK_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        json_mode=True,
    )
    if usage is not None:
        usage.add_model_call(
            "rerank", result.prompt_tokens, result.completion_tokens, result.cost_usd
        )

    parsed = json.loads(result.text)

    score_by_position: dict[int, float] = {}
    for entry in parsed.get("scores", []):
        identifier = entry.get("id")
        raw_score = entry.get("score")
        if isinstance(identifier, int) and isinstance(raw_score, int | float):
            score_by_position[identifier] = float(raw_score) / 10.0

    if len(score_by_position) == 0:
        raise ValueError("the model returned no usable scores")

    position = 0
    for candidate in candidates:
        position = position + 1
        if position in score_by_position:
            candidate.rerank_score = round(score_by_position[position], 4)
        else:
            # The model skipped it. Score it 0 rather than guessing high:
            # a passage the judge ignored is not evidence.
            candidate.rerank_score = 0.0

    # `or 0.0` because rerank_score is optional on the model: a candidate
    # that was never reranked sorts last instead of raising TypeError
    # part-way through a comparison.
    candidates.sort(key=lambda item: item.rerank_score or 0.0, reverse=True)
    return candidates


def rerank(
    question: str,
    candidates: list[ScoredChunk],
    chat_client,
    use_model: bool,
    relevance_floor: float,
    relevance_ceiling: float,
    term_weights: CorpusTermWeights,
    semantic_trust: float = 1.0,
    usage=None,
) -> list[ScoredChunk]:
    """
    Pick a reranking strategy.

    If the model call fails for any reason we fall back to the heuristic. A
    reranking failure should degrade quality, never break the request.
    """
    if use_model and chat_client.is_live:
        try:
            return model_rerank(question, candidates, chat_client, usage)
        except Exception as error:
            log.warning("model rerank failed, falling back to the heuristic: %s", error)

    return heuristic_rerank(
        question,
        candidates,
        relevance_floor,
        relevance_ceiling,
        term_weights,
        semantic_trust,
    )
