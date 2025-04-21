"""
LAYER 7, STEP 1 - HOW MUCH CAN THIS EXTRACTION BE TRUSTED?
==========================================================
Confidence here answers exactly one question:

    did we read this document correctly?

It does NOT answer "is this document correct?". Those are different questions
with different answers, and keeping them apart is the most important decision in
this layer.

Consider two invoices. The first is a crisp PDF whose line items do not add up.
The second is a smudged fax that adds up perfectly. Blend validity into
confidence and both land in the middle, indistinguishable. Keep them apart and
you can say something useful about each: the first was read perfectly and is
wrong, the second may have been misread and is at least self-consistent. Those
need different things from a human - one needs a query to the supplier, the other
needs someone to squint at the paper.

So errors never reduce confidence. They block approval in step 2, on their own
terms.

Everything that DOES feed confidence is an observable fact about the reading:

  - how clearly the document type was identified
  - how well each required field could be read
  - how many scanner repairs the text needed
  - how many things looked odd enough to warn about

Nothing in this file asks a model how sure it is.
"""

from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer5_extract.step1_schemas import required_field_names

# How the two halves are weighted. The type matters less than the fields: getting
# the type right only buys you the right questions, while the field readings are
# the answers.
TYPE_WEIGHT = 0.25
FIELDS_WEIGHT = 0.75

# Text somebody's software had to repair is text that might still be wrong.
PENALTY_PER_REPAIR = 0.02
MAXIMUM_REPAIR_PENALTY = 0.08

# A warning is not proof of a misreading, but several of them together usually
# mean the document is not as clean as the field confidences suggest.
PENALTY_PER_WARNING = 0.03
MAXIMUM_WARNING_PENALTY = 0.12


@dataclass
class ConfidenceReport:
    confidence: float = 0.0
    type_confidence: float = 0.0
    fields_confidence: float = 0.0
    weakest_field: str = ""
    weakest_field_confidence: float = 0.0
    repair_penalty: float = 0.0
    warning_penalty: float = 0.0
    explanation: list[str] = field(default_factory=list)


def required_field_confidences(document_type: DocumentType,
                               fields: list[ExtractedField]) -> list[tuple[str, float]]:
    wanted = required_field_names(document_type)
    found: list[tuple[str, float]] = []

    for name in wanted:
        for candidate in fields:
            if candidate.name != name:
                continue
            if candidate.is_present():
                found.append((name, candidate.confidence))
            else:
                # A missing required field is a zero, not an absence. Leaving it
                # out of the average would make a document with three of four
                # required fields score the same as one with all four.
                found.append((name, 0.0))
            break

    return found


def score_confidence(document_type: DocumentType, type_confidence: float,
                     fields: list[ExtractedField],
                     issues: list[ValidationIssue],
                     scanner_repair_count: int) -> ConfidenceReport:
    report = ConfidenceReport(type_confidence=round(type_confidence, 3))

    if document_type == DocumentType.UNKNOWN:
        report.confidence = 0.0
        report.explanation.append(
            "the document type could not be identified, so there is nothing to "
            "be confident about"
        )
        return report

    per_field = required_field_confidences(document_type, fields)

    if len(per_field) == 0:
        report.fields_confidence = 0.0
        report.explanation.append("this document type has no required fields")
    else:
        total = 0.0
        weakest_name = per_field[0][0]
        weakest_value = per_field[0][1]
        for name, value in per_field:
            total = total + value
            if value < weakest_value:
                weakest_value = value
                weakest_name = name
        report.fields_confidence = round(total / len(per_field), 3)
        report.weakest_field = weakest_name
        report.weakest_field_confidence = round(weakest_value, 3)

    warning_count = 0
    for issue in issues:
        if issue.severity == Severity.WARNING:
            warning_count = warning_count + 1

    repair_penalty = scanner_repair_count * PENALTY_PER_REPAIR
    if repair_penalty > MAXIMUM_REPAIR_PENALTY:
        repair_penalty = MAXIMUM_REPAIR_PENALTY

    warning_penalty = warning_count * PENALTY_PER_WARNING
    if warning_penalty > MAXIMUM_WARNING_PENALTY:
        warning_penalty = MAXIMUM_WARNING_PENALTY

    report.repair_penalty = round(repair_penalty, 3)
    report.warning_penalty = round(warning_penalty, 3)

    blended = (type_confidence * TYPE_WEIGHT
               + report.fields_confidence * FIELDS_WEIGHT)
    confidence = blended - repair_penalty - warning_penalty

    if confidence < 0.0:
        confidence = 0.0
    if confidence > 1.0:
        confidence = 1.0
    report.confidence = round(confidence, 3)

    report.explanation.append(
        "document type identified with confidence %.2f (weight %.0f%%)"
        % (type_confidence, TYPE_WEIGHT * 100)
    )
    report.explanation.append(
        "the %d required field(s) averaged %.2f (weight %.0f%%), weakest was %s "
        "at %.2f" % (len(per_field), report.fields_confidence,
                     FIELDS_WEIGHT * 100, report.weakest_field or "n/a",
                     report.weakest_field_confidence)
    )
    if scanner_repair_count > 0:
        report.explanation.append(
            "%d scanner repair(s) cost %.2f" % (scanner_repair_count, repair_penalty)
        )
    if warning_count > 0:
        report.explanation.append(
            "%d warning(s) cost %.2f" % (warning_count, warning_penalty)
        )
    report.explanation.append(
        "errors are deliberately NOT counted here - they block approval in their "
        "own right, because 'I read it correctly' and 'it is correct' are "
        "different claims"
    )

    return report
