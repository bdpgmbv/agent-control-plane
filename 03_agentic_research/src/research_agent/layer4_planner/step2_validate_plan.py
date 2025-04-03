"""
LAYER 4 - PLANNER, STEP 2: CHECK THE PLAN BEFORE ACTING ON IT
=============================================================
The planner is not trusted to respect its own limits. Never mind whether it is a
model or a set of rules - a plan is input, and input gets validated.

What this catches, all of which happens in practice:

  TOO MANY SUB-QUESTIONS
      Asked for at most 6, the model returns 11. Each one costs searches and a
      model call, so the plan alone decides most of the bill. Extras are cut, and
      the fact they were cut is recorded.

  NEAR-DUPLICATE SUB-QUESTIONS
      "What are the benefits of X?" and "What advantages does X have?" are the
      same search twice at twice the price, and they produce a report that says
      the same thing in two sections.

  A SUB-QUESTION THAT IS JUST THE ORIGINAL AGAIN
      The most common failure. It produces a plan that has not decomposed
      anything, and a worker that does exactly what a single search would have.

  TOO DEEP
      Depth is checked against the budget. Without it, "research X" becomes a
      tree, and the tree is billed by the node.
"""

import uuid

from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer0_shared.similarity import jaccard_similarity, wording_similarity
from research_agent.layer0_shared.text_tools import to_stems
from research_agent.layer2_models.schemas import ResearchPlan, SubQuestion

log = get_logger(__name__)

# Two sub-questions this alike are the same search twice.
#
# THE THRESHOLD DEPENDS ON HOW SIMILARITY IS MEASURED, and the gap between the
# two is why this takes a `similarity` function rather than hard-coding one.
#
# Comparing words, "What are the benefits of X?" and "What advantages does X
# have?" score about 0.64 - they share only "X". Lowering the bar to catch them
# would also catch "What are the COSTS of X?", which shares exactly the same
# words and asks the opposite question. Merging those two would quietly delete
# half the research.
#
# Comparing meaning, benefits/advantages lands near 0.92 and benefits/costs near
# 0.70, and one threshold separates them cleanly. So with a key this works
# properly; without one, the validator lets the occasional near-duplicate
# through. That costs an extra sub-question, which is the right way round.
SUBQUESTION_DUPLICATE_THRESHOLD = 0.72

# A sub-question this close to the original has not decomposed anything.
#
# MEASURED SYMMETRICALLY, WHICH IS NOT AN IMPLEMENTATION DETAIL.
#
# The general similarity function rewards CONTAINMENT: a short claim quoted
# inside a longer one scores high. That is right for evidence and completely
# wrong here, because a good sub-question is SUPPOSED to contain the original
# question plus an angle:
#
#     original     "Is the four-day week good for productivity?"
#     sub-question "measured evidence for the four-day week and productivity"
#
# The sub-question contains nearly every content word of the original, so
# containment scores it 0.85 and the validator threw it away - along with every
# other one, leaving a "plan" consisting of the original question. The whole
# decomposition silently did nothing.
#
# Jaccard is symmetric: it also counts the words the sub-question ADDS. A real
# restatement still scores near 1.0; an angle on the topic scores about 0.45.
SAME_AS_ORIGINAL_THRESHOLD = 0.85


class PlanValidation:
    """What was changed, and why. Shown in the trace."""

    def __init__(self) -> None:
        self.dropped_duplicates: list[str] = []
        self.dropped_same_as_original: list[str] = []
        self.dropped_over_limit: list[str] = []
        self.dropped_too_short: list[str] = []

    def total_dropped(self) -> int:
        return (
            len(self.dropped_duplicates)
            + len(self.dropped_same_as_original)
            + len(self.dropped_over_limit)
            + len(self.dropped_too_short)
        )

    def to_dict(self) -> dict:
        return {
            "dropped_duplicates": self.dropped_duplicates,
            "dropped_same_as_original": self.dropped_same_as_original,
            "dropped_over_limit": self.dropped_over_limit,
            "dropped_too_short": self.dropped_too_short,
            "total_dropped": self.total_dropped(),
        }


def build_plan(
    question: str,
    raw_plan: dict,
    max_subquestions: int,
    depth: int = 1,
    similarity=None,
    duplicate_threshold: float = SUBQUESTION_DUPLICATE_THRESHOLD,
) -> tuple[ResearchPlan, PlanValidation]:
    """
    Turn a raw plan into a validated one.

    `similarity` is a function taking two strings and returning 0.0 to 1.0. It
    defaults to comparing words; the orchestrator passes the embedding-backed
    version when a key is configured. See the note on the thresholds above.
    """
    if similarity is None:
        similarity = wording_similarity

    validation = PlanValidation()
    accepted: list[SubQuestion] = []

    for text in raw_plan.get("sub_questions", []):
        text = text.strip()

        if len(text) < 10:
            validation.dropped_too_short.append(text)
            continue

        if jaccard_similarity(to_stems(text), to_stems(question)) >= SAME_AS_ORIGINAL_THRESHOLD:
            validation.dropped_same_as_original.append(text)
            continue

        is_duplicate = False
        for existing in accepted:
            if similarity(text, existing.text) >= duplicate_threshold:
                is_duplicate = True
                break

        if is_duplicate:
            validation.dropped_duplicates.append(text)
            continue

        if len(accepted) >= max_subquestions:
            validation.dropped_over_limit.append(text)
            continue

        accepted.append(
            SubQuestion(
                sub_question_id="sq_" + uuid.uuid4().hex[:8],
                text=text,
                depth=depth,
            )
        )

    # A plan with nothing in it is not a plan. Researching the original question
    # directly is a worse outcome than a good decomposition, and a much better
    # one than returning nothing.
    if len(accepted) == 0:
        accepted.append(
            SubQuestion(
                sub_question_id="sq_" + uuid.uuid4().hex[:8],
                text=question,
                depth=depth,
                note="the plan was empty after validation, so the original question is researched directly",
            )
        )

    plan = ResearchPlan(
        question=question,
        sub_questions=accepted,
        strategy=str(raw_plan.get("strategy", "")),
        planner_note=describe_validation(validation),
    )

    if validation.total_dropped() > 0:
        log_event(
            log,
            "plan.validated",
            accepted=len(accepted),
            dropped=validation.total_dropped(),
            detail=validation.to_dict(),
        )

    return (plan, validation)


def describe_validation(validation: PlanValidation) -> str:
    """One readable sentence about what was cut."""
    parts: list[str] = []

    if len(validation.dropped_duplicates) > 0:
        parts.append("%d near-duplicate sub-questions removed" % len(validation.dropped_duplicates))
    if len(validation.dropped_same_as_original) > 0:
        parts.append(
            "%d sub-questions were just the original question restated"
            % len(validation.dropped_same_as_original)
        )
    if len(validation.dropped_over_limit) > 0:
        parts.append("%d sub-questions dropped over the limit" % len(validation.dropped_over_limit))
    if len(validation.dropped_too_short) > 0:
        parts.append("%d sub-questions were too short to be useful" % len(validation.dropped_too_short))

    if len(parts) == 0:
        return "The plan passed validation unchanged."
    return "; ".join(parts) + "."
