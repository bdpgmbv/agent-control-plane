"""
LAYER 8 - EVALUATION: ANSWER METRICS
====================================
Once retrieval has been scored on its own, we score the answer.

  correctness      Does the answer contain the facts it must contain? Checked
                   against a short list of required strings per question. Crude
                   but objective, repeatable and free - which beats a clever
                   metric you cannot afford to run on every change.

  abstained_correctly
                   The one that stops the system embarrassing you. Did it say
                   "I don't know" exactly when it should have, and not otherwise?
                   Split into two failure kinds because they are different bugs:
                       answered when it should not  -> a hallucination, or a leak
                       abstained when it should not -> an unhelpful assistant

  citation_precision
                   Of the sources the answer cited, what share were genuinely
                   relevant? A confident answer citing the wrong document is
                   harder for a user to catch than no answer at all.

  groundedness     Carried through from layer 6: is every sentence supported by
                   the retrieved text?

  leaked_forbidden_source
                   Did an answer quote a document the caller may not read? This
                   must be zero. Not "low" - zero.
"""

from rag_assistant.layer2_models.schemas import AnswerResponse


def contains_all_required(answer_text: str, required_strings: list[str]) -> tuple[bool, list[str]]:
    """
    Case-insensitive check that every required fact is present.

    A required entry may list alternatives separated by "|", and any one of them
    counts. This matters more than it sounds: "refunds go back to the original
    payment method" and "we cannot refund to a different card" state the same
    fact. Insisting on one exact wording would mark a correct answer wrong, and a
    metric that punishes correct answers is worse than no metric.
    """
    lowered = answer_text.lower()

    missing: list[str] = []
    for required in required_strings:
        alternatives = required.split("|")

        found = False
        for alternative in alternatives:
            if alternative.strip().lower() in lowered:
                found = True
                break

        if not found:
            missing.append(required)

    return (len(missing) == 0, missing)


def citation_precision(response: AnswerResponse, relevant_sources: list[str]) -> float:
    """Share of the cited sources that are in the expected set."""
    if len(response.citations) == 0:
        return 0.0
    if len(relevant_sources) == 0:
        return 0.0

    hits = 0
    for citation in response.citations:
        if citation.source in relevant_sources:
            hits = hits + 1

    return round(hits / len(response.citations), 4)


def find_forbidden_citations(response: AnswerResponse, forbidden_sources: list[str]) -> list[str]:
    """Any cited source the caller was not allowed to read. Must always be empty."""
    leaked: list[str] = []
    for citation in response.citations:
        if citation.source in forbidden_sources:
            if citation.source not in leaked:
                leaked.append(citation.source)
    return leaked


def evaluate_one_answer(
    response: AnswerResponse,
    must_contain: list[str],
    should_abstain: bool,
    relevant_sources: list[str],
    forbidden_sources: list[str],
) -> dict:
    """Score one answer against one expected result."""
    if should_abstain:
        # For an abstention case, the only correct behaviour is to abstain.
        abstained_correctly = not response.answered
        correct = abstained_correctly
        missing: list[str] = []
    else:
        abstained_correctly = response.answered
        correct, missing = contains_all_required(response.answer, must_contain)
        if not response.answered:
            correct = False

    groundedness = response.groundedness
    if groundedness is None:
        groundedness = 0.0

    return {
        "correct": correct,
        "missing_facts": missing,
        "answered": response.answered,
        "should_abstain": should_abstain,
        "abstained_correctly": abstained_correctly,
        "answered_when_it_should_not": should_abstain and response.answered,
        "abstained_when_it_should_not": (not should_abstain) and (not response.answered),
        "citation_count": len(response.citations),
        "citation_precision": citation_precision(response, relevant_sources),
        "groundedness": groundedness,
        "leaked_forbidden_sources": find_forbidden_citations(response, forbidden_sources),
        "confidence": response.confidence,
    }
