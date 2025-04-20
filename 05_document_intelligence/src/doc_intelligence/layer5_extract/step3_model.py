"""
LAYER 5, STEP 3 - ASK THE MODEL FOR WHAT IS LEFT
================================================
Two jobs, and a guard that applies to both.

Job one is transcription. A photograph or a scanned PDF has no characters in it,
only pixels, and no amount of clever code can parse what is not there. The
vision model turns the pixels into text - and then hands over. Every label
pattern, every arithmetic check, every checksum in this project runs on that
transcription exactly as it would on a text file. The model's job ends the
moment characters exist.

Job two is the handful of fields the patterns could not find, usually because a
document used wording the label list does not know. That is a genuine strength of
a model: unfamiliar phrasing.

The guard is this: every value the model returns is searched for in the document.

    found_verbatim = raw_value in document_text

If the model reports a total of 2,787.60 and that string is nowhere in the
document, then whatever it did, it did not read it off the page. The value is
kept - it may still be right, and a human may want to see it - but its confidence
drops to UNVERIFIED_CONFIDENCE, which is below every approval gate in layer 7.
The pipeline cannot stop a model from inventing a number. It can refuse to pay
one.
"""

import json
import re

from doc_intelligence.layer0_shared.model_client import ModelReply, ModelUnavailable
from doc_intelligence.layer0_shared.money import parse_date, parse_money
from doc_intelligence.layer2_models.schemas import ExtractedField, FieldSource
from doc_intelligence.layer5_extract.step1_schemas import (
    KIND_DATE,
    KIND_MONEY,
    FieldSpec,
)

# A value that was read off the page, quoted back exactly.
VERIFIED_CONFIDENCE = 0.80

# A value that does not appear in the document. Deliberately below every gate in
# layer 7, so an unverifiable number can never be paid automatically.
UNVERIFIED_CONFIDENCE = 0.30

TRANSCRIBE_SYSTEM = """You transcribe scanned business documents into plain text.

Reproduce exactly what is printed, in reading order, preserving line breaks and
the column layout of any table. Keep the original spacing between columns so the
table stays readable.

Do not correct spelling. Do not tidy the numbers. Do not convert currencies or
reformat dates. Do not add anything that is not printed on the page, and do not
explain or summarise. If part of the page is illegible, write [illegible] in its
place rather than guessing.

Output the transcription and nothing else."""

TRANSCRIBE_USER = "Transcribe this document exactly as printed."

EXTRACT_SYSTEM = """You read business documents and return JSON.

Rules you must follow:

1. Copy values exactly as they are printed on the document. Do not reformat
   numbers, do not convert dates, do not expand abbreviations.
2. If a field is not on the document, use null. Never guess, never infer from
   what would be typical, and never calculate a value that is not printed.
3. Return only a JSON object. No explanation, no markdown fence.

Every value you return is checked against the text of the document. A value that
does not appear there is flagged as unverified, so a guess is worse than a null."""

EXTRACT_USER = """Read the following document and return JSON with exactly these keys:

%s

--- DOCUMENT START ---
%s
--- DOCUMENT END ---

JSON:"""


def transcribe_image(image_base64: str, media_type: str, client) -> ModelReply:
    """Turn a scanned page into characters. Raises ModelUnavailable on failure."""
    return client.read_image(
        TRANSCRIBE_SYSTEM, TRANSCRIBE_USER, image_base64, media_type,
        max_tokens=3000,
    )


def build_field_list(specs: list[FieldSpec]) -> str:
    lines = []
    for spec in specs:
        lines.append('  "%s": %s (%s)' % (spec.name, spec.description, spec.kind))
    return "\n".join(lines)


def strip_json_fence(text: str) -> str:
    """
    Models wrap JSON in ```json fences even when told not to.

    Telling it not to is a request. Removing the fence is enforcement, and only
    one of those is reliable.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline >= 0:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def parse_json_object(text: str) -> dict:
    cleaned = strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the first {...} span, which survives a stray sentence
        # before or after the object.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match is None:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}

    if not isinstance(parsed, dict):
        return {}
    return parsed


def appears_in_document(raw_value: str, document_text: str) -> bool:
    """
    Did this string actually come off the page?

    Whitespace is normalised on both sides before comparing, because a value
    read out of a table may have been printed with different spacing. Nothing
    else is relaxed - the digits and letters must match.
    """
    if raw_value.strip() == "":
        return False
    tidy_value = re.sub(r"\s+", " ", raw_value.strip())
    tidy_document = re.sub(r"\s+", " ", document_text)
    if tidy_value in tidy_document:
        return True
    # Try again without the thousands separators, so "2787.60" still matches a
    # document that printed "2,787.60".
    bare_value = tidy_value.replace(",", "").replace(" ", "")
    bare_document = tidy_document.replace(",", "").replace(" ", "")
    if len(bare_value) >= 3 and bare_value in bare_document:
        return True
    return False


def normalise_value(spec: FieldSpec, raw_value: str) -> tuple[str, str]:
    """
    Turn what the model wrote into the pipeline's own format.

    Returns (normalised value, note). Dates become ISO and money becomes a plain
    two-decimal number, so that every later comparison is between like and like -
    a validator should never be the place where "14 March 2025" meets "2025-03-14".
    """
    if spec.kind == KIND_DATE:
        parsed_date = parse_date(raw_value.strip())
        if parsed_date.found():
            return parsed_date.iso(), parsed_date.note
        return "", "the model returned '%s', which is not a date we can read" % raw_value[:30]

    if spec.kind == KIND_MONEY:
        parsed_money = parse_money(raw_value)
        if parsed_money.found() and parsed_money.amount is not None:
            return "%.2f" % parsed_money.amount, parsed_money.note
        return "", "the model returned '%s', which is not an amount" % raw_value[:30]

    return raw_value.strip(), ""


def extract_missing_fields(document_text: str, missing_specs: list[FieldSpec],
                           client) -> tuple[list[ExtractedField], ModelReply | None, str]:
    """
    Ask the model only for the fields the patterns did not find.

    Returns (fields, usage, error message). The error message is empty on
    success; on failure the caller keeps what the patterns found and records why
    the model could not help, rather than failing the whole document.
    """
    if len(missing_specs) == 0:
        return [], None, ""

    prompt = EXTRACT_USER % (build_field_list(missing_specs), document_text)

    try:
        reply = client.complete(EXTRACT_SYSTEM, prompt, max_tokens=900)
    except ModelUnavailable as error:
        return [], None, error.friendly_message()

    answers = parse_json_object(reply.text)
    if len(answers) == 0:
        return [], reply, "the model's reply was not JSON we could read"

    fields: list[ExtractedField] = []
    for spec in missing_specs:
        if spec.name not in answers:
            continue
        raw = answers[spec.name]
        if raw is None:
            continue
        raw_value = str(raw).strip()
        if raw_value == "" or raw_value.lower() in ("null", "none", "n/a", "-"):
            continue

        verbatim = appears_in_document(raw_value, document_text)
        value, note = normalise_value(spec, raw_value)

        if value == "":
            # It answered, but not with something this field can hold.
            fields.append(ExtractedField(
                name=spec.name, value="", raw_value=raw_value,
                source=FieldSource.MODEL, confidence=0.0,
                found_verbatim=verbatim, note=note,
            ))
            continue

        if verbatim:
            confidence = VERIFIED_CONFIDENCE
            final_note = "read by the model and found verbatim in the document"
            if note != "":
                final_note = final_note + "; " + note
        else:
            confidence = UNVERIFIED_CONFIDENCE
            final_note = ("UNVERIFIED: the model returned '%s' but that value "
                          "does not appear anywhere in the document, so it was "
                          "not read off the page" % raw_value[:40])

        fields.append(ExtractedField(
            name=spec.name, value=value, raw_value=raw_value,
            source=FieldSource.MODEL, confidence=confidence,
            evidence=raw_value, found_verbatim=verbatim, note=final_note,
        ))

    return fields, reply, ""
