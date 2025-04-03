"""TESTS FOR the corpus search and the planner."""

from research_agent.layer0_shared.text_tools import to_stems
from research_agent.layer4_planner.step1_decompose import (
    decompose_with_rules,
    split_on_conjunctions,
)
from research_agent.layer4_planner.step2_validate_plan import build_plan

# ---------------- retrieval ----------------

def test_a_question_the_corpus_answers_returns_documents(corpus):
    hits = corpus.search("four day work week productivity", 5)
    assert len(hits) > 0
    assert hits[0].relevance == 1.0
    assert hits[0].absolute_score > 0


def test_a_question_the_corpus_cannot_answer_returns_nothing(corpus):
    """
    THE HALLUCINATION THIS PREVENTS.

    Asked about deep sea mining, BM25 returned a study about remote work because
    it contains the word "deep" (in "deep concentration"). Its rescaled relevance
    was 1.00, because rescaling makes the best hit 1.00 however bad it is, and
    the system wrote a ten-source report at 0.99 confidence.
    """
    hits = corpus.search("economic impact of deep sea mining on coastal fisheries", 5)
    assert hits == []


def test_a_relative_score_cannot_say_nothing_matched(corpus):
    """
    Why SearchHit carries the raw score as well as the rescaled one: the best hit
    is always 1.00, so only the absolute score can tell you it was a bad match.
    """
    good = corpus.search("four day work week productivity", 3)
    narrow = corpus.search("shipping container trade", 3)

    assert good[0].relevance == 1.0
    assert narrow[0].relevance == 1.0
    # But their absolute scores are not the same thing at all.
    assert good[0].absolute_score != narrow[0].absolute_score


def test_query_coverage_separates_real_matches_from_coincidences(corpus):
    real = to_stems("four day work week productivity")
    junk = to_stems("deep sea mining coastal fisheries economic impact")

    best_real = 0.0
    best_junk = 0.0
    for document in corpus.documents:
        score, matched = corpus.score_document(document.document_id, real)
        if score > 0:
            best_real = max(best_real, corpus.query_coverage(matched, real))
        score, matched = corpus.score_document(document.document_id, junk)
        if score > 0:
            best_junk = max(best_junk, corpus.query_coverage(matched, junk))

    assert best_real > 0.3
    assert best_junk < 0.2


def test_a_word_absent_from_the_corpus_is_the_most_informative(corpus):
    """
    For scoring, a word nothing contains is irrelevant. For asking "did we find
    what was asked about?", it is the most important word in the query.
    """
    common = corpus.term_information("productivity")
    absent = corpus.term_information("fishery")
    assert absent > common


# ---------------- planning ----------------

def test_a_user_written_split_is_honoured():
    parts = split_on_conjunctions("Solid-state versus lithium-ion batteries")
    assert len(parts) == 2


def test_the_template_produces_several_angles():
    plan = decompose_with_rules("Is the four-day week good?", max_subquestions=4)
    assert len(plan["sub_questions"]) == 4


def test_a_sub_question_that_just_restates_the_question_is_dropped():
    question = "Is the four-day work week good for productivity?"
    raw = {"sub_questions": [question, "what do manufacturing studies measure about output?"]}

    plan, validation = build_plan(question, raw, max_subquestions=5)

    assert len(validation.dropped_same_as_original) == 1
    assert len(plan.sub_questions) == 1


def test_a_good_sub_question_containing_the_question_is_kept():
    """
    THE BUG THIS PREVENTS.

    A good sub-question is supposed to contain the original question plus an
    angle. Measured by containment that scores 0.85, so the validator threw away
    every sub-question and left a "plan" consisting of the original question -
    decomposition ran, reported success, and did nothing.
    """
    question = "Is the four-day work week good for productivity?"
    raw = {"sub_questions": [
        "measured evidence and controlled studies for four-day work week productivity outcomes",
    ]}

    plan, validation = build_plan(question, raw, max_subquestions=5)

    assert len(validation.dropped_same_as_original) == 0
    assert len(plan.sub_questions) == 1


def test_too_many_sub_questions_are_cut():
    raw = {"sub_questions": [
        "first distinct angle about manufacturing output measurement",
        "second distinct angle about employee wellbeing and burnout",
        "third distinct angle about revenue and customer service coverage",
        "fourth distinct angle about government policy recommendations",
    ]}
    plan, validation = build_plan("some question", raw, max_subquestions=2)

    assert len(plan.sub_questions) == 2
    assert len(validation.dropped_over_limit) == 2


def test_an_empty_plan_falls_back_to_the_original_question():
    plan, validation = build_plan("What about X?", {"sub_questions": []}, max_subquestions=5)
    assert len(plan.sub_questions) == 1
    assert plan.sub_questions[0].text == "What about X?"
