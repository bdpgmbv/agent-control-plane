"""
LAYER 6, STEP 6 - RUN EVERY CHECK
=================================
One function, so that nothing can be skipped by accident.

Which checks apply depends on the document type. A contract has no line items and
no arithmetic, so running the sum checks on one would invent errors that are not
there. A pipeline that produces false errors gets ignored, and an ignored review
queue is worse than none - people learn to approve without reading.

Note what is NOT here: there is no check that asks a model whether the document
looks right. Every check in this layer is a calculation or a comparison, which
means every one of them is repeatable, explainable to a finance team, and free.
"""

from datetime import date

from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    LineItem,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer5_extract.step1_schemas import has_line_items
from doc_intelligence.layer6_validate.step1_fields import (
    check_field_confidence,
    check_required_fields,
    check_values_are_traceable,
)
from doc_intelligence.layer6_validate.step2_arithmetic import run_arithmetic_checks
from doc_intelligence.layer6_validate.step3_identifiers import run_identifier_checks
from doc_intelligence.layer6_validate.step4_dates import run_date_checks
from doc_intelligence.layer6_validate.step5_duplicates import SeenDocument, run_duplicate_checks


class ValidationSettings:
    """
    The numbers the checks need, gathered in one place.

    Passed in rather than imported so that tests and the threshold-tuning script
    can vary them without editing .env - the same reason project 01 could measure
    its retrieval threshold instead of guessing it.
    """

    def __init__(self, arithmetic_tolerance: float = 0.02,
                 tax_tolerance: float = 0.05,
                 minimum_required_field_confidence: float = 0.70,
                 future_days: float = 30.0,
                 maximum_age_days: float = 1825.0,
                 today: date | None = None) -> None:
        self.arithmetic_tolerance = arithmetic_tolerance
        self.tax_tolerance = tax_tolerance
        self.minimum_required_field_confidence = minimum_required_field_confidence
        self.future_days = future_days
        self.maximum_age_days = maximum_age_days
        if today is None:
            today = date.today()
        self.today = today


def validate_document(document_id: str, document_type: DocumentType,
                      text: str,
                      fields: list[ExtractedField],
                      line_items: list[LineItem],
                      settings: ValidationSettings,
                      already_seen: list[SeenDocument] | None = None,
                      had_scanner_repairs: bool = False) -> list[ValidationIssue]:
    if already_seen is None:
        already_seen = []

    issues: list[ValidationIssue] = []

    if document_type == DocumentType.UNKNOWN:
        issues.append(ValidationIssue(
            code="document_type_not_recognised",
            severity=Severity.ERROR,
            field="document_type",
            message="this is not a document type the pipeline knows how to "
                    "process, so nothing was extracted and nothing can be "
                    "checked. A person needs to look at it.",
            expected="invoice, receipt, purchase order or contract",
            actual="unknown",
        ))
        return issues

    for issue in check_required_fields(document_type, fields):
        issues.append(issue)
    for issue in check_values_are_traceable(fields):
        issues.append(issue)
    for issue in check_field_confidence(document_type, fields,
                                        settings.minimum_required_field_confidence):
        issues.append(issue)

    if has_line_items(document_type):
        for issue in run_arithmetic_checks(line_items, fields,
                                           settings.arithmetic_tolerance,
                                           settings.tax_tolerance):
            issues.append(issue)

    for issue in run_identifier_checks(document_type, fields, had_scanner_repairs):
        issues.append(issue)

    for issue in run_date_checks(document_type, fields, settings.today,
                                 settings.future_days, settings.maximum_age_days):
        issues.append(issue)

    for issue in run_duplicate_checks(document_id, text, fields, already_seen):
        issues.append(issue)

    return issues


def sort_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    """Errors before warnings before notes, so the queue reads worst-first."""
    order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}

    def key(issue: ValidationIssue):
        return (order.get(issue.severity, 3), issue.code)

    sorted_issues = list(issues)
    sorted_issues.sort(key=key)
    return sorted_issues
