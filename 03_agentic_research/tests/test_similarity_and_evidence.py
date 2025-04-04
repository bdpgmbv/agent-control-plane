"""TESTS FOR duplicate detection, conflict detection, and the evidence pipeline."""

from research_agent.layer0_shared.similarity import (
    compare_claims,
    count_shared_subject_terms,
    numbers_agree,
)
from research_agent.layer0_shared.text_tools import stem_word
from research_agent.layer2_models.schemas import Evidence, SourceType
from research_agent.layer6_evidence.step1_deduplicate import deduplicate, sources_are_independent
from research_agent.layer6_evidence.step2_detect_conflicts import find_conflicts


def make_evidence(identifier, claim, source_name, source_type, document_id="DOC-X", score=0.5):
    return Evidence(
        evidence_id=identifier,
        sub_question_id="sq1",
        claim=claim,
        quote=claim,
        document_id=document_id,
        title="T " + identifier,
        source_name=source_name,
        source_type=source_type,
        published_date="2025-01-01",
        url="u",
        relevance=0.8,
        credibility=0.9,
        recency=0.9,
        score=score,
    )


# ---------------- stemming ----------------

def test_plurals_and_verb_forms_reach_the_same_stem():
    """
    A naive stemmer strips "es" before "s", so "employees" becomes "employe"
    while "employee" stays "employee", and the two never match.
    """
    assert stem_word("employees") == stem_word("employee")
    assert stem_word("improved") == stem_word("improve")
    assert stem_word("increases") == stem_word("increased")
    assert stem_word("studies") == "study"


# ---------------- duplicate versus conflict ----------------

def test_the_same_finding_worded_differently_is_a_duplicate():
    verdict = compare_claims(
        "Employee wellbeing improved substantially in the pilot.",
        "Wellbeing among employees improved substantially during the pilot.",
    )
    assert verdict.is_duplicate() is True
    assert verdict.is_conflict() is False


def test_the_same_wording_with_a_different_number_is_a_conflict_not_a_duplicate():
    """
    THE MISTAKE THIS PREVENTS.

    Measured by wording these are 85% identical, so an ordinary duplicate
    detector merges them and keeps one - silently deleting a disagreement
    between two studies and making the report look more settled than it is.
    """
    verdict = compare_claims(
        "The trial found productivity rose by 12 percent.",
        "The trial found productivity fell by 3 percent.",
    )
    assert verdict.is_duplicate() is False
    assert verdict.is_conflict() is True


def test_close_numbers_do_not_make_a_fake_disagreement():
    assert numbers_agree("productivity rose 12 percent", "productivity rose 12.4 percent") is True
    assert numbers_agree("productivity rose 12 percent", "productivity rose 3 percent") is False


def test_two_different_things_both_falling_is_not_a_disagreement():
    """
    A FALSE CONFLICT THAT DID HAPPEN.

    "Burnout fell by 71 percent" and "turnover fell by 57 percent" have the same
    shape and different numbers. They are two results from one study, not a
    contradiction - and reporting them as one makes a study appear to contradict
    itself.
    """
    verdict = compare_claims(
        "Employee burnout scores fell by 71 percent of a standard deviation.",
        "Staff turnover fell by 57 percent.",
    )
    assert count_shared_subject_terms(
        "Employee burnout scores fell by 71 percent of a standard deviation.",
        "Staff turnover fell by 57 percent.",
    ) == 0
    assert verdict.is_conflict() is False


def test_unrelated_claims_are_neither():
    verdict = compare_claims(
        "The trial found productivity rose by 12 percent.",
        "Battery energy density reached 400 watt hours per kilogram.",
    )
    assert verdict.is_duplicate() is False
    assert verdict.is_conflict() is False


# ---------------- deduplication ----------------

def test_a_duplicate_is_removed_and_the_stronger_source_kept(engine):
    journal = make_evidence(
        "e1", "Productivity rose by 12 percent in the trial.",
        "Journal of Organisational Behaviour", SourceType.PEER_REVIEWED, "DOC-001", score=0.8
    )
    newspaper = make_evidence(
        "e2", "Productivity rose by 12 percent in the trial.",
        "The Daily Business Review", SourceType.NEWS, "DOC-002", score=0.5
    )

    result = deduplicate([journal, newspaper], engine)

    assert len(result.kept) == 1
    assert len(result.removed) == 1
    # The journal survives, so a reader following the citation reaches the study.
    assert result.kept[0].source_name == "Journal of Organisational Behaviour"


def test_a_newspaper_repeating_a_study_is_not_independent_corroboration():
    """
    A journal publishes a finding; a newspaper reports the journal. That is ONE
    study, not two sources agreeing. Counting it as corroboration makes a single
    result look twice as well supported.
    """
    journal = make_evidence("e1", "x", "Journal A", SourceType.PEER_REVIEWED)
    newspaper = make_evidence("e2", "x", "Daily News", SourceType.NEWS)
    other_journal = make_evidence("e3", "x", "Journal B", SourceType.PEER_REVIEWED)

    assert sources_are_independent(journal, newspaper) is False
    assert sources_are_independent(journal, other_journal) is True
    # Two papers in the same journal are very often the same group.
    assert sources_are_independent(journal, make_evidence("e4", "x", "Journal A", SourceType.PEER_REVIEWED)) is False


def test_independent_corroboration_raises_the_score_a_little(engine):
    first = make_evidence(
        "e1", "Productivity rose by 12 percent.", "Journal A", SourceType.PEER_REVIEWED,
        "DOC-1", score=0.60
    )
    second = make_evidence(
        "e2", "Productivity rose by 12 percent.", "Journal B", SourceType.PEER_REVIEWED,
        "DOC-2", score=0.55
    )

    result = deduplicate([first, second], engine)
    assert len(result.kept) == 1
    assert result.kept[0].score > 0.60
    # Modest: two studies agreeing is better than one, not twice as good.
    assert result.kept[0].score <= 0.76


# ---------------- conflicts ----------------

def test_a_document_cannot_conflict_with_itself(engine):
    """
    One paper stating two different facts is one paper stating two different
    facts. The conflict test flagged a study for disagreeing with itself.
    """
    first = make_evidence(
        "e1", "The trial covered 2900 employees across 61 organisations.",
        "Journal A", SourceType.PEER_REVIEWED, document_id="DOC-001"
    )
    second = make_evidence(
        "e2", "Of the 61 organisations, 56 chose to continue afterwards.",
        "Journal A", SourceType.PEER_REVIEWED, document_id="DOC-001"
    )

    assert find_conflicts([first, second], engine) == []


def test_a_real_disagreement_between_two_documents_is_found(engine):
    first = make_evidence(
        "e1", "Productivity rose by 12 percent relative to baseline.",
        "Journal A", SourceType.PEER_REVIEWED, document_id="DOC-001"
    )
    second = make_evidence(
        "e2", "Productivity fell by 3 percent relative to baseline.",
        "Journal B", SourceType.PEER_REVIEWED, document_id="DOC-003"
    )

    conflicts = find_conflicts([first, second], engine)
    assert len(conflicts) == 1
    assert conflicts[0].source_a != conflicts[0].source_b
