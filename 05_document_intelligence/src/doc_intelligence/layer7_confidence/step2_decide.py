"""
LAYER 7, STEP 2 - AUTOMATE IT, OR SEND IT TO A PERSON
=====================================================
Four gates. A document is processed automatically only if it passes all of them.

  1. no errors                   something that does not add up is never paid,
                                 however clearly it was printed
  2. confidence >= 0.90          the reading itself has to be trustworthy
  3. every required field >= 0.70 checked individually, because an average lets a
                                 crisp supplier name hide an unreadable total
  4. the amount is below 10,000   a large payment gets a person regardless

Gate 4 is not a confidence judgement and is not meant to be. The pipeline can be
completely right about a 60,000 invoice and it should still stop, because the cost
of being wrong scales with the number and the cost of a human glance does not.
Every straight-through-processing system worth trusting has a rule like this, and
it is the one rule that cannot be tuned away by better extraction.

REJECT means something narrow and useful: there is no document here to review.
An empty file, or a scan with no vision model available. Sending that to a human
queue wastes their time - the input has to be supplied again. Everything else
that fails a gate becomes NEEDS_REVIEW, because a person can act on it.
"""

from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import (
    Decision,
    DocumentType,
    ExtractedField,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer5_extract.step1_schemas import required_field_names

# Fields that represent the money at stake. A contract's value counts as much as
# an invoice's total - a 200,000 agreement is not low-risk because nobody is
# being asked to pay it today.
AMOUNT_FIELDS = ("total_amount", "contract_value")


@dataclass
class DecisionReport:
    decision: Decision = Decision.NEEDS_REVIEW
    reasons: list[str] = field(default_factory=list)
    gates_passed: list[str] = field(default_factory=list)
    amount_at_stake: float = 0.0


def largest_amount(fields: list[ExtractedField]) -> float:
    largest = 0.0
    for candidate in fields:
        if candidate.name not in AMOUNT_FIELDS or not candidate.is_present():
            continue
        try:
            value = abs(float(candidate.value))
        except ValueError:
            continue
        if value > largest:
            largest = value
    return largest


def decide(document_type: DocumentType, confidence: float,
           fields: list[ExtractedField], issues: list[ValidationIssue],
           has_usable_text: bool,
           auto_approve_confidence: float,
           minimum_required_field_confidence: float,
           always_review_above: float) -> DecisionReport:
    report = DecisionReport()
    report.amount_at_stake = largest_amount(fields)

    if not has_usable_text:
        report.decision = Decision.REJECT
        report.reasons.append(
            "there is no readable text in this document, so there is nothing to "
            "review. It needs to be supplied again - as a text or PDF file, or "
            "with a vision model configured to read the scan."
        )
        return report

    if document_type == DocumentType.UNKNOWN:
        report.decision = Decision.NEEDS_REVIEW
        report.reasons.append(
            "the document type was not recognised, so no fields were extracted. "
            "Someone needs to say what this document is."
        )
        return report

    # ---- gate 1: errors ----
    errors: list[ValidationIssue] = []
    for issue in issues:
        if issue.severity == Severity.ERROR:
            errors.append(issue)

    if len(errors) > 0:
        report.decision = Decision.NEEDS_REVIEW
        for issue in errors:
            report.reasons.append(issue.message)
        return report
    report.gates_passed.append("no validation errors")

    # ---- gate 2: overall confidence ----
    if confidence < auto_approve_confidence:
        report.decision = Decision.NEEDS_REVIEW
        report.reasons.append(
            "the extraction scored %.2f, below the %.2f needed to process a "
            "document without a person looking at it"
            % (confidence, auto_approve_confidence)
        )
        return report
    report.gates_passed.append("confidence %.2f >= %.2f" % (confidence, auto_approve_confidence))

    # ---- gate 3: every required field, individually ----
    wanted = required_field_names(document_type)
    for name in wanted:
        for candidate in fields:
            if candidate.name != name:
                continue
            if candidate.confidence < minimum_required_field_confidence:
                report.decision = Decision.NEEDS_REVIEW
                report.reasons.append(
                    "%s was read with confidence %.2f, below the %.2f a required "
                    "field must reach on its own"
                    % (name.replace("_", " "), candidate.confidence,
                       minimum_required_field_confidence)
                )
                return report
            break
    report.gates_passed.append(
        "all %d required field(s) above %.2f" % (len(wanted), minimum_required_field_confidence)
    )

    # ---- gate 4: the amount ----
    if report.amount_at_stake > always_review_above:
        report.decision = Decision.NEEDS_REVIEW
        report.reasons.append(
            "the amount at stake is %.2f, above the %.2f limit for automatic "
            "processing. This is not a doubt about the reading - the extraction "
            "passed every other gate. It is a policy that large amounts get a "
            "person." % (report.amount_at_stake, always_review_above)
        )
        return report
    report.gates_passed.append(
        "amount %.2f is at or below the %.2f limit" % (report.amount_at_stake, always_review_above)
    )

    report.decision = Decision.AUTO_APPROVE
    report.reasons.append(
        "passed every gate: %s" % "; ".join(report.gates_passed)
    )
    return report
