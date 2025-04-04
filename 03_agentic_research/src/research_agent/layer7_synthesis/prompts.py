"""
LAYER 7 - SYNTHESIS: PROMPTS
============================
The rules here all exist to stop the same failure: a report that reads better
than the evidence justifies.

A model handed twelve findings will write a smooth, confident paragraph. Smooth
and confident is exactly wrong when two of the findings contradict each other and
three came from a company blog. So the prompts insist on the numbers, insist on
the citation markers, and insist that disagreement is reported rather than
resolved.

As always: the prompt asks, and layer 7's verification step checks.
"""

from research_agent.layer2_models.schemas import Evidence

SECTION_SYSTEM_PROMPT = """You answer one research sub-question using only the findings given to you.

Rules:
- Use ONLY the numbered findings. No outside knowledge, not even obvious facts.
- Put the number of the finding in square brackets after each sentence: [2].
  Every sentence needs one. A sentence with no marker is not allowed.
- Keep every figure exactly as the finding states it. Do not round or generalise.
- If two findings disagree, say so and cite both. Do not choose between them and
  do not average them.
- Three or four sentences. No preamble, no heading, no bullet points.
- If the findings do not answer the sub-question, say exactly that in one
  sentence. That is a useful answer."""

SUMMARY_SYSTEM_PROMPT = """You write the opening summary of a research report.

Rules:
- Use ONLY the section texts given to you.
- Four sentences at most.
- Lead with the clearest, best-supported finding.
- If the sections disagree about anything, say so in the summary. A summary that
  hides a disagreement is worse than no summary.
- Keep citation markers like [2] exactly where they support a statement.
- Do not add a conclusion, recommendation or opinion. Report what was found."""


def format_findings_for_prompt(numbered_evidence: list[tuple[int, Evidence]]) -> str:
    """
    Lay the findings out for the model.

    The source type and date are included on purpose. A model told that one
    finding is peer-reviewed and another is a company blog writes a noticeably
    more careful paragraph than one handed twelve anonymous sentences.
    """
    lines: list[str] = []

    for marker, evidence in numbered_evidence:
        lines.append(
            "[%d] %s\n     source: %s (%s, %s)\n     quote: \"%s\""
            % (
                marker,
                evidence.claim,
                evidence.source_name,
                evidence.source_type.value,
                evidence.published_date,
                evidence.quote,
            )
        )

    return "\n\n".join(lines)
