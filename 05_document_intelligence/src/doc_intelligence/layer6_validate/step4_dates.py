"""
LAYER 6, STEP 4 - DO THE DATES MAKE SENSE TOGETHER?
===================================================
Individual dates were already parsed and given a confidence in layer 0. This step
asks the questions that only make sense once you have more than one of them:

    is the invoice dated in the future?
    is payment due before the invoice was issued?
    does the contract end before it starts?

None of these need a model, and none of them can be answered by looking at one
field. They are relationships, and relationships are what a schema buys you.

The reference date is passed in rather than read from the clock, so the tests are
not a different set of tests tomorrow.
"""

from datetime import date, timedelta

from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    Severity,
    ValidationIssue,
)


def date_value(fields: list[ExtractedField], name: str) -> date | None:
    for field in fields:
        if field.name != name or not field.is_present():
            continue
        try:
            return date.fromisoformat(field.value)
        except ValueError:
            return None
    return None


def check_not_in_the_future(fields: list[ExtractedField], primary_date_field: str,
                            today: date, allowed_days: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    issued = date_value(fields, primary_date_field)
    if issued is None:
        return issues

    limit = today + timedelta(days=allowed_days)
    if issued <= limit:
        return issues

    days_ahead = (issued - today).days
    issues.append(ValidationIssue(
        code="document_dated_in_the_future",
        severity=Severity.ERROR,
        field=primary_date_field,
        message="this document is dated %s, which is %d days in the future. The "
                "usual cause is a mistyped or misread year."
                % (issued.isoformat(), days_ahead),
        expected="on or before %s" % limit.isoformat(),
        actual=issued.isoformat(),
    ))
    return issues


def check_not_too_old(fields: list[ExtractedField], primary_date_field: str,
                      today: date, maximum_days: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    issued = date_value(fields, primary_date_field)
    if issued is None:
        return issues

    age_days = (today - issued).days
    if age_days <= maximum_days:
        return issues

    issues.append(ValidationIssue(
        code="document_very_old",
        severity=Severity.WARNING,
        field=primary_date_field,
        message="this document is dated %s, which is %d days ago. It may already "
                "have been dealt with." % (issued.isoformat(), age_days),
        expected="within the last %d days" % int(maximum_days),
        actual="%d days old" % age_days,
    ))
    return issues


def check_due_after_issue(fields: list[ExtractedField], issue_field: str,
                          due_field: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    issued = date_value(fields, issue_field)
    due = date_value(fields, due_field)
    if issued is None or due is None:
        return issues

    if due >= issued:
        return issues

    issues.append(ValidationIssue(
        code="due_before_issue",
        severity=Severity.ERROR,
        field=due_field,
        message="payment is due on %s but the document is dated %s - the due "
                "date is before the document exists"
                % (due.isoformat(), issued.isoformat()),
        expected="on or after %s" % issued.isoformat(),
        actual=due.isoformat(),
    ))
    return issues


def check_term_order(fields: list[ExtractedField]) -> list[ValidationIssue]:
    """A contract that ends before it starts."""
    issues: list[ValidationIssue] = []

    start = date_value(fields, "start_date")
    end = date_value(fields, "end_date")
    if start is None or end is None:
        return issues

    if end > start:
        return issues

    issues.append(ValidationIssue(
        code="term_ends_before_it_starts",
        severity=Severity.ERROR,
        field="end_date",
        message="the term runs from %s to %s, which is not a term"
                % (start.isoformat(), end.isoformat()),
        expected="an end date after %s" % start.isoformat(),
        actual=end.isoformat(),
    ))
    return issues


# Which date field is the document's own date, per type. A purchase order is
# dated by when it was ordered, a contract by when it was agreed.
PRIMARY_DATE_FIELD = {
    DocumentType.INVOICE: "invoice_date",
    DocumentType.RECEIPT: "receipt_date",
    DocumentType.PURCHASE_ORDER: "order_date",
    DocumentType.CONTRACT: "agreement_date",
}


def run_date_checks(document_type: DocumentType, fields: list[ExtractedField],
                    today: date, future_days: float,
                    maximum_age_days: float) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if document_type not in PRIMARY_DATE_FIELD:
        return issues

    primary = PRIMARY_DATE_FIELD[document_type]

    for issue in check_not_in_the_future(fields, primary, today, future_days):
        issues.append(issue)
    for issue in check_not_too_old(fields, primary, today, maximum_age_days):
        issues.append(issue)

    if document_type == DocumentType.INVOICE:
        for issue in check_due_after_issue(fields, "invoice_date", "due_date"):
            issues.append(issue)
    if document_type == DocumentType.PURCHASE_ORDER:
        for issue in check_due_after_issue(fields, "order_date", "required_by_date"):
            issues.append(issue)
    if document_type == DocumentType.CONTRACT:
        for issue in check_term_order(fields):
            issues.append(issue)
        # Deliberately NOT checking that start_date follows agreement_date. A
        # contract signed in April can take effect from January, and backdating
        # is ordinary commercial practice. An "error" that fires on correct
        # documents trains people to click past the queue, which costs more than
        # the check was ever going to catch.

    return issues
