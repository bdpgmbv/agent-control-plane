"""
LAYER 5, STEP 2 - EXTRACT WITHOUT A MODEL
=========================================
Pull the fields out by reading labels and parsing the table, the way a clerk
does: find the words "Invoice Number", look to the right of them.

This runs before any model is consulted, and on the twelve sample documents it
finds almost everything. What it produces is also better than a model's answer
in one specific way that matters: every value came from a known position in the
text, so `evidence` is a real quotation and `found_verbatim` is true by
construction. A value that can be pointed at can be checked.

Four traps handled here, each of which produced a wrong number first:

  "total" inside "Subtotal"    solved by word-boundary matching, so \\btotal\\b
                               never fires inside "Subtotal"
  "VAT 20%   464.60"           the first number on the line is the rate, not the
                               amount, so money fields take the LAST number
  "VAT Registration: GB123..." nine digits that are not money, excluded by the
                               spec's avoid_lines
  "44 Harbour Road, BS1 5TY"   three numbers in an address, which looked exactly
                               like a line item until rows were required to be
                               inside the table rules AND column-aligned
"""

import re

from doc_intelligence.layer0_shared.money import (
    NUMBER_PATTERN,
    find_currency,
    interpret_number,
    parse_date,
    round_money,
)
from doc_intelligence.layer2_models.schemas import (
    DocumentType,
    ExtractedField,
    FieldSource,
    LineItem,
)
from doc_intelligence.layer5_extract.step1_schemas import (
    KIND_DATE,
    KIND_IDENTIFIER,
    KIND_MONEY,
    FieldSpec,
    fields_for,
    has_line_items,
)

# Date shapes worth looking for inside a line. parse_date needs an exact string,
# so the date has to be located before it can be read.
DATE_SHAPES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b"),
    re.compile(r"\b[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}\b"),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}\b"),
]

# Three or more spaces means a new column on a printed page.
COLUMN_GAP_PATTERN = re.compile(r"\s{3,}")

# Measured by scripts/check_letterhead.py: the first non-empty line is the
# issuing company on 10 of 10 corpus documents. Held below a labelled field
# because the corpus is small, not because the rule has been seen to fail.
LETTERHEAD_CONFIDENCE = 0.85

# A horizontal rule drawn with dashes or equals signs.
RULE_PATTERN = re.compile(r"^[\s\-=_]{10,}$")

# "2 x Flat white      6.40" - the receipt form, where quantity comes first.
RECEIPT_ROW_PATTERN = re.compile(
    r"^\s*(\d+)\s*[xX]\s+(.+?)\s{2,}([\d.,]+)\s*$"
)

# Words that mean a line is a summary, not a line item.
TOTALS_WORDS = (
    "subtotal", "sub total", "net total", "total", "vat", "tax", "discount",
    "shipping", "carriage", "balance", "due", "amount payable",
)

# Table header words, so the header row is never read as a product.
HEADER_WORDS = ("description", "qty", "quantity", "unit price", "amount", "item")


def label_pattern(label: str) -> re.Pattern:
    """
    Build a word-boundary pattern for a label.

    The boundary is only added where the label starts or ends with a word
    character - "invoice #" and "and:" end in punctuation, and demanding \\b
    after them would never match anything.
    """
    escaped = re.escape(label)
    prefix = ""
    suffix = ""
    if label[0].isalnum():
        prefix = r"\b"
    if label[-1].isalnum():
        suffix = r"\b"
    return re.compile(prefix + escaped + suffix, re.IGNORECASE)


def line_is_excluded(line: str, avoid_lines: list[str]) -> bool:
    lowered = line.lower()
    for phrase in avoid_lines:
        if phrase in lowered:
            return True
    return False


# Characters that introduce a value rather than belong to it. The hyphen is
# handled separately below, because it has two jobs.
SEPARATOR_CHARACTERS = ":=.–—"


def text_after_label(line: str, match: re.Match) -> str:
    """
    Everything to the right of the label, with the separator removed.

    The hyphen needs care. In "Total - 500.00" it introduces the value; in
    "TOTAL   -500.00" it is a minus sign and part of it. Stripping it in both
    cases turned a credit note of -2,787.60 into a payable of +2,787.60 - the
    pipeline would have asked someone to pay a refund. The attack script found
    this, and it is the most expensive bug in the project so far.

    The rule: a hyphen is only a separator when whitespace follows it. A minus
    sign is written against its digits.
    """
    value = line[match.end():]
    value = value.lstrip()

    while len(value) > 0:
        if value[0] in SEPARATOR_CHARACTERS:
            value = value[1:].lstrip()
            continue
        if value[0] == "-":
            rest = value[1:]
            if rest == "" or rest[:1].isspace():
                value = rest.lstrip()
                continue
            break   # "-500.00" - the hyphen belongs to the number
        break

    return value


def cut_at_column_gap(value: str) -> str:
    """
    Stop at the next column.

    "Date: 07 March 2025    Time: 09:42" holds two fields on one line. Without
    this, the date field would swallow the time as well.
    """
    parts = COLUMN_GAP_PATTERN.split(value, maxsplit=1)
    return parts[0].strip()


def find_date_in(text: str):
    """Locate a date inside a piece of text and parse it."""
    for shape in DATE_SHAPES:
        match = shape.search(text)
        if match is None:
            continue
        parsed = parse_date(match.group(0).strip())
        if parsed.found():
            return parsed
    return None


def money_from_line(value: str):
    """
    Read the amount from a value that may contain several numbers.

    The last number wins. On "VAT 20%   464.60" the first number is the rate and
    the last is the money, and that is true of nearly every printed total line.
    Returns (amount, confidence, note, raw) or None.
    """
    matches = []
    for match in NUMBER_PATTERN.finditer(value):
        matches.append(match)
    if len(matches) == 0:
        return None

    chosen = matches[-1]
    raw = chosen.group(0)

    negative = raw.startswith("-")
    digits = raw
    if negative:
        digits = digits[1:]

    amount, confidence, note = interpret_number(digits)
    if amount is None:
        return None
    if negative:
        amount = -amount

    return (round_money(amount), confidence, note, raw)


def collect_label_matches(lines: list[str], spec: FieldSpec) -> list[tuple[int, str]]:
    """
    Every place a label for this field appears, as (line index, value text).

    All labels are collected before anything is chosen, because a label can
    match a line that has no usable value on it - "3. CHARGES" matches the
    "charges" label but holds no number. Gathering candidates first lets the
    chooser skip those instead of giving up at the first match.
    """
    candidates: list[tuple[int, str]] = []

    # Labels are the outer loop, lines the inner one. That ordering is the whole
    # point: the spec lists labels most-distinctive-first, and scanning line by
    # line instead threw that ordering away. It cost two wrong fields on the
    # sample contract - "Payment Terms: 30 days from the date of invoice" lost to
    # the word "invoiced" in an earlier sentence, and the governing-law clause
    # lost to its own heading. Whichever label is most specific should win no
    # matter where on the page it appears.
    for label in spec.labels:
        pattern = label_pattern(label)
        for index, line in enumerate(lines):
            if line_is_excluded(line, spec.avoid_lines):
                continue
            match = pattern.search(line)
            if match is None:
                continue
            candidates.append((index, text_after_label(line, match)))

    return candidates


def first_non_empty_line(lines: list[str]) -> tuple[int, str]:
    for index, line in enumerate(lines):
        if line.strip() != "":
            return index, line.strip()
    return -1, ""


def next_non_empty_line(lines: list[str], after_index: int) -> tuple[int, str]:
    index = after_index + 1
    while index < len(lines):
        if lines[index].strip() != "":
            return index, lines[index].strip()
        index = index + 1
    return -1, ""


def tidy_company_name(value: str) -> str:
    """
    Trim a party line down to the company name.

    Contract parties are written as 'Fenwick Analytics Ltd, of 12 Rowan Street,
    Leeds LS2 8JT ("the Client")'. The name is the useful part; the address and
    the defined term are not what the field is for.
    """
    for separator in (", of ", " of ", " (", ",of "):
        position = value.find(separator)
        if position > 0:
            value = value[:position]
            break
    return value.strip().rstrip(",")


def make_missing_field(spec: FieldSpec) -> ExtractedField:
    return ExtractedField(
        name=spec.name,
        value="",
        source=FieldSource.RULES,
        confidence=0.0,
        note="no label for this field was found in the document",
    )


def extract_letterhead_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    """
    The company name printed at the top of the page.

    The confidence here is measured, not guessed. `scripts/check_letterhead.py`
    runs this rule against every document in the corpus and compares it to the
    company name a person read off the page: 10 of 10 correct, including the
    purchase order, where the letterhead is the BUYER and the rule is asked for
    `buyer_name` rather than `supplier_name`.

    It is still set below a labelled field, and the reason is not modesty about
    the hit rate - it is that ten documents I wrote myself is thin evidence. The
    rule has never met a letterhead with a logo above the name, a parent-company
    banner, or a franking mark, and any of those would break it. 0.85 says "this
    works on everything it has been shown, and it has not been shown much".
    """
    index, value = first_non_empty_line(lines)
    if index < 0:
        return make_missing_field(spec)

    return ExtractedField(
        name=spec.name,
        value=tidy_company_name(value),
        raw_value=value,
        source=FieldSource.RULES,
        confidence=LETTERHEAD_CONFIDENCE,
        evidence=value,
        found_verbatim=True,
        note="taken from the letterhead, the first line of the document - "
             "correct on 10 of 10 documents in the corpus, but position is "
             "weaker evidence than a label",
    )


def extract_block_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    """A field whose value sits on the line after its label, such as 'Bill To:'."""
    candidates = collect_label_matches(lines, spec)
    for index, after_label in candidates:
        value = cut_at_column_gap(after_label)
        evidence_line = lines[index].strip()

        if value == "":
            following_index, following = next_non_empty_line(lines, index)
            if following_index < 0:
                continue
            value = cut_at_column_gap(following)
            evidence_line = following

        if value == "":
            continue

        return ExtractedField(
            name=spec.name,
            value=tidy_company_name(value),
            raw_value=value,
            source=FieldSource.RULES,
            confidence=0.88,
            evidence=evidence_line,
            found_verbatim=True,
            note="read from the block after '%s'" % spec.labels[0],
        )

    return make_missing_field(spec)


def extract_date_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    candidates = collect_label_matches(lines, spec)
    found: list[ExtractedField] = []

    for index, after_label in candidates:
        parsed = find_date_in(after_label)
        evidence_line = lines[index].strip()
        note_extra = ""

        if parsed is None:
            # The date may have wrapped onto the next line, which is how
            # "commences on 15 April 2025 and continues until\n 14 April 2026"
            # hides a contract's end date.
            following_index, following = next_non_empty_line(lines, index)
            if following_index >= 0:
                parsed = find_date_in(following)
                if parsed is not None:
                    evidence_line = following
                    note_extra = " (the date continued onto the next line)"

        if parsed is None:
            continue

        found.append(ExtractedField(
            name=spec.name,
            value=parsed.iso(),
            raw_value=parsed.raw,
            source=FieldSource.RULES,
            confidence=round(0.9 * parsed.confidence, 3),
            evidence=evidence_line,
            found_verbatim=parsed.raw in lines[index] or parsed.raw in evidence_line,
            note=parsed.note + note_extra,
        ))

    if len(found) == 0:
        return make_missing_field(spec)
    if spec.prefer == "last":
        return found[-1]
    return found[0]


def extract_money_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    candidates = collect_label_matches(lines, spec)
    found: list[ExtractedField] = []

    for index, after_label in candidates:
        result = money_from_line(after_label)
        if result is None:
            continue
        amount, confidence, note, raw = result

        found.append(ExtractedField(
            name=spec.name,
            value="%.2f" % amount,
            raw_value=raw,
            source=FieldSource.RULES,
            confidence=round(0.92 * confidence, 3),
            evidence=lines[index].strip(),
            found_verbatim=raw in lines[index],
            note=note,
        ))

    if len(found) == 0:
        return make_missing_field(spec)
    if spec.prefer == "last":
        return found[-1]
    return found[0]


def extract_text_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    candidates = collect_label_matches(lines, spec)

    for index, after_label in candidates:
        value = cut_at_column_gap(after_label)

        # Some labels sit at the end of the sentence they describe, such as
        # "...by giving 90 days written notice." In that case the sentence is
        # the value, so fall back to the whole line.
        if len(value) < 3:
            value = lines[index].strip()
        if len(value) < 3:
            continue

        return ExtractedField(
            name=spec.name,
            value=value,
            raw_value=value,
            source=FieldSource.RULES,
            confidence=0.85,
            evidence=lines[index].strip(),
            found_verbatim=value in lines[index],
            note="read from the label '%s'" % spec.labels[0],
        )

    return make_missing_field(spec)


def extract_identifier_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    candidates = collect_label_matches(lines, spec)

    for index, after_label in candidates:
        value = cut_at_column_gap(after_label)
        if value == "":
            continue
        return ExtractedField(
            name=spec.name,
            value=value,
            raw_value=value,
            source=FieldSource.RULES,
            confidence=0.93,
            evidence=lines[index].strip(),
            found_verbatim=value in lines[index],
            note="read from the label '%s'" % spec.labels[0],
        )

    return make_missing_field(spec)


def extract_one_field(lines: list[str], spec: FieldSpec) -> ExtractedField:
    if spec.from_letterhead:
        return extract_letterhead_field(lines, spec)
    if spec.from_block:
        return extract_block_field(lines, spec)
    if spec.kind == KIND_DATE:
        return extract_date_field(lines, spec)
    if spec.kind == KIND_MONEY:
        return extract_money_field(lines, spec)
    if spec.kind == KIND_IDENTIFIER:
        return extract_identifier_field(lines, spec)
    return extract_text_field(lines, spec)


# =============================================================
#  The table of line items
# =============================================================

def find_table_region(lines: list[str]) -> tuple[int, int] | None:
    """
    The span between the first and last horizontal rule.

    Restricting row parsing to this region is what stopped a postal address
    being read as a line item: "44 Harbour Road, Bristol BS1 5TY" contains three
    numbers, which is exactly the shape of a quantity, a unit price and a total.
    """
    rule_indexes = []
    for index, line in enumerate(lines):
        if RULE_PATTERN.match(line) is not None:
            rule_indexes.append(index)

    if len(rule_indexes) < 2:
        return None
    return (rule_indexes[0] + 1, rule_indexes[-1])


def looks_like_summary(description: str) -> bool:
    lowered = description.lower()
    for word in TOTALS_WORDS:
        if re.search(r"\b" + re.escape(word) + r"\b", lowered) is not None:
            return True
    return False


def looks_like_header(description: str) -> bool:
    lowered = description.lower()
    hits = 0
    for word in HEADER_WORDS:
        if word in lowered:
            hits = hits + 1
    return hits >= 2


def parse_columnar_row(line: str) -> LineItem | None:
    """
    A row printed as: description, quantity, unit price, amount.

    The last three numbers on the line are taken, not the first three. A
    description often contains a number of its own - "Sample rack, 50 position"
    or "Brochure printing, A5, 1000 units" - and reading from the left turns
    that number into the quantity.
    """
    matches = []
    for match in NUMBER_PATTERN.finditer(line):
        matches.append(match)
    if len(matches) < 3:
        return None

    quantity_match = matches[-3]
    unit_match = matches[-2]
    total_match = matches[-1]

    description = line[:quantity_match.start()].strip()
    if description == "":
        return None

    # The numbers must be in their own columns. A sentence that happens to hold
    # three numbers separated by single spaces is prose, not a table row.
    gap = line[len(description):quantity_match.start()]
    if len(gap) < 2:
        return None

    quantity, _, _ = interpret_number(quantity_match.group(0))
    unit_price, _, _ = interpret_number(unit_match.group(0))
    line_total, _, _ = interpret_number(total_match.group(0))

    if quantity is None or unit_price is None or line_total is None:
        return None

    return LineItem(
        description=description,
        quantity=quantity,
        unit_price=round_money(unit_price),
        line_total=round_money(line_total),
        raw_line=line.strip(),
    )


def parse_receipt_row(line: str) -> LineItem | None:
    """A till-roll row: "2 x Flat white      6.40"."""
    match = RECEIPT_ROW_PATTERN.match(line)
    if match is None:
        return None

    quantity, _, _ = interpret_number(match.group(1))
    line_total, _, _ = interpret_number(match.group(3))
    if quantity is None or line_total is None or quantity == 0:
        return None

    return LineItem(
        description=match.group(2).strip(),
        quantity=quantity,
        unit_price=round_money(line_total / quantity),
        line_total=round_money(line_total),
        raw_line=line.strip(),
    )


def extract_line_items(lines: list[str], maximum: int = 200) -> tuple[list[LineItem], list[str]]:
    items: list[LineItem] = []
    notes: list[str] = []

    region = find_table_region(lines)
    if region is not None:
        start, end = region
        for index in range(start, end):
            line = lines[index]
            if RULE_PATTERN.match(line) is not None:
                continue
            if line.strip() == "":
                continue

            item = parse_columnar_row(line)
            if item is None:
                item = parse_receipt_row(line)
            if item is None:
                continue
            if looks_like_summary(item.description):
                continue
            if looks_like_header(item.description):
                continue

            items.append(item)
            if len(items) >= maximum:
                notes.append("stopped after %d line items" % maximum)
                break
    else:
        # No printed rules, so this is a till roll. Only the unambiguous
        # "N x item  price" form is accepted here - without a table boundary to
        # lean on, guessing at looser shapes reads addresses as purchases.
        for line in lines:
            item = parse_receipt_row(line)
            if item is None:
                continue
            if looks_like_summary(item.description):
                continue
            items.append(item)
            if len(items) >= maximum:
                notes.append("stopped after %d line items" % maximum)
                break
        if len(items) > 0:
            notes.append("no table rules found, rows read from the 'N x item' form")

    return items, notes


# =============================================================
#  The whole step
# =============================================================

def infer_currency(text: str, fields: list[ExtractedField]) -> ExtractedField:
    """
    Work out the currency, and say which way it was worked out.

    A written code or symbol is direct evidence. Deriving it from the country
    prefix of a VAT number or IBAN is an inference, so it is recorded as
    COMPUTED at lower confidence - a GB prefix strongly suggests sterling, but
    nothing on the page actually said so.
    """
    stated = find_currency(text)
    if stated != "":
        return ExtractedField(
            name="currency", value=stated, raw_value=stated,
            source=FieldSource.RULES, confidence=0.95,
            found_verbatim=True,
            note="the document states the currency",
        )

    country_to_currency = {"GB": "GBP", "DE": "EUR", "FR": "EUR", "NL": "EUR",
                           "IE": "EUR", "ES": "EUR", "IT": "EUR", "US": "USD"}
    for field_name in ("supplier_vat", "iban"):
        for candidate in fields:
            if candidate.name != field_name or not candidate.is_present():
                continue
            prefix = candidate.value.strip().upper()[:2]
            if prefix in country_to_currency:
                return ExtractedField(
                    name="currency",
                    value=country_to_currency[prefix],
                    source=FieldSource.COMPUTED,
                    confidence=0.60,
                    evidence=candidate.value,
                    found_verbatim=False,
                    note="inferred from the '%s' country prefix on %s; the "
                         "document never states a currency" % (prefix, field_name),
                )

    return ExtractedField(
        name="currency", value="", source=FieldSource.COMPUTED, confidence=0.0,
        note="the document does not state a currency and none could be inferred",
    )


def extract_with_patterns(text: str, document_type: DocumentType,
                          maximum_line_items: int = 200):
    """Returns (fields, line_items, notes)."""
    lines = text.split("\n")

    fields: list[ExtractedField] = []
    for spec in fields_for(document_type):
        fields.append(extract_one_field(lines, spec))

    items: list[LineItem] = []
    notes: list[str] = []
    if has_line_items(document_type):
        items, notes = extract_line_items(lines, maximum_line_items)

    fields.append(infer_currency(text, fields))
    return fields, items, notes
