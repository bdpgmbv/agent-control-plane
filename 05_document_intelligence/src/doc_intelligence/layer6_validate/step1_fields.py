"""
LAYER 6, STEP 1 - IS EVERYTHING THERE, AND CAN IT BE TRUSTED?
=============================================================
The cheapest checks, run first: did every required field arrive, and is any
value here one nobody can point at on the page?

The second question is the interesting one. An extraction pipeline that only
asks "did I get a value?" will happily approve an invented total. This step turns
the verbatim flag from layer 5 into a validation issue, so a value the model
produced out of nowhere becomes a visible problem rather than a quiet number.
"""

from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    FieldSource,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer5_extract.step1_schemas import fields_for, spec_for


def check_required_fields(document_type: DocumentType,
                          fields: list[ExtractedField]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    present_names = []
    for field in fields:
        if field.is_present():
            present_names.append(field.name)

    for spec in fields_for(document_type):
        if not spec.required:
            continue
        if spec.name in present_names:
            continue
        issues.append(ValidationIssue(
            code="required_field_missing",
            severity=Severity.ERROR,
            field=spec.name,
            message="a %s must have a %s, and none was found"
                    % (document_type.value, spec.name.replace("_", " ")),
            expected="a value",
            actual="nothing",
        ))

    return issues


def check_values_are_traceable(fields: list[ExtractedField]) -> list[ValidationIssue]:
    """
    Flag any value that does not appear in the document.

    A value from the pattern extractor is traceable by construction - it was cut
    out of a known position in the text. A value from the model is only traceable
    if the verbatim check passed. This is the difference between "the pipeline
    says the total is 2,787.60" and "the document says the total is 2,787.60",
    and it is the difference that decides whether anyone should pay it.
    """
    issues: list[ValidationIssue] = []

    for field in fields:
        if not field.is_present():
            continue
        if field.found_verbatim:
            continue
        if field.source == FieldSource.CORRECTED:
            # A person typed this in. They are the authority, not the page.
            continue
        if field.source == FieldSource.COMPUTED:
            # Derived on purpose, and its own note explains from what.
            continue

        issues.append(ValidationIssue(
            code="value_not_in_document",
            severity=Severity.ERROR,
            field=field.name,
            message="the value for %s does not appear anywhere in the document, "
                    "so it was not read off the page"
                    % field.name.replace("_", " "),
            expected="a value quoted from the document",
            actual=field.raw_value[:60],
        ))

    return issues


def check_field_confidence(document_type: DocumentType, fields: list[ExtractedField],
                           minimum: float) -> list[ValidationIssue]:
    """
    A required field read with low confidence is a warning in its own right.

    Averaging confidence across fields would let a crisply printed supplier name
    hide a total nobody could read. Each required field has to clear the bar on
    its own.
    """
    issues: list[ValidationIssue] = []

    for field in fields:
        if not field.is_present():
            continue
        spec = spec_for(document_type, field.name)
        if spec is None or not spec.required:
            continue
        if field.confidence >= minimum:
            continue

        issues.append(ValidationIssue(
            code="low_confidence_required_field",
            severity=Severity.WARNING,
            field=field.name,
            message="%s was read with confidence %.2f, below the %.2f needed "
                    "for a required field (%s)"
                    % (field.name.replace("_", " "), field.confidence, minimum,
                       field.note[:80] or "no note"),
            expected=">= %.2f" % minimum,
            actual="%.2f" % field.confidence,
        ))

    return issues
