"""
LAYER 6 - GENERATION: BUILDING THE ANSWER
=========================================
Takes the retrieved passages and produces the final answer, with four guarantees
the model alone cannot give you:

  1. NO PASSAGES MEANS NO MODEL CALL.
     If retrieval found nothing, we abstain immediately. Calling the model with
     empty context invites it to answer from memory - and it costs money to be
     told something we already knew.

  2. EVERY CITATION IS REAL.
     The model writes [2]; we check that passage 2 exists and attach the actual
     text. An invented citation number is dropped, not shown to the user.

  3. AN ANSWER WITH NO CITATIONS IS NOT AN ANSWER.
     If the model ignored rule 2 of the prompt, we do not ship the text. We
     abstain instead. This is the difference between hoping the prompt is obeyed
     and enforcing it in code.

  4. EVERY ANSWER CARRIES A CONFIDENCE AND A GROUNDEDNESS SCORE,
     so the caller can decide what to do with a weak one.
"""

import time

from rag_assistant.layer0_shared.llm_client import ABSTAIN_SENTENCE
from rag_assistant.layer0_shared.logging_setup import get_logger, log_event
from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer0_shared.text_tools import shorten
from rag_assistant.layer0_shared.usage_tracker import UsageAccumulator
from rag_assistant.layer2_models.schemas import Citation, ScoredChunk, UsageReport
from rag_assistant.layer6_generation.groundedness import find_citation_markers, measure_groundedness
from rag_assistant.layer6_generation.prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt

log = get_logger(__name__)

# Phrases that mean the model chose to abstain, even if it did not use our exact
# sentence. Checked in lowercase.
ABSTAIN_PHRASES = [
    "i don't know",
    "i do not know",
    "cannot be determined from",
    "not mentioned in the provided",
    "the context does not",
    "no information in the provided",
]


class GeneratedAnswer:
    """Everything layer 6 produces for one question."""

    def __init__(
        self,
        answer_text: str,
        answered: bool,
        abstain_reason: str,
        citations: list[Citation],
        confidence: float,
        groundedness: float | None,
        usage: UsageReport,
        unsupported_sentences: list[str],
    ) -> None:
        self.answer_text = answer_text
        self.answered = answered
        self.abstain_reason = abstain_reason
        self.citations = citations
        self.confidence = confidence
        self.groundedness = groundedness
        self.usage = usage
        self.unsupported_sentences = unsupported_sentences


def looks_like_abstention(answer_text: str) -> bool:
    """Did the model decline to answer?"""
    lowered = answer_text.lower()
    for phrase in ABSTAIN_PHRASES:
        if phrase in lowered:
            return True
    return False


def build_citations(answer_text: str, passages: list[ScoredChunk]) -> tuple[list[Citation], int]:
    """
    Turn the [n] markers in the answer into real citations.

    Returns (citations, number of invented markers that were dropped).
    A marker is invented when it points at a passage that was never provided -
    the classic hallucinated footnote.
    """
    citations: list[Citation] = []
    invented = 0

    for marker in find_citation_markers(answer_text):
        position = marker - 1          # markers start at 1, lists start at 0
        if position < 0 or position >= len(passages):
            invented = invented + 1
            continue

        passage = passages[position]
        score = passage.rerank_score
        if score is None:
            score = passage.score

        citations.append(
            Citation(
                marker=marker,
                chunk_id=passage.chunk.chunk_id,
                document_title=passage.chunk.document_title,
                source=passage.chunk.source,
                quote=shorten(passage.chunk.text, 300),
                score=round(score, 4),
            )
        )

    return (citations, invented)


def compute_confidence(
    passages: list[ScoredChunk],
    citation_count: int,
    groundedness: float,
) -> float:
    """
    One number, from three honest signals:

        retrieval strength - how relevant the best passage was
        citation coverage  - did the answer actually lean on the passages
        groundedness       - is every sentence supported

    Deliberately not asked of the model. A model's stated confidence reflects how
    fluent its answer sounded, not whether it was right.
    """
    if len(passages) == 0:
        return 0.0

    top_score = passages[0].rerank_score
    if top_score is None:
        top_score = passages[0].score

    if citation_count == 0:
        citation_signal = 0.0
    elif citation_count == 1:
        citation_signal = 0.75
    else:
        citation_signal = 1.0

    confidence = (0.45 * top_score) + (0.20 * citation_signal) + (0.35 * groundedness)
    if confidence > 1.0:
        confidence = 1.0
    return round(confidence, 4)


def abstain(reason: str, usage: UsageReport) -> GeneratedAnswer:
    """Build the standard "I don't know" result."""
    metrics.increment("abstain_total")
    return GeneratedAnswer(
        answer_text=ABSTAIN_SENTENCE,
        answered=False,
        abstain_reason=reason,
        citations=[],
        confidence=0.0,
        groundedness=None,
        usage=usage,
        unsupported_sentences=[],
    )


class AnswerBuilder:
    """Produces the final answer from retrieved passages."""

    def __init__(self, chat_client) -> None:
        self.chat_client = chat_client

    def build_usage_report(self, accumulator: UsageAccumulator) -> UsageReport:
        """Turn the accumulator into the shape the API returns."""
        return UsageReport(
            prompt_tokens=accumulator.prompt_tokens,
            completion_tokens=accumulator.completion_tokens,
            embedding_tokens=accumulator.embedding_tokens,
            total_tokens=accumulator.total_tokens(),
            estimated_cost_usd=round(accumulator.cost_usd, 8),
            by_stage=dict(accumulator.by_stage),
        )

    def build(
        self,
        question: str,
        passages: list[ScoredChunk],
        retrieval_usage: UsageAccumulator | None = None,
    ) -> GeneratedAnswer:
        # Start from whatever retrieval already spent: the query embedding, and
        # in live mode the rewrite and rerank calls too.
        if retrieval_usage is None:
            retrieval_usage = UsageAccumulator()

        usage = self.build_usage_report(retrieval_usage)

        # --- guarantee 1: nothing retrieved, so do not call the model ---
        if len(passages) == 0:
            log_event(log, "generation.abstained", reason="no_relevant_passages", question=question)
            return abstain(
                "Nothing in the knowledge base scored above the relevance threshold.",
                usage,
            )

        started = time.perf_counter()

        user_prompt = build_answer_prompt(question, passages)
        result = self.chat_client.complete(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )

        retrieval_usage.add_model_call(
            "answer", result.prompt_tokens, result.completion_tokens, result.cost_usd
        )
        usage = self.build_usage_report(retrieval_usage)

        answer_text = result.text.strip()

        # --- the model chose to abstain ---
        if looks_like_abstention(answer_text):
            log_event(log, "generation.abstained", reason="model_declined", question=question)
            return abstain("The model judged that the passages do not answer this question.", usage)

        # --- guarantee 2: citations must point at real passages ---
        citations, invented_count = build_citations(answer_text, passages)
        if invented_count > 0:
            metrics.increment("invented_citations_total", invented_count)
            log_event(log, "generation.invented_citation", count=invented_count, question=question)

        # --- guarantee 3: no citations means no answer ---
        if len(citations) == 0:
            metrics.increment("uncited_answers_total")
            log_event(log, "generation.abstained", reason="no_citations", question=question)
            return abstain(
                "The model produced an answer without citing any source, so it "
                "could not be verified against the knowledge base.",
                usage,
            )

        # --- guarantee 4: score it ---
        cited_passages: list[ScoredChunk] = []
        for citation in citations:
            for passage in passages:
                if passage.chunk.chunk_id == citation.chunk_id:
                    cited_passages.append(passage)
                    break

        groundedness, unsupported = measure_groundedness(answer_text, cited_passages)
        confidence = compute_confidence(passages, len(citations), groundedness)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        usage.latency_ms = elapsed_ms

        metrics.observe("generation_latency_ms", elapsed_ms)
        metrics.observe("groundedness", groundedness)
        metrics.observe("tokens_per_request", usage.total_tokens)
        metrics.observe("cost_usd_per_request", usage.estimated_cost_usd)
        metrics.increment("answers_total")

        log_event(
            log,
            "generation.finished",
            question=question,
            citations=len(citations),
            groundedness=groundedness,
            confidence=confidence,
            unsupported_sentences=len(unsupported),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cost_usd=usage.estimated_cost_usd,
            latency_ms=elapsed_ms,
        )

        return GeneratedAnswer(
            answer_text=answer_text,
            answered=True,
            abstain_reason="",
            citations=citations,
            confidence=confidence,
            groundedness=groundedness,
            usage=usage,
            unsupported_sentences=unsupported,
        )

    def stream(self, question: str, passages: list[ScoredChunk], usage_sink: dict | None = None):
        """
        Yield the answer piece by piece for the UI.

        The checks above cannot run until the whole answer exists, so the caller
        collects the streamed text and then calls verify_streamed_answer().
        """
        if len(passages) == 0:
            yield ABSTAIN_SENTENCE
            return

        user_prompt = build_answer_prompt(question, passages)
        yield from self.chat_client.stream(
            system_prompt=ANSWER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            usage_sink=usage_sink,
        )

    def verify_streamed_answer(self, answer_text: str, passages: list[ScoredChunk]) -> dict:
        """
        Run the same checks on text that was already streamed to the user.

        Streaming forces this order: the user sees the words first, and the
        verdict arrives a moment later. The UI shows the verdict next to the
        answer, so a weakly grounded answer is still visible as one.
        """
        citations, invented_count = build_citations(answer_text, passages)

        cited_passages: list[ScoredChunk] = []
        for citation in citations:
            for passage in passages:
                if passage.chunk.chunk_id == citation.chunk_id:
                    cited_passages.append(passage)
                    break

        groundedness, unsupported = measure_groundedness(answer_text, cited_passages)
        confidence = compute_confidence(passages, len(citations), groundedness)

        citation_dicts: list[dict] = []
        for citation in citations:
            citation_dicts.append(citation.model_dump())

        return {
            "citations": citation_dicts,
            "invented_citations": invented_count,
            "groundedness": groundedness,
            "confidence": confidence,
            "unsupported_sentences": unsupported,
            "answered": len(citations) > 0 and not looks_like_abstention(answer_text),
        }
