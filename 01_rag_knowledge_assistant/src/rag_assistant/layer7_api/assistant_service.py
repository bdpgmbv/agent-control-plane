"""
LAYER 7 - API: THE ASSISTANT SERVICE
====================================
The use case layer. It wires layers 3 to 6 into the one operation the product
actually performs:

    a question in  ->  a cited, scored answer out

Everything the HTTP routes need is here, which means the same code path is used
by the API, by the evaluation suite in layer 8, and by the tests. If they used
different code paths, your evaluation scores would not describe your product.

The cache lookup happens here, before retrieval, because a cache hit should skip
the entire pipeline - not just the model call.
"""

import time

from rag_assistant.layer0_shared.cache import build_cache_key, build_scope_key
from rag_assistant.layer0_shared.logging_setup import current_request_id, get_logger, log_event
from rag_assistant.layer0_shared.metrics import metrics
from rag_assistant.layer2_models.schemas import (
    AnswerResponse,
    AskRequest,
    UsageReport,
)
from rag_assistant.layer3_storage.index_guard import check_index_matches_embedder
from rag_assistant.layer5_retrieval.step6_pipeline import RetrievalPipeline
from rag_assistant.layer6_generation.answer_builder import AnswerBuilder

log = get_logger(__name__)


class AssistantService:
    """Answers questions. Owns the order the layers run in."""

    def __init__(self, store, embedder, chat_client, cache) -> None:
        self.store = store
        self.embedder = embedder
        self.chat_client = chat_client
        self.cache = cache
        self.retrieval = RetrievalPipeline(store=store, embedder=embedder, chat_client=chat_client)
        self.answer_builder = AnswerBuilder(chat_client=chat_client)
        self.index_has_been_checked = False

    def ensure_index_matches_embedder(self) -> None:
        """
        Check once per process that the stored vectors came from this embedder.

        Checked here rather than per query, because it is a configuration fact
        that cannot change while the process runs.
        """
        if self.index_has_been_checked:
            return
        check_index_matches_embedder(self.store, self.embedder)
        self.index_has_been_checked = True

    def cache_filters(self, allowed_tags: list[str], request: AskRequest) -> dict:
        """
        The filter fingerprint that is part of the cache key.

        The caller's allowed tags belong in here. Without them, an admin's answer
        built from secret documents could be served to a user who may only read
        public ones - a cache that leaks permissions.
        """
        return {
            "allowed_tags": sorted(allowed_tags),
            "requested_tags": sorted(request.filter_access_tags or []),
            "sources": sorted(request.filter_sources or []),
            "top_k": request.top_k,
        }

    def ask(self, request: AskRequest, allowed_tags: list[str]) -> AnswerResponse:
        started = time.perf_counter()
        metrics.increment("requests_total")

        self.ensure_index_matches_embedder()

        question = request.question.strip()
        if question == "":
            raise ValueError("The question is empty.")

        filters = self.cache_filters(allowed_tags, request)
        cache_key = build_cache_key(question, filters)
        # The scope keeps one caller's cached answers out of another caller's reach.
        scope = build_scope_key(filters)

        # ---------- 1. cache ----------
        if request.use_cache:
            cached = self.cache.get_exact(cache_key)
            if cached is not None:
                return self.finish_from_cache(cached, started, "exact")

            # A slightly different wording of the same question. Costs one short
            # embedding call to check, which is far cheaper than a cache miss.
            question_vector = self.embedder.embed([question]).vectors[0]
            similar = self.cache.get_similar(question_vector, scope=scope)
            if similar is not None:
                cached_value, similarity = similar
                log_event(log, "cache.semantic_hit", similarity=similarity, question=question)
                return self.finish_from_cache(cached_value, started, "semantic")
        else:
            question_vector = None

        # ---------- 2. retrieve ----------
        outcome = self.retrieval.retrieve(
            question=question,
            caller_allowed_tags=allowed_tags,
            requested_tags=request.filter_access_tags,
            sources=request.filter_sources,
            top_k=request.top_k,
        )

        # ---------- 3. generate ----------
        generated = self.answer_builder.build(
            question=question,
            passages=outcome.passages,
            retrieval_usage=outcome.usage,
        )

        total_ms = int((time.perf_counter() - started) * 1000)
        generated.usage.latency_ms = total_ms
        metrics.observe("request_latency_ms", total_ms)

        response = AnswerResponse(
            question=question,
            answer=generated.answer_text,
            answered=generated.answered,
            abstain_reason=generated.abstain_reason,
            confidence=generated.confidence,
            citations=generated.citations,
            groundedness=generated.groundedness,
            usage=generated.usage,
            trace=outcome.trace,
            request_id=current_request_id.get(),
        )

        # ---------- 4. remember ----------
        # Only cache answers we actually stand behind. Caching an abstention
        # would keep serving "I don't know" after the missing document is added.
        if request.use_cache and generated.answered:
            if question_vector is None:
                question_vector = self.embedder.embed([question]).vectors[0]
            self.cache.store(cache_key, response.model_dump(), question_vector, scope=scope)

        return response

    def finish_from_cache(self, cached_value: dict, started: float, hit_kind: str) -> AnswerResponse:
        """Rebuild a response from the cache and mark it as a hit."""
        metrics.increment("cache_hits_total")
        metrics.increment("cache_hits_%s_total" % hit_kind)

        response = AnswerResponse(**cached_value)
        total_ms = int((time.perf_counter() - started) * 1000)

        response.usage = UsageReport(
            prompt_tokens=0,
            completion_tokens=0,
            embedding_tokens=0,
            total_tokens=0,
            estimated_cost_usd=0.0,
            latency_ms=total_ms,
            cache_hit=True,
        )
        response.request_id = current_request_id.get()
        metrics.observe("request_latency_ms", total_ms)
        return response

    def retrieve_only(self, question: str, allowed_tags: list[str], top_k: int | None = None):
        """
        Retrieval with no answer generation.

        The evaluation suite needs this so it can measure retrieval quality on its
        own. Retrieval and answering fail for different reasons, and a single
        combined score hides which one is broken.
        """
        self.ensure_index_matches_embedder()
        return self.retrieval.retrieve(
            question=question,
            caller_allowed_tags=allowed_tags,
            top_k=top_k,
        )

    def stream_answer(self, request: AskRequest, allowed_tags: list[str]):
        """
        Yield the answer as it is produced, then the verification result.

        Retrieval must finish first - we cannot stream what we have not found -
        so the shape is: retrieve, then stream, then verify.
        """
        started = time.perf_counter()
        question = request.question.strip()
        metrics.increment("requests_total")
        metrics.increment("stream_requests_total")

        outcome = self.retrieval.retrieve(
            question=question,
            caller_allowed_tags=allowed_tags,
            requested_tags=request.filter_access_tags,
            sources=request.filter_sources,
            top_k=request.top_k,
        )

        collected: list[str] = []
        usage_sink: dict = {}

        for piece in self.answer_builder.stream(question, outcome.passages, usage_sink):
            collected.append(piece)
            yield ("token", piece)

        answer_text = "".join(collected).strip()
        verdict = self.answer_builder.verify_streamed_answer(answer_text, outcome.passages)
        verdict["trace"] = outcome.trace.model_dump()

        total_ms = int((time.perf_counter() - started) * 1000)
        prompt_tokens = usage_sink.get("prompt_tokens", 0)
        completion_tokens = usage_sink.get("completion_tokens", 0)

        outcome.usage.add_model_call(
            "answer", prompt_tokens, completion_tokens, usage_sink.get("cost_usd", 0.0)
        )

        verdict["usage"] = UsageReport(
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            embedding_tokens=outcome.usage.embedding_tokens,
            total_tokens=outcome.usage.total_tokens(),
            estimated_cost_usd=round(outcome.usage.cost_usd, 8),
            latency_ms=total_ms,
            cache_hit=False,
            by_stage=dict(outcome.usage.by_stage),
        ).model_dump()

        metrics.observe("request_latency_ms", total_ms)
        metrics.observe("tokens_per_request", verdict["usage"]["total_tokens"])
        metrics.observe("cost_usd_per_request", verdict["usage"]["estimated_cost_usd"])
        if verdict["answered"]:
            metrics.increment("answers_total")
        else:
            metrics.increment("abstain_total")

        yield ("done", verdict)
