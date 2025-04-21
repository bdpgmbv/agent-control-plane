"""
LAYER 8, STEP 2 - THE HUMAN REVIEW QUEUE
========================================
What happens after the pipeline says "I am not sure about this one".

The interesting part is what a correction does. When a reviewer fixes a field,
validation runs again on the corrected values, and the reviewer is told whether
their fix actually resolved the problem.

That matters because a reviewer typing 4730.00 into a subtotal cannot see whether
the invoice now adds up - they would have to add four numbers in their head to
find out. Re-running the checks answers it instantly: "the line items now sum to
the subtotal" or "still out by 100.00, check the third line". The deterministic
validator stops being a gate and becomes an assistant, and it is the same code
either way.

A corrected document is never marked AUTO_APPROVE. A person looked at it, so the
record should say a person approved it. The audit trail is there to answer who
decided, and quietly relabelling human work as automatic would make it lie.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import (
    Decision,
    FieldSource,
    ProcessResult,
    ReviewItem,
    Severity,
    ValidationIssue,
)
from doc_intelligence.layer8_review.step1_store import DocumentStore, now_text

VALID_ACTIONS = ("approve", "correct", "reject")

# Re-run validation, confidence and the gates on corrected values. Injected
# rather than imported so layer 8 does not have to know how the pipeline is
# assembled - and so a test can check the queue logic on its own.
RevalidateFunction = Callable[[ProcessResult], ProcessResult]


@dataclass
class ReviewOutcome:
    ok: bool = False
    message: str = ""
    review: ReviewItem | None = None
    result: ProcessResult | None = None
    fixed_issues: list[str] = field(default_factory=list)
    remaining_issues: list[str] = field(default_factory=list)


def make_review_id() -> str:
    return "rev_" + uuid.uuid4().hex[:12]


def queue_for_review(store: DocumentStore, result: ProcessResult,
                     reasons: list[str]) -> ReviewItem:
    item = ReviewItem(
        review_id=make_review_id(),
        document_id=result.document_id,
        filename=result.filename,
        document_type=result.document_type.value,
        confidence=result.confidence,
        reasons=reasons,
        issue_count=len(result.issues),
    )
    store.add_review(item)
    store.record_audit(result.document_id, "pipeline", "queued_for_review",
                       {"confidence": result.confidence, "reasons": reasons})
    return item


def apply_corrections(result: ProcessResult, corrections: dict) -> list[str]:
    """
    Write a person's values into the result.

    A corrected field gets confidence 1.0 and source CORRECTED. That is not
    flattery towards humans - it records that the value's authority is now a
    person who looked at the paper, not a pattern match. The provenance is the
    point: six months later, "who says the total is 2,787.60?" has an answer.
    """
    applied: list[str] = []

    for name, new_value in corrections.items():
        text_value = str(new_value).strip()
        existing = result.field_named(name)

        if existing is None:
            continue

        old_value = existing.value
        existing.value = text_value
        existing.raw_value = text_value
        existing.source = FieldSource.CORRECTED
        existing.confidence = 1.0
        existing.found_verbatim = True
        existing.note = "corrected by a reviewer (was %r)" % old_value
        applied.append("%s: %r -> %r" % (name, old_value, text_value))

    return applied


def issue_codes(issues: list[ValidationIssue]) -> list[str]:
    codes: list[str] = []
    for issue in issues:
        codes.append("%s (%s)" % (issue.code, issue.severity.value))
    return codes


def apply_review_decision(store: DocumentStore, review_id: str, action: str,
                          actor: str, corrections: dict | None = None,
                          note: str = "",
                          revalidate: RevalidateFunction | None = None) -> ReviewOutcome:
    """
    `revalidate` is a function (ProcessResult) -> ProcessResult that re-runs
    validation, confidence and the decision gates on corrected values. It is
    injected rather than imported so that layer 8 does not have to know how the
    pipeline is assembled - and so a test can check the queue logic on its own.
    """
    if corrections is None:
        corrections = {}

    if action not in VALID_ACTIONS:
        return ReviewOutcome(
            ok=False,
            message="'%s' is not something a reviewer can do. Choose one of: %s"
                    % (action, ", ".join(VALID_ACTIONS)),
        )

    item = store.get_review(review_id)
    if item is None:
        return ReviewOutcome(ok=False, message="no review with id %r" % review_id)

    if item.status != "pending":
        return ReviewOutcome(
            ok=False,
            message="this review was already %s by %s at %s"
                    % (item.status, item.decided_by or "someone", item.decided_at),
        )

    result = store.load_result(item.document_id)
    if result is None:
        return ReviewOutcome(
            ok=False,
            message="the document for this review is no longer in the store",
        )

    outcome = ReviewOutcome(ok=True)

    if action == "reject":
        result.decision = Decision.REJECT
        result.decision_reasons = ["rejected by %s" % actor]
        if note != "":
            result.decision_reasons.append(note)
        item.status = "rejected"
        outcome.message = "rejected"

    elif action == "approve":
        result.decision = Decision.AUTO_APPROVE
        result.decision_reasons = [
            "approved by %s after review, with %d issue(s) still recorded"
            % (actor, len(result.issues))
        ]
        if note != "":
            result.decision_reasons.append(note)
        item.status = "approved"
        outcome.message = "approved by %s" % actor

    else:
        issues_before = issue_codes(result.issues)
        applied = apply_corrections(result, corrections)

        if len(applied) == 0:
            return ReviewOutcome(
                ok=False,
                message="none of the field names sent match this document: %s"
                        % ", ".join(sorted(corrections.keys())),
            )

        if revalidate is not None:
            result = revalidate(result)

        issues_after = issue_codes(result.issues)

        for code in issues_before:
            if code not in issues_after:
                outcome.fixed_issues.append(code)
        for code in issues_after:
            outcome.remaining_issues.append(code)

        # Whatever the gates now say, a person decided this one.
        errors_remaining = 0
        for issue in result.issues:
            if issue.severity == Severity.ERROR:
                errors_remaining = errors_remaining + 1

        if errors_remaining == 0:
            result.decision = Decision.AUTO_APPROVE
            result.decision_reasons = [
                "corrected and approved by %s; every check now passes" % actor
            ]
            outcome.message = ("corrections applied - the document now passes "
                               "every check")
        else:
            result.decision = Decision.NEEDS_REVIEW
            result.decision_reasons = [
                "corrected by %s, but %d error(s) remain" % (actor, errors_remaining)
            ]
            outcome.message = ("corrections applied, but %d error(s) remain - see "
                               "below" % errors_remaining)

        item.corrections = corrections
        item.confidence = result.confidence

        # A part-finished correction STAYS in the queue. The first version marked
        # it "corrected" whatever the outcome, which quietly dropped documents
        # that still had errors: the decision said NEEDS_REVIEW but the queue no
        # longer listed them, so nobody would ever see them again. Fixing one of
        # two errors is progress, not completion.
        if errors_remaining == 0:
            item.status = "corrected"
        else:
            item.status = "pending"
            outcome.message = outcome.message + " - this stays in the queue"

    # Only stamp a decision time on a review that is actually finished. A
    # "decided at" on something still pending would be a lie in the audit trail.
    if item.status != "pending":
        item.decided_at = now_text()
        item.decided_by = actor
    store.update_review(item)

    store.save_result(
        result,
        reference=(result.value_of("invoice_number")
                   or result.value_of("receipt_number")
                   or result.value_of("po_number")),
        supplier=(result.value_of("supplier_name")
                  or result.value_of("merchant_name")),
        document_date=(result.value_of("invoice_date")
                       or result.value_of("receipt_date")
                       or result.value_of("order_date")
                       or result.value_of("agreement_date")),
    )
    store.record_audit(result.document_id, actor, action, {
        "review_id": review_id,
        "corrections": corrections,
        "note": note,
        "decision_after": result.decision.value,
    })

    outcome.review = item
    outcome.result = result
    return outcome
