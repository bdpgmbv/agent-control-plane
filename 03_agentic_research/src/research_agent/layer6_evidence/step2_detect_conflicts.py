"""
LAYER 6 - EVIDENCE, STEP 2: WHERE THE SOURCES DISAGREE
======================================================
The most valuable output of a research system, and the one most systems throw
away.

Two studies of the same question report 12 percent and minus 3 percent. A
research agent that quietly picks one has not answered the question - it has
hidden the most important thing it found, and the reader has no way to know.

So conflicts are first-class. They are detected, they are explained, and they
appear in the report as their own section.

HOW A CONFLICT IS RECOGNISED
    Two findings that are ABOUT the same thing but whose NUMBERS disagree. See
    similarity.py: that pairing is exactly why the duplicate check looks at
    wording and figures separately.

CONFLICTS ARE NOT RESOLVED HERE
    The report says which side carries more weight, by source credibility, and
    then shows both. Deciding that a peer-reviewed study beats a company blog is
    reasonable; deleting the blog and pretending the disagreement did not happen
    is not. The reader gets the disagreement and the weighting, and can disagree
    with the weighting.
"""

import uuid

from research_agent.layer0_shared.llm_client import TASK_EXPLAIN_CONFLICT
from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer0_shared.text_tools import shorten
from research_agent.layer2_models.schemas import Conflict, Evidence

log = get_logger(__name__)

CONFLICT_SYSTEM_PROMPT = """Two sources report different figures for the same question.

In one or two sentences, say what they disagree about and the most likely reason:
different measurement (self-reported versus measured), different populations,
different time periods, or different definitions.

Do not decide who is right. Do not add facts that are not in the two findings.
Reply with plain text, no preamble."""

# At most this many conflicts are explained by the model. Explaining is a call
# each, and a run with a lot of contradictory evidence could otherwise spend more
# on describing disagreements than on finding them.
MAXIMUM_EXPLAINED_CONFLICTS = 4


def find_conflicts(evidence_items: list[Evidence], engine) -> list[Conflict]:
    """Every pair of findings that talk about the same thing but disagree."""
    conflicts: list[Conflict] = []

    claims: list[str] = []
    for item in evidence_items:
        claims.append(item.claim)
    engine.prepare(claims)

    first_position = 0
    while first_position < len(evidence_items):
        second_position = first_position + 1

        while second_position < len(evidence_items):
            first = evidence_items[first_position]
            second = evidence_items[second_position]

            # TWO FINDINGS FROM THE SAME DOCUMENT ARE NOT A DISAGREEMENT.
            #
            # A paper says "2,900 employees took part" and "56 of 61 organisations
            # continued". Similar wording, different numbers - and the conflict
            # test happily flagged a study for disagreeing with itself. A source
            # stating two different facts is a source stating two different facts.
            if first.document_id == second.document_id:
                second_position = second_position + 1
                continue

            verdict = engine.compare(first.claim, second.claim)
            if verdict.is_conflict():
                conflicts.append(
                    Conflict(
                        conflict_id="cf_" + uuid.uuid4().hex[:8],
                        topic=shorten(first.claim, 90),
                        evidence_id_a=first.evidence_id,
                        evidence_id_b=second.evidence_id,
                        claim_a=first.claim,
                        claim_b=second.claim,
                        source_a=first.source_name,
                        source_b=second.source_name,
                    )
                )

            second_position = second_position + 1
        first_position = first_position + 1

    metrics.increment("conflicts_found_total", len(conflicts))

    if len(conflicts) > 0:
        log_event(log, "conflicts.found", count=len(conflicts))

    return conflicts


def explain_conflicts(conflicts: list[Conflict], model) -> list[Conflict]:
    """Add a sentence to each conflict saying what is probably going on."""
    explained = 0

    for conflict in conflicts:
        if explained >= MAXIMUM_EXPLAINED_CONFLICTS:
            conflict.explanation = default_explanation(conflict)
            continue

        if not model.is_live:
            conflict.explanation = default_explanation(conflict)
            continue

        user_prompt = (
            "FINDING A (" + conflict.source_a + "): " + conflict.claim_a + "\n\n"
            "FINDING B (" + conflict.source_b + "): " + conflict.claim_b
        )

        result = model.complete(
            system_prompt=CONFLICT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            task=TASK_EXPLAIN_CONFLICT,
            max_tokens=200,
            describe="explaining a conflict between sources",
        )

        if result is None or result.text.strip() == "":
            conflict.explanation = default_explanation(conflict)
        else:
            conflict.explanation = result.text.strip()
            explained = explained + 1

    return conflicts


def default_explanation(conflict: Conflict) -> str:
    """Used with no model, or once the explanation budget is spent."""
    return (
        "%s and %s report different figures for this. The difference may come "
        "from how each was measured, who was studied, or over what period."
        % (conflict.source_a, conflict.source_b)
    )
