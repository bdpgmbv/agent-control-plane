"""
LAYER 7 - SYNTHESIS, STEP 1: WRITING THE REPORT
===============================================
Turns evidence into the deliverable: a summary, one section per sub-question,
the conflicts, and a numbered citation list.

THE ORDER MATTERS
    Sections are written first, from evidence. The summary is written last, from
    the sections. Writing the summary from the raw evidence produces a summary
    that says things no section supports, and the reader then cannot find where
    any of it came from.

SYNTHESIS SPENDS FROM THE RESERVE
    Every model call here passes for_synthesis=True, which is the one place
    allowed into the budget's reserve. Research will consume everything you give
    it; without a reserve you pay full price for a pile of evidence and no report.
"""

from research_agent.layer0_shared.llm_client import TASK_SYNTHESISE
from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer2_models.schemas import (
    Citation,
    Conflict,
    Evidence,
    Report,
    ReportSection,
    SubQuestion,
    SubQuestionStatus,
)
from research_agent.layer7_synthesis.prompts import (
    SECTION_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    format_findings_for_prompt,
)

log = get_logger(__name__)


def number_the_evidence(
    sub_questions: list[SubQuestion],
    evidence_by_id: dict[str, Evidence],
) -> tuple[dict[str, int], list[Evidence]]:
    """
    Give every surviving piece of evidence a citation number.

    Numbered in the order the reader meets them - sub-question by sub-question,
    best evidence first - so [1] is near the top of the report rather than
    somewhere in the middle.
    """
    marker_by_evidence_id: dict[str, int] = {}
    ordered: list[Evidence] = []
    next_marker = 1

    for sub_question in sub_questions:
        items: list[Evidence] = []
        for evidence_id in sub_question.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is not None:
                items.append(evidence)

        items.sort(key=lambda item: item.score, reverse=True)

        for evidence in items:
            if evidence.evidence_id in marker_by_evidence_id:
                continue
            marker_by_evidence_id[evidence.evidence_id] = next_marker
            ordered.append(evidence)
            next_marker = next_marker + 1

    return (marker_by_evidence_id, ordered)


def write_section_with_rules(sub_question_text: str, numbered: list[tuple[int, Evidence]]) -> str:
    """
    The no-model section writer.

    It states each finding and attaches its marker. It cannot connect two
    findings into an argument, which is the thing a model is genuinely good at -
    but it also cannot state anything the evidence does not say, because every
    sentence it writes came out of a source.
    """
    if len(numbered) == 0:
        return "No usable evidence was found for this sub-question."

    sentences: list[str] = []
    for marker, evidence in numbered:
        claim = evidence.claim.strip()
        # The marker goes BEFORE the full stop, so that splitting the report into
        # sentences keeps each marker with the sentence it belongs to. Putting it
        # after the stop attaches it to the next sentence instead, and the
        # verifier then reports perfectly cited text as uncited.
        while claim.endswith("."):
            claim = claim[:-1]
        sentences.append("%s [%d]." % (claim, marker))

    return " ".join(sentences)


def write_section(sub_question_text: str, numbered: list[tuple[int, Evidence]], model) -> str:
    if len(numbered) == 0:
        return "No usable evidence was found for this sub-question."

    if not model.is_live:
        return write_section_with_rules(sub_question_text, numbered)

    user_prompt = (
        "SUB-QUESTION: " + sub_question_text + "\n\nFINDINGS:\n"
        + format_findings_for_prompt(numbered)
    )

    result = model.complete(
        system_prompt=SECTION_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        task=TASK_SYNTHESISE,
        max_tokens=450,
        for_synthesis=True,
        describe=(
            "the section for '%s' was written from the evidence without the model"
            % sub_question_text[:44]
        ),
    )

    if result is None or result.text.strip() == "":
        # Out of budget, or the model returned nothing. The rule-based version
        # still produces a correct, cited section.
        return write_section_with_rules(sub_question_text, numbered)

    return result.text.strip()


def write_summary_with_rules(sections: list[ReportSection]) -> str:
    """Take the first sentence of each section that had evidence."""
    pieces: list[str] = []

    for section in sections:
        if section.status != SubQuestionStatus.RESEARCHED:
            continue
        text = section.findings.strip()
        if text == "":
            continue

        first_stop = text.find(". ")
        if first_stop == -1:
            pieces.append(text)
        else:
            pieces.append(text[: first_stop + 1])

        if len(pieces) >= 4:
            break

    if len(pieces) == 0:
        return "The research did not produce usable findings for this question."
    return " ".join(pieces)


def write_summary(question: str, sections: list[ReportSection], model) -> str:
    if not model.is_live:
        return write_summary_with_rules(sections)

    section_texts: list[str] = []
    for section in sections:
        if section.findings.strip() == "":
            continue
        section_texts.append(section.sub_question + "\n" + section.findings)

    if len(section_texts) == 0:
        return "The research did not produce usable findings for this question."

    user_prompt = "RESEARCH QUESTION: " + question + "\n\nSECTIONS:\n\n" + "\n\n".join(section_texts)

    result = model.complete(
        system_prompt=SUMMARY_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        task=TASK_SYNTHESISE,
        max_tokens=350,
        for_synthesis=True,
        describe="the summary was written from the sections without the model",
    )

    if result is None or result.text.strip() == "":
        return write_summary_with_rules(sections)

    return result.text.strip()


def build_citations(
    ordered_evidence: list[Evidence],
    marker_by_evidence_id: dict[str, int],
) -> list[Citation]:
    citations: list[Citation] = []

    for evidence in ordered_evidence:
        citations.append(
            Citation(
                marker=marker_by_evidence_id[evidence.evidence_id],
                evidence_id=evidence.evidence_id,
                title=evidence.title,
                source_name=evidence.source_name,
                source_type=evidence.source_type.value,
                published_date=evidence.published_date,
                url=evidence.url,
                quote=evidence.quote,
                credibility=evidence.credibility,
            )
        )

    citations.sort(key=lambda citation: citation.marker)
    return citations


def build_report(
    question: str,
    sub_questions: list[SubQuestion],
    evidence_by_id: dict[str, Evidence],
    conflicts: list[Conflict],
    model,
) -> Report:
    """Assemble the whole report."""
    marker_by_evidence_id, ordered_evidence = number_the_evidence(sub_questions, evidence_by_id)

    sections: list[ReportSection] = []

    for sub_question in sub_questions:
        numbered: list[tuple[int, Evidence]] = []
        for evidence_id in sub_question.evidence_ids:
            evidence = evidence_by_id.get(evidence_id)
            if evidence is None:
                continue
            numbered.append((marker_by_evidence_id[evidence_id], evidence))

        numbered.sort(key=lambda pair: pair[0])

        if sub_question.status == SubQuestionStatus.SKIPPED_NO_BUDGET:
            findings = ""
        else:
            findings = write_section(sub_question.text, numbered, model)

        markers: list[int] = []
        evidence_ids: list[str] = []
        for marker, evidence in numbered:
            markers.append(marker)
            evidence_ids.append(evidence.evidence_id)

        sections.append(
            ReportSection(
                sub_question=sub_question.text,
                status=sub_question.status,
                findings=findings,
                citation_markers=markers,
                evidence_ids=evidence_ids,
                note=sub_question.note,
            )
        )

    summary = write_summary(question, sections, model)

    log_event(
        log,
        "report.built",
        sections=len(sections),
        citations=len(ordered_evidence),
        conflicts=len(conflicts),
    )

    return Report(
        question=question,
        summary=summary,
        sections=sections,
        conflicts=conflicts,
        citations=build_citations(ordered_evidence, marker_by_evidence_id),
    )
