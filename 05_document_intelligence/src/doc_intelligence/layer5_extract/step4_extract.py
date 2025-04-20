"""
LAYER 5, STEP 4 - THE EXTRACTION DECISION
=========================================
Patterns first, the model for the gaps, and a rule about which gaps are worth
paying for.

The rule: the model is only called when a REQUIRED field is missing.

That sounds stingy until you look at the corpus. Eight of the twelve sample
documents are missing `payment_terms` - because eight of them genuinely do not
state any payment terms. Sending all eight to a model produces eight calls, eight
lots of latency, and eight nulls, because the information is not there to find.
A missing optional field is usually a fact about the document, not a failure of
the extractor.

A missing *required* field is different. An invoice with no total cannot be paid,
so it is worth a call to see whether the patterns simply did not recognise the
wording.

`ask_model_for_optional=True` overrides this, and the evaluation suite uses it to
measure what the model adds when money is no object.
"""

from doc_intelligence.layer0_shared.model_client import ModelUnavailable
from doc_intelligence.layer0_shared.usage import UsageAccumulator
from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    LineItem,
)
from doc_intelligence.layer3_ingest.step1_load import LoadedDocument
from doc_intelligence.layer3_ingest.step2_clean import clean_document_text
from doc_intelligence.layer5_extract.step1_schemas import fields_for
from doc_intelligence.layer5_extract.step2_patterns import extract_with_patterns
from doc_intelligence.layer5_extract.step3_model import (
    extract_missing_fields,
    transcribe_image,
)


class ExtractionOutcome:
    def __init__(self) -> None:
        self.fields: list[ExtractedField] = []
        self.line_items: list[LineItem] = []
        self.notes: list[str] = []
        self.model_used = False
        self.transcribed = False

    def field_named(self, name: str) -> ExtractedField | None:
        for field in self.fields:
            if field.name == name:
                return field
        return None


def prepare_text(document: LoadedDocument, client=None,
                 usage: UsageAccumulator | None = None) -> tuple[str, list[str], bool]:
    """
    Get clean text out of a loaded document, transcribing a scan if necessary.

    Returns (text, notes, transcribed). Notice what happens after this function:
    nothing downstream knows or cares whether the characters came from a text
    file or from a photograph. The vision model's entire contribution is to make
    the rest of the pipeline applicable.
    """
    notes: list[str] = []
    for note in document.notes:
        notes.append(note)

    if not document.needs_vision():
        cleaned = clean_document_text(document.text)
        for note in cleaned.notes:
            notes.append(note)
        for repair in cleaned.repairs:
            notes.append("scanner repair: " + repair)
        return cleaned.text, notes, False

    if client is None:
        notes.append(
            "this is an image and there is no vision model available, so there "
            "is no text to work with. Add OPENAI_API_KEY to .env to read scans."
        )
        return "", notes, False

    try:
        reply = transcribe_image(document.image_base64, document.image_media_type, client)
    except ModelUnavailable as error:
        notes.append("the scan could not be transcribed: " + error.friendly_message())
        return "", notes, False

    if usage is not None:
        usage.record("transcribe", reply.input_tokens, reply.output_tokens)

    cleaned = clean_document_text(reply.text)
    notes.append("transcribed from an image by the vision model, then processed "
                 "exactly like a text document")
    for repair in cleaned.repairs:
        notes.append("scanner repair: " + repair)
    return cleaned.text, notes, True


def missing_specs_for(document_type: DocumentType, fields: list[ExtractedField]):
    """The specs whose fields came back empty, split into required and optional."""
    present_names = []
    for field in fields:
        if field.is_present():
            present_names.append(field.name)

    missing_required = []
    missing_optional = []
    for spec in fields_for(document_type):
        if spec.name in present_names:
            continue
        if spec.required:
            missing_required.append(spec)
        else:
            missing_optional.append(spec)
    return missing_required, missing_optional


def merge_model_fields(fields: list[ExtractedField],
                       model_fields: list[ExtractedField]) -> list[ExtractedField]:
    """
    Fill the empty slots with the model's answers.

    A pattern result is never overwritten. It carries a position in the text and
    a verbatim quotation; the model's answer carries neither unless it passed the
    verbatim check. When both have something to say, the one that can be pointed
    at on the page wins.
    """
    merged: list[ExtractedField] = []
    model_by_name = {}
    for field in model_fields:
        model_by_name[field.name] = field

    for field in fields:
        if field.is_present():
            merged.append(field)
            continue
        if field.name in model_by_name:
            merged.append(model_by_name[field.name])
            continue
        merged.append(field)

    return merged


def extract_fields(text: str, document_type: DocumentType, client=None,
                   usage: UsageAccumulator | None = None,
                   maximum_line_items: int = 200,
                   ask_model_for_optional: bool = False) -> ExtractionOutcome:
    outcome = ExtractionOutcome()

    if document_type == DocumentType.UNKNOWN:
        outcome.notes.append(
            "the document type is unknown, so there is no field schema to "
            "extract against. A person has to say what this document is first."
        )
        return outcome

    fields, line_items, notes = extract_with_patterns(
        text, document_type, maximum_line_items,
    )
    outcome.fields = fields
    outcome.line_items = line_items
    for note in notes:
        outcome.notes.append(note)

    missing_required, missing_optional = missing_specs_for(document_type, fields)

    if client is None:
        if len(missing_required) > 0:
            names = []
            for spec in missing_required:
                names.append(spec.name)
            outcome.notes.append(
                "required field(s) missing and no model available to look again: "
                + ", ".join(names)
            )
        return outcome

    specs_to_ask = list(missing_required)
    if ask_model_for_optional:
        for spec in missing_optional:
            specs_to_ask.append(spec)

    if len(specs_to_ask) == 0:
        if len(missing_optional) > 0:
            names = []
            for spec in missing_optional:
                names.append(spec.name)
            outcome.notes.append(
                "the patterns found every required field, so the model was not "
                "called. Optional field(s) left empty: " + ", ".join(names)
            )
        return outcome

    model_fields, reply, error_message = extract_missing_fields(text, specs_to_ask, client)

    if reply is not None and usage is not None:
        usage.record("extract", reply.input_tokens, reply.output_tokens)

    if error_message != "":
        outcome.notes.append("the model could not fill the gaps: " + error_message)
        return outcome

    outcome.model_used = True
    outcome.fields = merge_model_fields(fields, model_fields)

    for field in model_fields:
        if field.is_present() and not field.found_verbatim:
            outcome.notes.append(
                "the model's value for '%s' is not present in the document text "
                "and cannot be trusted without a person looking" % field.name
            )

    return outcome


def extract_document(document: LoadedDocument, document_type: DocumentType,
                     client=None, usage: UsageAccumulator | None = None,
                     maximum_line_items: int = 200,
                     ask_model_for_optional: bool = False) -> tuple[str, ExtractionOutcome]:
    """Convenience wrapper: text preparation and extraction in one call."""
    text, notes, transcribed = prepare_text(document, client, usage)
    outcome = extract_fields(text, document_type, client, usage,
                             maximum_line_items, ask_model_for_optional)
    outcome.transcribed = transcribed
    combined_notes = notes + outcome.notes
    outcome.notes = combined_notes
    return text, outcome
