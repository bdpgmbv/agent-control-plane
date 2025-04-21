"""The human review queue, and what a correction actually does."""

from doc_intelligence.layer2_models.schemas import Decision, FieldSource
from doc_intelligence.layer8_review.step2_queue import apply_review_decision


def queue_one(pipeline, name):
    result = pipeline.process_path("samples/" + name)
    pending = pipeline.store.pending_reviews()
    return result, pending[0].review_id


def test_a_failed_document_lands_in_the_queue_with_its_reason(pipeline):
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    item = pipeline.store.get_review(review_id)
    assert item.status == "pending"
    assert len(item.reasons) > 0
    assert "4730.00" in item.reasons[0]


def test_a_correction_re_runs_every_check(pipeline):
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "correct", "tester",
                                    {"subtotal": "4730.00"},
                                    revalidate=pipeline.revalidate)
    assert outcome.ok
    assert "line_items_do_not_sum_to_subtotal (error)" in outcome.fixed_issues


def test_a_part_finished_correction_stays_in_the_queue(pipeline):
    # This was a real bug: the review was marked "corrected" whatever happened,
    # so a document that still had errors left the queue and was never seen
    # again, while its own decision still said it needed review.
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "correct", "tester",
                                    {"subtotal": "4730.00"},
                                    revalidate=pipeline.revalidate)
    assert len(outcome.remaining_issues) > 0
    assert len(pipeline.store.pending_reviews()) == 1
    assert pipeline.store.get_review(review_id).status == "pending"
    assert pipeline.store.get_review(review_id).decided_at == ""


def test_finishing_the_job_clears_it(pipeline):
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    apply_review_decision(pipeline.store, review_id, "correct", "tester",
                          {"subtotal": "4730.00"}, revalidate=pipeline.revalidate)
    review_id = pipeline.store.pending_reviews()[0].review_id
    outcome = apply_review_decision(pipeline.store, review_id, "correct", "tester",
                                    {"total_amount": "5609.70"},
                                    revalidate=pipeline.revalidate)
    assert outcome.result.decision == Decision.AUTO_APPROVE
    assert len(pipeline.store.pending_reviews()) == 0


def test_a_corrected_field_records_who_says_so(pipeline):
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "correct", "tester",
                                    {"subtotal": "4730.00"},
                                    revalidate=pipeline.revalidate)
    field = outcome.result.field_named("subtotal")
    assert field.source == FieldSource.CORRECTED
    assert field.confidence == 1.0
    assert "4630.00" in field.note


def test_a_reviewer_can_approve_a_document_that_still_has_issues(pipeline):
    result, review_id = queue_one(pipeline, "04_invoice_bad_iban.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "approve", "tester",
                                    revalidate=pipeline.revalidate)
    assert outcome.result.decision == Decision.AUTO_APPROVE
    assert "approved by tester" in outcome.result.decision_reasons[0]


def test_a_review_cannot_be_decided_twice(pipeline):
    result, review_id = queue_one(pipeline, "04_invoice_bad_iban.txt")
    apply_review_decision(pipeline.store, review_id, "approve", "first")
    second = apply_review_decision(pipeline.store, review_id, "reject", "second")
    assert not second.ok
    assert "already" in second.message


def test_an_unknown_action_is_refused(pipeline):
    result, review_id = queue_one(pipeline, "04_invoice_bad_iban.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "pay it", "tester")
    assert not outcome.ok


def test_correcting_a_field_that_does_not_exist_is_refused(pipeline):
    result, review_id = queue_one(pipeline, "04_invoice_bad_iban.txt")
    outcome = apply_review_decision(pipeline.store, review_id, "correct", "tester",
                                    {"favourite_colour": "blue"},
                                    revalidate=pipeline.revalidate)
    assert not outcome.ok


def test_every_decision_reaches_the_audit_trail(pipeline):
    result, review_id = queue_one(pipeline, "02_invoice_arithmetic_error.txt")
    apply_review_decision(pipeline.store, review_id, "correct", "tester",
                          {"subtotal": "4730.00"}, revalidate=pipeline.revalidate)
    entries = pipeline.store.audit_for(result.document_id)
    actions = []
    for entry in entries:
        actions.append(entry["action"])
    assert "queued_for_review" in actions
    assert "correct" in actions


def test_revalidation_does_not_make_a_document_a_duplicate_of_itself(pipeline):
    # The document is already in the store. Including it in the "already seen"
    # list would make every correction produce a duplicate error.
    processed = pipeline.process_path("samples/02_invoice_arithmetic_error.txt")
    again = pipeline.revalidate(processed)
    codes = []
    for issue in again.issues:
        codes.append(issue.code)
    assert "identical_file_already_processed" not in codes
    assert "same_reference_already_processed" not in codes
