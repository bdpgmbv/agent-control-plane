"""
LAYER 8 - EVALUATION: THE MODEL AS JUDGE
========================================
The keyword check in answer_metrics.py is objective but blunt: an answer can be
completely right and still not contain the exact string "30 days".

So we add a second opinion from a model. It is asked two narrow questions, both
answerable from text alone:

    1. Is the answer supported by the passages? (groundedness)
    2. Does the answer actually address the question? (relevance)

WHAT A JUDGE IS GOOD FOR, AND WHAT IT IS NOT
    Good:  catching answers that are fluent but unsupported, at a scale no human
           reviewer can match.
    Not:   being the only metric. A judge shares the biases of the model being
           judged, it costs money, and it is not perfectly repeatable. Use it
           next to the cheap deterministic checks, never instead of them.

With no API key this returns "skipped" rather than inventing a score. A fake
evaluation number is worse than no number, because people act on it.
"""

import json

from rag_assistant.layer0_shared.context_format import format_context_blocks
from rag_assistant.layer0_shared.logging_setup import get_logger

log = get_logger(__name__)

JUDGE_SYSTEM_PROMPT = """You grade a question-answering system. You are given a question, the passages the
system retrieved, and the answer it produced.

Grade two things independently, each from 0 to 10:

  grounded  - is every claim in the answer supported by the passages?
              10 = every claim is supported. 0 = the answer states things the
              passages do not say.

  relevant  - does the answer address the question that was asked?
              10 = directly answers it. 0 = answers something else.

If the answer says it does not know, and the passages genuinely do not contain
the answer, then grounded = 10 and relevant = 10: refusing when there is no
evidence is the correct behaviour, not a failure.

Reply with JSON only:
{"grounded": 8, "relevant": 9, "reason": "one short sentence"}"""


def judge_one_answer(question: str, answer_text: str, passages: list[dict], judge_client) -> dict:
    """
    Ask the judge model to grade one answer.

    `passages` is a list of {"title", "source", "text"}.
    """
    if not judge_client.is_live:
        return {"status": "skipped", "reason": "no API key configured, so no judge was run"}

    context_section = format_context_blocks(passages)
    user_prompt = (
        context_section
        + "\n\nQUESTION: "
        + question
        + "\n\nANSWER GIVEN BY THE SYSTEM:\n"
        + answer_text
    )

    try:
        result = judge_client.complete(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_mode=True,
        )
        parsed = json.loads(result.text)

        grounded = float(parsed.get("grounded", 0)) / 10.0
        relevant = float(parsed.get("relevant", 0)) / 10.0

        return {
            "status": "ok",
            "judge_grounded": round(grounded, 3),
            "judge_relevant": round(relevant, 3),
            "judge_reason": str(parsed.get("reason", "")),
            "judge_cost_usd": result.cost_usd,
        }
    except Exception as error:
        log.warning("judge failed: %s", error)
        return {"status": "failed", "reason": str(error)}


# ---------------------------------------------------------------------------
#  Validating the judge itself
# ---------------------------------------------------------------------------

# A passage and an answer that plainly contradicts it. Any judge worth trusting
# must score this badly.
CALIBRATION_PASSAGES = [
    {
        "title": "Refund Policy",
        "source": "refund_policy.md",
        "text": "Customers may request a refund within 30 days of the delivery date.",
    }
]
CALIBRATION_QUESTION = "How long do I have to ask for a refund?"
CALIBRATION_BAD_ANSWER = (
    "Refunds can be requested at any time within five years, and our regional "
    "manager approves them personally every Friday afternoon. [1]"
)

# Above this, the judge is agreeing with an answer it should have rejected.
CALIBRATION_MAXIMUM_ACCEPTABLE_SCORE = 0.5


def check_the_judge_can_say_no(judge_client) -> dict:
    """
    Feed the judge an answer that is obviously unsupported, and check it notices.

    WHY THIS MATTERS
        A judge that scores everything 10/10 produces a beautiful report and
        tells you nothing. Before believing a perfect score, prove the judge is
        capable of an imperfect one.

        This is the cheapest useful habit in LLM evaluation: one extra call,
        every run, to check the instrument before reading the measurement.
    """
    if not judge_client.is_live:
        return {"status": "skipped", "reason": "no API key configured"}

    verdict = judge_one_answer(
        question=CALIBRATION_QUESTION,
        answer_text=CALIBRATION_BAD_ANSWER,
        passages=CALIBRATION_PASSAGES,
        judge_client=judge_client,
    )

    if verdict.get("status") != "ok":
        return {"status": "failed", "reason": verdict.get("reason", "judge call failed")}

    score = verdict["judge_grounded"]
    passed = score <= CALIBRATION_MAXIMUM_ACCEPTABLE_SCORE

    if passed:
        note = "The judge correctly rejected an unsupported answer."
    else:
        note = (
            "WARNING: the judge approved an answer that contradicts its source. "
            "Treat every judge score in this report as unreliable."
        )

    return {
        "status": "ok",
        "passed": passed,
        "score_given_to_a_deliberately_bad_answer": score,
        "note": note,
    }
