"""
LAYER 7 - SYNTHESIS, STEP 2: CHECKING THE REPORT BEFORE SHIPPING IT
===================================================================
The prompts ask for markers on every sentence and no invented figures. This is
where that is checked, because a prompt is a request.

Four checks:

  INVENTED CITATION MARKERS
      The model writes [9] when there are six findings. The marker is stripped -
      leaving it in is worse than removing it, because a reader who follows a
      citation into nothing loses trust in all the others.

  UNCITED SENTENCES
      A sentence with no marker is a sentence nobody can check. Counted, and it
      pushes the confidence score down.

  FIGURES THAT ARE NOT IN THE EVIDENCE
      The most dangerous failure, because it looks like precision. The model
      writes "productivity rose 15 percent" when the finding said 12. Every
      number in the report is checked against the numbers in the evidence.

  CONFIDENCE
      One number, from things that were measured rather than from asking the
      model how sure it is. A model's stated confidence describes how fluent its
      output felt, not whether it is right.
"""

import re

from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer0_shared.similarity import values_are_close
from research_agent.layer0_shared.text_tools import extract_numbers, to_sentences
from research_agent.layer2_models.schemas import Evidence, Report, SubQuestionStatus

log = get_logger(__name__)

CITATION_MARKER = re.compile(r"\[(\d+)\]")

# A marker written AFTER the full stop belongs to the sentence before it.
# Both styles appear in practice - the rule-based writer puts it inside, models
# often put it outside - and splitting on sentences without normalising first
# hands every marker to the wrong sentence, making a fully cited report look
# entirely uncited.
MARKER_AFTER_STOP = re.compile(r"([.!?])(\s*)((?:\[\d+\]\s*)+)")


def move_markers_inside_sentences(text: str) -> str:
    """Rewrite 'claim. [2]' as 'claim [2].' so sentence splitting keeps them together."""

    def swap(match):
        punctuation = match.group(1)
        markers = match.group(3).strip()
        return " " + markers + punctuation + " "

    return MARKER_AFTER_STOP.sub(swap, text).strip()


class VerificationResult:
    def __init__(self) -> None:
        self.invented_markers: list[int] = []
        self.uncited_sentences: list[str] = []
        self.unsupported_numbers: list[str] = []
        self.sentences_checked = 0

    def to_dict(self) -> dict:
        return {
            "invented_markers": self.invented_markers,
            "uncited_sentences": len(self.uncited_sentences),
            "unsupported_numbers": self.unsupported_numbers,
            "sentences_checked": self.sentences_checked,
        }

    def is_clean(self) -> bool:
        return (
            len(self.invented_markers) == 0
            and len(self.unsupported_numbers) == 0
            and len(self.uncited_sentences) == 0
        )


def strip_invented_markers(text: str, valid_markers: set[int], found: list[int]) -> str:
    """Remove any [n] that does not point at a real citation."""

    def replace(match):
        number = int(match.group(1))
        if number in valid_markers:
            return match.group(0)
        if number not in found:
            found.append(number)
        return ""

    return CITATION_MARKER.sub(replace, text)


def numbers_are_supported(text: str, allowed_numbers: list[float]) -> list[str]:
    """
    Every figure in the text must appear in the evidence.

    Small integers are ignored: "three sectors" or "two studies" are counting
    words the writer is entitled to use, not claims about the data. Anything
    larger, or with a decimal point, has to come from somewhere.

    ------------------------------------------------------------------------
    CITATION MARKERS ARE STRIPPED FIRST, AND THAT IS NOT A DETAIL.
    ------------------------------------------------------------------------
    A correct, fully sourced report ending "...not expected until at least 2033
    [10][11]." was reported as containing two invented figures: 11 and 12. They
    were the citation numbers. Markers below 10 slipped past the small-integer
    rule, so the bug only appeared on reports with more than ten sources.

    A verification step that raises false alarms is worse than one that does
    nothing, because the first thing anyone does with a noisy alarm is switch it
    off - and then the real ones go unnoticed too.
    """
    unsupported: list[str] = []

    text_without_markers = CITATION_MARKER.sub(" ", text)

    for value in extract_numbers(text_without_markers):
        if value == int(value) and abs(value) <= 10:
            continue

        supported = False
        for allowed in allowed_numbers:
            if values_are_close(value, allowed):
                supported = True
                break

        if not supported:
            unsupported.append(str(value))

    return unsupported


def collect_allowed_numbers(evidence_items: list[Evidence]) -> list[float]:
    """Every number that appears in any claim or quote, plus every year."""
    allowed: list[float] = []

    for evidence in evidence_items:
        for value in extract_numbers(evidence.claim):
            allowed.append(value)
        for value in extract_numbers(evidence.quote):
            allowed.append(value)
        for value in extract_numbers(evidence.published_date):
            allowed.append(value)

    return allowed


def collect_allowed_numbers_from_citations(report) -> list[float]:
    """
    The same list, read from the report's own citations.

    A report should be checkable ON ITS OWN. Reading the permitted figures out of
    a separate evidence dictionary means verification silently passes - or
    silently fails everything - whenever that dictionary is not to hand. The
    citations already carry the quote and the date, which is everything needed.
    """
    allowed: list[float] = []

    for citation in report.citations:
        for value in extract_numbers(citation.quote):
            allowed.append(value)
        for value in extract_numbers(citation.published_date):
            allowed.append(value)

    return allowed


def verify_report(report: Report, evidence_by_id: dict[str, Evidence]) -> VerificationResult:
    """Check the report and clean it up in place."""
    verification = VerificationResult()

    valid_markers: set[int] = set()
    for citation in report.citations:
        valid_markers.add(citation.marker)

    # Read from the citations, so the report can be verified on its own. Any
    # evidence objects we happen to hold add their claims on top.
    allowed_numbers = collect_allowed_numbers_from_citations(report)

    cited_evidence: list[Evidence] = []
    for citation in report.citations:
        evidence = evidence_by_id.get(citation.evidence_id)
        if evidence is not None:
            cited_evidence.append(evidence)

    for value in collect_allowed_numbers(cited_evidence):
        allowed_numbers.append(value)

    # --- sections ---
    for section in report.sections:
        if section.status == SubQuestionStatus.SKIPPED_NO_BUDGET:
            continue

        # A section with no evidence says so in one sentence, and that sentence
        # has nothing to cite. Counting it as an uncited claim punishes the system
        # for being honest about a gap, and pushes confidence down for the one
        # behaviour we most want.
        if len(section.citation_markers) == 0:
            continue

        section.findings = strip_invented_markers(
            section.findings, valid_markers, verification.invented_markers
        )

        for sentence in to_sentences(move_markers_inside_sentences(section.findings)):
            verification.sentences_checked = verification.sentences_checked + 1

            if len(CITATION_MARKER.findall(sentence)) == 0:
                if len(sentence.split()) >= 6:
                    verification.uncited_sentences.append(sentence)

            for figure in numbers_are_supported(sentence, allowed_numbers):
                verification.unsupported_numbers.append(figure)

    # --- the summary ---
    report.summary = strip_invented_markers(
        report.summary, valid_markers, verification.invented_markers
    )
    for figure in numbers_are_supported(report.summary, allowed_numbers):
        if figure not in verification.unsupported_numbers:
            verification.unsupported_numbers.append(figure)

    if len(verification.invented_markers) > 0:
        metrics.increment("invented_citations_total", len(verification.invented_markers))
    if len(verification.unsupported_numbers) > 0:
        metrics.increment("unsupported_numbers_total", len(verification.unsupported_numbers))

    if not verification.is_clean():
        log_event(log, "report.verification_problems", detail=verification.to_dict())

    return verification


def score_confidence(
    report: Report,
    verification: VerificationResult,
    sub_question_count: int,
    researched_count: int,
    budget_exhausted: bool,
) -> tuple[float, str]:
    """
    How much to trust this report, from things that were measured.

    Five inputs, each of which is a real reason to trust it less:
      coverage      how many sub-questions actually got researched
      support       how much evidence there is, and how credible
      cleanliness   invented markers or unsupported figures
      conflicts     unresolved disagreement between sources
      completeness  whether the run ran out of budget
    """
    reasons: list[str] = []

    # --- coverage ---
    if sub_question_count == 0:
        coverage = 0.0
    else:
        coverage = researched_count / sub_question_count
    if coverage < 1.0:
        reasons.append(
            "only %d of %d sub-questions were researched" % (researched_count, sub_question_count)
        )

    # --- support ---
    if len(report.citations) == 0:
        support = 0.0
        reasons.append("no evidence was cited")
    else:
        total_credibility = 0.0
        for citation in report.citations:
            total_credibility = total_credibility + citation.credibility
        average_credibility = total_credibility / len(report.citations)

        # Enough sources, and credible ones. Four good citations is a reasonable
        # bar; more than that adds little.
        volume = min(1.0, len(report.citations) / 4.0)
        support = (0.6 * average_credibility) + (0.4 * volume)

        if average_credibility < 0.6:
            reasons.append("the evidence comes mostly from low-credibility sources")

    # --- cleanliness ---
    cleanliness = 1.0
    if len(verification.invented_markers) > 0:
        cleanliness = cleanliness - 0.4
        reasons.append("the report contained %d invented citation markers" % len(verification.invented_markers))
    if len(verification.unsupported_numbers) > 0:
        cleanliness = cleanliness - 0.4
        reasons.append(
            "%d figures in the report do not appear in the evidence"
            % len(verification.unsupported_numbers)
        )
    if len(verification.uncited_sentences) > 0:
        cleanliness = cleanliness - 0.2
        reasons.append("%d sentences carry no citation" % len(verification.uncited_sentences))
    if cleanliness < 0.0:
        cleanliness = 0.0

    # --- conflicts ---
    if len(report.conflicts) == 0:
        agreement = 1.0
    else:
        agreement = max(0.4, 1.0 - (0.2 * len(report.conflicts)))
        reasons.append(
            "%d unresolved disagreement(s) between sources" % len(report.conflicts)
        )

    # --- completeness ---
    if budget_exhausted:
        completeness = 0.6
        reasons.append("the run reached a budget limit before finishing")
    else:
        completeness = 1.0

    confidence = (
        (0.25 * coverage)
        + (0.25 * support)
        + (0.25 * cleanliness)
        + (0.15 * agreement)
        + (0.10 * completeness)
    )

    # A REPORT THAT CITES NOTHING CANNOT SCORE HALF MARKS.
    #
    # Cleanliness and agreement both give full marks for having no problems, and
    # a report with no evidence has no problems - no invented markers, no
    # unsupported figures, no disagreements. It was scoring 0.50, which reads as
    # "reasonably confident" for a report that found nothing at all.
    #
    # Having nothing to say is not the same as saying it well.
    if len(report.citations) == 0:
        confidence = min(confidence, 0.25)

    if len(reasons) == 0:
        explanation = "All sub-questions were researched, every claim is cited, and the sources agree."
    else:
        explanation = "Reduced because " + "; ".join(reasons) + "."

    return (round(min(1.0, confidence), 4), explanation)
