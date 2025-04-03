"""
LAYER 4 - PLANNER, STEP 1: BREAK THE QUESTION UP
================================================
"Is the four-day week a good idea?" is not a search query. It is three or four
questions wearing a coat:

    what do the measured results actually show?
    what are the costs and the cases where it does not work?
    who disagrees, and why?

Decomposition is what turns a question you cannot search for into questions you
can. It is also the stage where a research agent most easily runs away from you,
because every sub-question can be decomposed again, and the tree grows faster
than the budget.

TWO IMPLEMENTATIONS

  model   asks for sub-questions as JSON. Better on unusual questions, and it
          picks up the specific angle a question is really about.

  rules   a fixed research template - evidence, counter-evidence, costs and
          practical constraints - plus splitting on any "and"/"versus" the user
          wrote themselves. It is not clever, but it is a genuinely reasonable
          shape for a research plan, and it costs nothing.

Whichever runs, step 2 then validates the result. The planner is never trusted
to respect its own limits.
"""

import json

from research_agent.layer0_shared.llm_client import TASK_PLAN
from research_agent.layer0_shared.logging_setup import get_logger
from research_agent.layer0_shared.text_tools import to_keywords

log = get_logger(__name__)

PLANNER_SYSTEM_PROMPT = """You break a research question into independent sub-questions.

Rules:
- Produce between 2 and {max_subquestions} sub-questions.
- Each one must be answerable on its own, by searching. No sub-question may
  depend on the answer to another.
- Cover different angles: what the evidence shows, what the counter-evidence
  shows, costs and practical limits. Do not produce four rewordings of the
  original question.
- Keep the specific nouns from the question. "What does the research say?" is
  useless; "What do controlled studies measure for manufacturing output?" is not.
- Do not answer anything. Only decompose.

Reply with JSON only:
{{"strategy": "one sentence on how you split it",
  "sub_questions": ["...", "..."]}}"""

# The fallback plan shape. Applied to any question, in this order.
RESEARCH_ANGLES = [
    ("measured results and evidence for {topic}", "what the evidence shows"),
    ("criticism limitations and negative results for {topic}", "what argues against it"),
    ("costs practical constraints and implementation problems of {topic}", "what it costs in practice"),
    ("who disagrees about {topic} and why", "where sources conflict"),
]

SPLIT_WORDS = [" versus ", " vs ", " compared with ", " compared to ", " and also "]


def topic_of(question: str) -> str:
    """The subject of the question, with the question framing stripped off."""
    keywords = to_keywords(question)
    if len(keywords) == 0:
        return question.strip()

    # Drop the leading interrogative words, which are about asking rather than
    # about the subject.
    framing = {"what", "how", "why", "does", "do", "is", "are", "should", "can",
               "which", "who", "when", "tell", "explain", "research", "evidence"}

    kept: list[str] = []
    for word in keywords:
        if word in framing:
            continue
        kept.append(word)

    if len(kept) == 0:
        return question.strip()
    return " ".join(kept)


def split_on_conjunctions(question: str) -> list[str]:
    """
    Honour a split the user wrote themselves.

    "Solid-state versus lithium-ion batteries" is already two research questions,
    and the person asking has told you where the seam is.
    """
    lowered = question.lower()

    for separator in SPLIT_WORDS:
        if separator in lowered:
            position = lowered.index(separator)
            first = question[:position].strip()
            second = question[position + len(separator) :].strip()
            if len(first) > 3 and len(second) > 3:
                return [first, second]

    return []


def decompose_with_rules(question: str, max_subquestions: int) -> dict:
    """The no-model planner: a fixed research template."""
    sub_questions: list[str] = []

    for part in split_on_conjunctions(question):
        sub_questions.append("evidence and measured results for " + part)

    topic = topic_of(question)
    for pattern, _why in RESEARCH_ANGLES:
        if len(sub_questions) >= max_subquestions:
            break
        sub_questions.append(pattern.format(topic=topic))

    return {
        "strategy": (
            "Standard research template: evidence, counter-evidence, costs, and "
            "points of disagreement."
        ),
        "sub_questions": sub_questions[:max_subquestions],
    }


def decompose_with_model(question: str, max_subquestions: int, model) -> dict:
    """Ask the model. Falls back to the template on any problem."""
    result = model.complete(
        system_prompt=PLANNER_SYSTEM_PROMPT.format(max_subquestions=max_subquestions),
        user_prompt=question,
        task=TASK_PLAN,
        json_mode=True,
        max_tokens=600,
        describe="planning the research",
    )

    if result is None:
        # The budget refused before any work was done at all.
        plan = decompose_with_rules(question, max_subquestions)
        plan["strategy"] = "Budget was exhausted before planning; used the standard template."
        return plan

    try:
        parsed = json.loads(result.text)
        raw_questions = parsed.get("sub_questions", [])

        cleaned: list[str] = []
        for item in raw_questions:
            if isinstance(item, str) and len(item.strip()) > 8:
                cleaned.append(item.strip())

        if len(cleaned) == 0:
            raise ValueError("the model returned no usable sub-questions")

        return {
            "strategy": str(parsed.get("strategy", "")),
            "sub_questions": cleaned,
        }
    except Exception as error:
        log.warning("planning by model failed, using the template: %s", error)
        return decompose_with_rules(question, max_subquestions)


def decompose(question: str, max_subquestions: int, model) -> dict:
    if model.is_live:
        return decompose_with_model(question, max_subquestions, model)
    return decompose_with_rules(question, max_subquestions)
