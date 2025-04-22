"""
LAYER 9 - THE PIPELINE
======================
Every layer in one function, in the order the README describes:

    load -> clean -> classify -> extract -> validate -> score -> decide -> store

Reading `process` top to bottom is the fastest way to understand this project.
Notice how little of it involves a model: one optional call to classify a
document the rules could not place, one optional call to fill a missing required
field, and one call to transcribe a scan. Everything else - the labels, the sums,
the checksums, the dates, the duplicate lookup, the confidence, the decision - is
ordinary code.

That is the point the project exists to make. The model is a component, and
specifically it is the component you reach for when you need to read something
unfamiliar. It is not the system.
"""

import time
from datetime import date
from pathlib import Path

from doc_intelligence.layer0_shared.model_client import build_chat_client
from doc_intelligence.layer0_shared.usage import UsageAccumulator
from doc_intelligence.layer1_config.settings import SETTINGS
from doc_intelligence.layer2_models.schemas import (
    BatchSummary,
    Decision,
    DocumentType,
    ProcessResult,
)
from doc_intelligence.layer3_ingest.step1_load import (
    LoadedDocument,
    load_from_bytes,
    load_from_path,
    load_from_text,
)
from doc_intelligence.layer4_classify.step3_classify import classify_document
from doc_intelligence.layer5_extract.step4_extract import extract_document, extract_fields
from doc_intelligence.layer6_validate.step6_validate import (
    ValidationSettings,
    sort_issues,
    validate_document,
)
from doc_intelligence.layer7_confidence.step1_confidence import score_confidence
from doc_intelligence.layer7_confidence.step2_decide import decide
from doc_intelligence.layer8_review.step1_store import DocumentStore
from doc_intelligence.layer8_review.step2_queue import queue_for_review


def count_scanner_repairs(notes: list[str]) -> int:
    count = 0
    for note in notes:
        if note.startswith("scanner repair:"):
            count = count + 1
    return count


class DocumentPipeline:
    def __init__(self, store: DocumentStore | None = None,
                 client=None, today: date | None = None,
                 offline: bool = False) -> None:
        if store is None:
            store = DocumentStore(SETTINGS.sqlite_path)
        self.store = store

        # `offline=True` is a separate argument rather than `client=None` on
        # purpose. The first version of this constructor treated a client of None
        # as "work it out from .env", which made an explicit
        # DocumentPipeline(client=None) silently build a live client and spend
        # money - including inside what was supposed to be an offline test run.
        # "Not specified" and "definitely none" have to be different things.
        if offline:
            self.client = None
        elif client is not None:
            self.client = client
        elif SETTINGS.is_offline():
            self.client = None
        else:
            self.client = build_chat_client()

        self.today = today

    def is_offline(self) -> bool:
        return self.client is None

    def validation_settings(self) -> ValidationSettings:
        return ValidationSettings(
            arithmetic_tolerance=SETTINGS.arithmetic_tolerance,
            tax_tolerance=SETTINGS.tax_tolerance,
            minimum_required_field_confidence=SETTINGS.min_required_field_confidence,
            future_days=SETTINGS.max_document_future_days,
            maximum_age_days=SETTINGS.max_document_age_days,
            today=self.today,
        )

    def build_structured_output(self, result: ProcessResult) -> dict:
        """
        The clean JSON a downstream system would consume.

        Only fields that were actually found appear here, and each one carries
        its confidence and where it came from. A consumer can decide for itself
        whether a 0.30 value read by a model is good enough for what it wants to
        do - which it cannot do if the pipeline flattens everything to a string.
        """
        fields: dict = {}
        for extracted in result.fields:
            if not extracted.is_present():
                continue
            fields[extracted.name] = {
                "value": extracted.value,
                "confidence": round(extracted.confidence, 3),
                "source": extracted.source.value,
                "verbatim": extracted.found_verbatim,
            }

        items: list[dict] = []
        for item in result.line_items:
            items.append({
                "description": item.description,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
            })

        problems: list[dict] = []
        for issue in result.issues:
            problems.append({
                "code": issue.code,
                "severity": issue.severity.value,
                "field": issue.field,
                "message": issue.message,
                "expected": issue.expected,
                "actual": issue.actual,
            })

        return {
            "document_id": result.document_id,
            "document_type": result.document_type.value,
            "fields": fields,
            "line_items": items,
            "issues": problems,
            "confidence": result.confidence,
            "decision": result.decision.value,
        }

    def process(self, document: LoadedDocument,
                ask_model_for_optional: bool = False) -> ProcessResult:
        started = time.time()
        usage = UsageAccumulator(SETTINGS.usd_per_1k_input, SETTINGS.usd_per_1k_output)

        result = ProcessResult(
            document_id=document.document_id,
            filename=document.filename,
        )

        # ---- 1 & 2. load and clean (transcribing a scan if that is what this is)
        text, outcome = extract_document(
            document, DocumentType.UNKNOWN, self.client, usage,
            SETTINGS.max_line_items, ask_model_for_optional,
        )
        # extract_document was called with UNKNOWN to get the text prepared; the
        # real extraction happens below, once we know what this document is.
        # Splitting it this way keeps the "classify before you extract" order
        # visible instead of hiding it inside a helper.
        result.notes = list(outcome.notes)

        if len(text) > SETTINGS.max_document_characters:
            text = text[:SETTINGS.max_document_characters]
            result.notes.append(
                "the document was longer than %d characters and was truncated"
                % SETTINGS.max_document_characters
            )

        result.document_text = text
        result.characters = len(text)

        has_usable_text = len(text.strip()) > 0

        # ---- 3. classify
        if has_usable_text:
            classification = classify_document(text, self.client, usage)
        else:
            from doc_intelligence.layer4_classify.step3_classify import Classification
            classification = Classification(
                document_type=DocumentType.UNKNOWN, confidence=0.0,
                reason="there is no text to classify", decided_by="neither",
            )

        result.document_type = classification.document_type
        result.type_confidence = classification.confidence
        result.type_reason = classification.reason

        # ---- 4. extract, now that we know which schema applies
        if has_usable_text:
            extraction = extract_fields(
                text, classification.document_type, self.client, usage,
                SETTINGS.max_line_items, ask_model_for_optional,
            )
            result.fields = extraction.fields
            result.line_items = extraction.line_items
            for note in extraction.notes:
                result.notes.append(note)

        repair_count = count_scanner_repairs(result.notes)

        # ---- 5. validate
        result.issues = sort_issues(validate_document(
            document.document_id, result.document_type, text,
            result.fields, result.line_items, self.validation_settings(),
            already_seen=self.store.all_seen(),
            had_scanner_repairs=repair_count > 0,
        ))

        # ---- 6. score
        confidence_report = score_confidence(
            result.document_type, classification.confidence,
            result.fields, result.issues, repair_count,
        )
        result.confidence = confidence_report.confidence
        result.confidence_explanation = confidence_report.explanation

        # ---- 7. decide
        decision_report = decide(
            result.document_type, result.confidence, result.fields, result.issues,
            has_usable_text,
            SETTINGS.auto_approve_confidence,
            SETTINGS.min_required_field_confidence,
            SETTINGS.always_review_above_amount,
        )
        result.decision = decision_report.decision
        result.decision_reasons = decision_report.reasons

        result.model_calls = usage.total_calls()
        result.cost_usd = usage.total_cost_usd()
        result.seconds = round(time.time() - started, 3)
        result.structured = self.build_structured_output(result)

        # ---- 8. store, and queue for a person if needed
        self.store.save_result(
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

        if result.decision == Decision.NEEDS_REVIEW:
            queue_for_review(self.store, result, result.decision_reasons)
        else:
            self.store.record_audit(
                result.document_id, "pipeline", result.decision.value,
                {"confidence": result.confidence,
                 "reasons": result.decision_reasons},
            )

        return result

    # ---------------- entry points ----------------

    def process_text(self, text: str, filename: str = "pasted.txt",
                     ask_model_for_optional: bool = False) -> ProcessResult:
        return self.process(load_from_text(text, filename), ask_model_for_optional)

    def process_path(self, path: str | Path,
                     ask_model_for_optional: bool = False) -> ProcessResult:
        return self.process(load_from_path(path), ask_model_for_optional)

    def process_upload(self, filename: str, payload: bytes,
                       ask_model_for_optional: bool = False) -> ProcessResult:
        return self.process(load_from_bytes(filename, payload), ask_model_for_optional)

    # ---------------- re-running the checks after a correction ----------------

    def revalidate(self, result: ProcessResult) -> ProcessResult:
        """
        Run validation, confidence and the gates again on corrected values.

        The duplicate check is given an empty "already seen" list here. The
        document is already in the store, so including it would make every
        corrected document a duplicate of itself - the same self-match bug that
        made a report in project 03 contradict its own source.
        """
        repair_count = count_scanner_repairs(result.notes)

        result.issues = sort_issues(validate_document(
            result.document_id, result.document_type, result.document_text,
            result.fields, result.line_items, self.validation_settings(),
            already_seen=[],
            had_scanner_repairs=repair_count > 0,
        ))

        confidence_report = score_confidence(
            result.document_type, result.type_confidence,
            result.fields, result.issues, repair_count,
        )
        result.confidence = confidence_report.confidence
        result.confidence_explanation = confidence_report.explanation
        result.structured = self.build_structured_output(result)
        return result

    # ---------------- batch ----------------

    def process_directory(self, directory: str | Path,
                          ask_model_for_optional: bool = False):
        """
        Process every readable file in a folder, in name order.

        Order is not cosmetic here. The duplicate check and the bank-detail check
        both answer "compared to what came before", so the same folder processed
        in a different order gives different answers - and correctly so. The
        second copy of an invoice is the duplicate; the first is just an invoice.

        The sample documents are therefore named 01_, 02_, 03_ and so on, and
        name order is arrival order. Before they were numbered, this ran
        alphabetically, which put the forged bank-details invoice ahead of the
        genuine one and made the pipeline accuse the real invoice of being the
        forgery. Both files were fine; the order was the bug.
        """
        directory = Path(directory)
        results: list[ProcessResult] = []

        paths = sorted(directory.iterdir())
        for path in paths:
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            try:
                results.append(self.process_path(path, ask_model_for_optional))
            except Exception as error:
                # One unreadable file must not abandon the batch. A run of 400
                # invoices that stops on number 7 is worse than one that
                # processes 399 and names the one it could not.
                failed = ProcessResult(document_id="failed_" + path.name,
                                       filename=path.name)
                failed.decision = Decision.REJECT
                failed.decision_reasons = [
                    "this file could not be read: %s" % str(error)[:200]
                ]
                results.append(failed)

        return results, summarise(results)


def summarise(results: list[ProcessResult]) -> BatchSummary:
    summary = BatchSummary(processed=len(results))

    for result in results:
        if result.decision == Decision.AUTO_APPROVE:
            summary.auto_approved = summary.auto_approved + 1
        elif result.decision == Decision.REJECT:
            summary.rejected = summary.rejected + 1
        else:
            summary.needs_review = summary.needs_review + 1

        type_name = result.document_type.value
        if type_name not in summary.by_type:
            summary.by_type[type_name] = 0
        summary.by_type[type_name] = summary.by_type[type_name] + 1

        summary.total_errors = summary.total_errors + len(result.errors())
        summary.total_warnings = summary.total_warnings + len(result.warnings())

    if summary.processed > 0:
        summary.straight_through_rate = round(
            summary.auto_approved / summary.processed, 3
        )

    return summary
