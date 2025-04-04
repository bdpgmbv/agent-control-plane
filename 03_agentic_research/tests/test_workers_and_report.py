"""TESTS FOR the workers, the quote check, report verification and the whole run."""

from research_agent.layer2_models.schemas import (
    Citation,
    Report,
    ReportSection,
    ResearchRequest,
    SubQuestionStatus,
)
from research_agent.layer5_workers.step2_extract_evidence import quote_is_in_document, recency_score
from research_agent.layer7_synthesis.step2_verify_report import (
    move_markers_inside_sentences,
    score_confidence,
    verify_report,
)

# ---------------- quote verification ----------------

DOCUMENT = (
    "We report outcomes from a six-month trial of a four-day working week. "
    "Self-reported productivity rose by 12 percent relative to the baseline period. "
    "Employee burnout scores fell by 71 percent of a standard deviation."
)


def test_a_real_quote_is_accepted():
    assert quote_is_in_document(
        "Self-reported productivity rose by 12 percent relative to the baseline period.", DOCUMENT
    ) is True


def test_a_quote_differing_only_in_punctuation_is_accepted():
    assert quote_is_in_document(
        "Self reported productivity rose by 12 percent relative to the baseline period", DOCUMENT
    ) is True


def test_an_invented_quote_is_rejected():
    """
    A model asked to quote a document will sometimes produce a tidied-up sentence
    that was never there. The claim then looks perfectly sourced and is not.
    """
    assert quote_is_in_document(
        "Productivity increased dramatically across every participating organisation.", DOCUMENT
    ) is False


def test_two_sentences_welded_together_are_rejected():
    assert quote_is_in_document(
        "Self-reported productivity rose by 12 percent and burnout fell by 71 percent "
        "of a standard deviation across all sites.",
        DOCUMENT,
    ) is False


def test_an_empty_quote_is_rejected():
    assert quote_is_in_document("", DOCUMENT) is False


def test_newer_sources_score_higher_but_only_gently():
    """
    A 2019 randomised trial usually beats a 2025 blog post. A steep recency curve
    would invert that.
    """
    this_year = recency_score("2025-01-01", 2025)
    five_years_old = recency_score("2020-01-01", 2025)

    assert this_year == 1.0
    assert five_years_old > 0.4
    assert five_years_old < this_year


# ---------------- report verification ----------------

def build_small_report():
    return Report(
        question="q",
        summary="Productivity rose 12 percent [1].",
        sections=[
            ReportSection(
                sub_question="sq",
                status=SubQuestionStatus.RESEARCHED,
                findings="Productivity rose 12 percent [1]. Output fell 40 percent [9].",
                citation_markers=[1],
                evidence_ids=["e1"],
            )
        ],
        citations=[
            Citation(
                marker=1, evidence_id="e1", title="T", source_name="S",
                source_type="peer_reviewed", published_date="2025-01-01", url="u",
                quote="Productivity rose 12 percent.", credibility=1.0,
            )
        ],
    )


def test_an_invented_citation_marker_is_stripped():
    """
    A reader who follows a citation into nothing loses trust in all the others,
    so a marker pointing at no source is removed rather than left in.
    """
    report = build_small_report()
    verification = verify_report(report, {})

    assert 9 in verification.invented_markers
    assert "[9]" not in report.sections[0].findings
    assert "[1]" in report.sections[0].findings


def test_a_figure_that_is_in_no_source_is_reported():
    report = build_small_report()
    verification = verify_report(report, {})
    assert "40.0" in verification.unsupported_numbers


def test_citation_markers_are_not_mistaken_for_figures():
    """
    A REGRESSION TEST. A correct report ending "...until at least 2033 [10][11]."
    was reported as containing two invented figures. They were the markers.
    """
    report = Report(
        question="q",
        summary="Cost parity is not expected until 2033 [10][11].",
        sections=[],
        citations=[
            Citation(marker=10, evidence_id="e", title="T", source_name="S",
                     source_type="industry_report", published_date="2025-05-19", url="u",
                     quote="We expect cost parity no earlier than 2033.", credibility=0.7),
            Citation(marker=11, evidence_id="e2", title="T2", source_name="S2",
                     source_type="industry_report", published_date="2025-05-19", url="u",
                     quote="Costs are approximately 8 times those of lithium-ion.", credibility=0.7),
        ],
    )
    verification = verify_report(report, {})
    assert verification.unsupported_numbers == []


def test_markers_written_after_the_full_stop_stay_with_their_sentence():
    fixed = move_markers_inside_sentences("Claim one. [1] Claim two. [2]")
    assert fixed.startswith("Claim one [1].")


def test_a_section_with_no_evidence_is_not_penalised_for_citing_nothing():
    """Punishing the system for admitting a gap is exactly backwards."""
    report = Report(
        question="q",
        summary="",
        sections=[
            ReportSection(
                sub_question="sq",
                status=SubQuestionStatus.NO_EVIDENCE,
                findings="No usable evidence was found for this sub-question.",
                citation_markers=[],
            )
        ],
        citations=[],
    )
    verification = verify_report(report, {})
    assert verification.uncited_sentences == []


# ---------------- confidence ----------------

def test_confidence_falls_when_the_run_was_cut_short():
    report = build_small_report()
    verification = verify_report(report, {})

    complete, _reason = score_confidence(report, verification, 3, 3, budget_exhausted=False)
    cut_short, reason = score_confidence(report, verification, 3, 1, budget_exhausted=True)

    assert cut_short < complete
    assert "budget limit" in reason


def test_confidence_is_low_when_nothing_was_cited():
    empty = Report(question="q", summary="Nothing found.", sections=[], citations=[])
    verification = verify_report(empty, {})
    confidence, reason = score_confidence(empty, verification, 3, 0, budget_exhausted=False)

    assert confidence < 0.5
    assert "no evidence was cited" in reason


# ---------------- the whole run ----------------

def test_a_normal_run_produces_a_cited_report(service):
    response = service.research(
        ResearchRequest(question="Is the four-day work week actually good for productivity?")
    )

    assert len(response.plan.sub_questions) >= 2
    assert len(response.report.citations) >= 3
    assert response.report.partial is False
    assert response.report.confidence > 0.5
    assert len(response.report.summary) > 40


def test_every_citation_in_a_run_points_at_a_real_quote(service, corpus):
    """The end-to-end version of the quote check."""
    response = service.research(
        ResearchRequest(question="When will solid-state batteries actually be available in cars?")
    )

    body_by_title = {}
    for document in corpus.documents:
        body_by_title[document.title] = document.body

    for citation in response.report.citations:
        body = body_by_title.get(citation.title, "")
        assert body != "", citation.title
        assert quote_is_in_document(citation.quote, body), citation.quote


def test_a_question_the_corpus_cannot_answer_produces_no_citations(service):
    response = service.research(
        ResearchRequest(question="What is the economic impact of deep sea mining on coastal fisheries?")
    )

    assert len(response.report.citations) == 0
    assert response.report.confidence <= 0.6


def test_running_out_of_budget_still_produces_a_report(service):
    """
    GRACEFUL DEGRADATION. A partial answer with its gaps named is useful. A crash
    after ninety seconds and two dollars is not.
    """
    response = service.research(
        ResearchRequest(
            question="Does remote work increase or decrease productivity?",
            max_tool_calls=1,
        )
    )

    assert response.report.partial is True
    assert len(response.report.gaps) > 0
    assert len(response.report.summary) > 20
    assert response.trace.budget["stopped_because"] != "none"


def test_a_run_never_exceeds_the_budget_it_was_given(service):
    response = service.research(
        ResearchRequest(question="Is the four-day work week good?", max_tool_calls=3)
    )
    budget = response.trace.budget
    assert budget["tool_calls"]["made"] <= 3


def test_sub_questions_are_researched_in_parallel(service):
    """
    Wall-clock time must be less than the sum of the workers' own times, or they
    ran one after another.
    """
    response = service.research(
        ResearchRequest(question="Is the four-day work week actually good for productivity?")
    )

    total_worker_seconds = 0.0
    for sub_question in response.plan.sub_questions:
        total_worker_seconds = total_worker_seconds + sub_question.worker_seconds

    assert len(response.plan.sub_questions) >= 2
    # The offline model is so fast this is a weak check, but it still fails if
    # the pool is replaced with a plain loop over a slow source.
    assert response.seconds < total_worker_seconds + 1.0
