"""TESTS FOR LAYER 6 - citations, refusal and groundedness."""

from rag_assistant.layer0_shared.llm_client import ABSTAIN_SENTENCE
from rag_assistant.layer2_models.schemas import AskRequest, Chunk, ScoredChunk
from rag_assistant.layer6_generation.answer_builder import (
    AnswerBuilder,
    build_citations,
    compute_confidence,
    looks_like_abstention,
)
from rag_assistant.layer6_generation.groundedness import (
    find_citation_markers,
    measure_groundedness,
    sentence_support,
)


def make_passage(chunk_id, text, rerank_score=0.8):
    chunk = Chunk(
        chunk_id=chunk_id,
        document_id="d1",
        document_title="Refund Policy",
        source="refunds.md",
        access_tag="public",
        chunk_index=0,
        text=text,
    )
    return ScoredChunk(chunk=chunk, score=0.5, rerank_score=rerank_score)


# ---------------- citation parsing ----------------

def test_citation_markers_are_found_in_order_without_repeats():
    assert find_citation_markers("First [2]. Second [1]. Again [2].") == [2, 1]


def test_a_citation_pointing_at_a_real_passage_is_kept():
    passages = [make_passage("c1", "Refunds within 30 days.")]
    citations, invented = build_citations("Refunds take 30 days [1].", passages)

    assert len(citations) == 1
    assert invented == 0
    assert citations[0].chunk_id == "c1"


def test_an_invented_citation_is_dropped_and_counted():
    """
    The classic hallucinated footnote: the model cites passage 7 when only one
    passage was provided. It must never reach the user as a real source.
    """
    passages = [make_passage("c1", "Refunds within 30 days.")]
    citations, invented = build_citations("Refunds take 30 days [7].", passages)

    assert citations == []
    assert invented == 1


# ---------------- refusal detection ----------------

def test_the_standard_refusal_sentence_is_detected():
    assert looks_like_abstention(ABSTAIN_SENTENCE) is True


def test_other_phrasings_of_refusal_are_detected():
    assert looks_like_abstention("I do not know from these documents.") is True
    assert looks_like_abstention("The context does not mention parental leave.") is True
    assert looks_like_abstention("Refunds take 30 days.") is False


# ---------------- groundedness ----------------

def test_a_sentence_copied_from_the_passage_is_fully_supported():
    passage = "Customers may request a refund within 30 days of delivery."
    assert sentence_support("Customers may request a refund within 30 days.", passage) == 1.0


def test_an_invented_sentence_is_not_supported():
    passage = "Customers may request a refund within 30 days of delivery."
    assert sentence_support("Refunds are approved by the regional manager on Fridays.", passage) < 0.55


def test_citation_markers_do_not_affect_groundedness():
    passage = "Express delivery costs 12.99 dollars."
    with_marker = sentence_support("Express delivery costs 12.99 dollars. [1]", passage)
    assert with_marker == 1.0


def test_groundedness_reports_the_unsupported_sentences():
    passages = [make_passage("c1", "Express delivery costs 12.99 dollars.")]
    score, unsupported = measure_groundedness(
        "Express delivery costs 12.99 dollars. [1] Overnight shipping is always free in Iceland.",
        passages,
    )
    assert score == 0.5
    assert len(unsupported) == 1
    assert "Iceland" in unsupported[0]


# ---------------- confidence ----------------

def test_confidence_is_zero_with_no_passages():
    assert compute_confidence([], 0, 0.0) == 0.0


def test_confidence_rises_with_better_evidence():
    passages = [make_passage("c1", "text", rerank_score=0.9)]
    weak = compute_confidence(passages, citation_count=0, groundedness=0.0)
    strong = compute_confidence(passages, citation_count=2, groundedness=1.0)
    assert strong > weak
    assert strong <= 1.0


# ---------------- the builder as a whole ----------------

def test_no_passages_means_refuse_without_calling_the_model(chat_client):
    """
    Guarantee 1. With nothing retrieved we must refuse immediately - calling the
    model with empty context invites it to answer from memory, and costs money.
    """
    builder = AnswerBuilder(chat_client=chat_client)
    result = builder.build("anything at all", [])

    assert result.answered is False
    assert result.answer_text == ABSTAIN_SENTENCE
    assert result.usage.prompt_tokens == 0        # proof no call was made
    assert "threshold" in result.abstain_reason


def test_an_answer_with_no_citations_is_refused():
    """
    Guarantee 3. The prompt tells the model to cite. This test proves we do not
    merely hope it obeys.
    """

    class ModelThatIgnoresTheCitationRule:
        model = "test-double"
        is_live = False

        def complete(self, system_prompt, user_prompt, json_mode=False):
            from rag_assistant.layer0_shared.llm_client import LlmResult

            return LlmResult(text="Refunds take about a month.", model=self.model)

    builder = AnswerBuilder(chat_client=ModelThatIgnoresTheCitationRule())
    result = builder.build("refund window", [make_passage("c1", "Refunds within 30 days.")])

    assert result.answered is False
    assert "without citing" in result.abstain_reason


def test_a_good_answer_is_scored_and_cited(service):
    response = service.ask(
        AskRequest(question="How long do I have to ask for a refund?", use_cache=False),
        allowed_tags=["public", "internal"],
    )

    assert response.answered is True
    assert len(response.citations) > 0
    assert response.citations[0].source == "refund_policy.md"
    assert response.groundedness is not None
    assert response.confidence > 0


def test_an_unanswerable_question_is_refused(service):
    response = service.ask(
        AskRequest(question="Who won the football world cup in 1998?", use_cache=False),
        allowed_tags=["public", "internal", "secret"],
    )
    assert response.answered is False
    assert response.citations == []
