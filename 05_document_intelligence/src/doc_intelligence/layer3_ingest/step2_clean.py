"""
LAYER 3, STEP 2 - CLEAN UP A SCAN
=================================
Scanned documents arrive with characters the scanner guessed wrong. The classic
confusions are letter O for zero, lowercase l for one, capital I for one.

The tempting fix is a global replace of "O" with "0". That would turn TOTAL into
T0TAL and Ordered into 0rdered, and every downstream pattern would stop matching.
The actual bug is narrower than the tempting fix, so the repair must be narrower
too.

The rule used here: split the text into runs of letters and digits, and only
repair a run that

  1. already contains at least one digit, and
  2. has no other letters in it besides the confusable ones.

So "2O25" becomes "2025" and "3l" becomes "31", while "TOTAL" is untouched
because it holds no digit, and "GB55l234567" is untouched because G and B are
not confusable characters - a run that mixes real letters with digits is
probably a reference or a VAT number and guessing inside it does more harm than
good. That one is deliberate: the malformed VAT number survives to layer 6,
fails its format check, and reaches a person. A silent repair would have hidden
it.

Every repair is recorded. Layer 7 lowers confidence when repairs happened,
because text somebody had to fix is text that might still be wrong.
"""

import re
from dataclasses import dataclass, field

# Only these three. Each one is a confusion scanners actually make between a
# letter and a digit of near-identical shape. S/5 and B/8 are left out on
# purpose: they are rarer, and a wrong repair is worse than no repair.
DIGIT_CONFUSIONS = {
    "O": "0",
    "o": "0",
    "l": "1",
    "I": "1",
}

RUN_PATTERN = re.compile(r"[A-Za-z0-9]+")
CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
TRAILING_SPACE_PATTERN = re.compile(r"[ \t]+$", re.MULTILINE)
MANY_BLANK_LINES_PATTERN = re.compile(r"\n{4,}")


@dataclass
class CleanedText:
    text: str
    repairs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def repair_count(self) -> int:
        return len(self.repairs)


def normalise_whitespace(text: str) -> str:
    """
    Tidy the text without destroying its shape.

    Line breaks and runs of spaces are load-bearing in this project: they are
    what separates a description from a quantity from a price in a printed
    table. Collapsing all whitespace would make the table unreadable, so only
    trailing spaces and absurd runs of blank lines go.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = CONTROL_PATTERN.sub("", text)
    text = text.replace(" ", " ")     # non-breaking space, common in PDFs
    text = TRAILING_SPACE_PATTERN.sub("", text)
    text = MANY_BLANK_LINES_PATTERN.sub("\n\n\n", text)
    return text.strip("\n")


def repair_run(run: str) -> str:
    """Repair one run of letters and digits, or return it unchanged."""
    has_digit = False
    for character in run:
        if character.isdigit():
            has_digit = True
            break
    if not has_digit:
        return run

    # Every non-digit in the run must be one of the confusable characters,
        # otherwise this is a reference like "A5" or "GB55l234567" and we keep out.
    for character in run:
        if character.isdigit():
            continue
        if character not in DIGIT_CONFUSIONS:
            return run

    repaired_characters = []
    for character in run:
        if character in DIGIT_CONFUSIONS:
            repaired_characters.append(DIGIT_CONFUSIONS[character])
        else:
            repaired_characters.append(character)
    return "".join(repaired_characters)


def repair_ocr_digits(text: str) -> CleanedText:
    repairs: list[str] = []
    pieces: list[str] = []
    position = 0

    for match in RUN_PATTERN.finditer(text):
        pieces.append(text[position:match.start()])
        run = match.group(0)
        repaired = repair_run(run)
        if repaired != run:
            repairs.append("%s -> %s" % (run, repaired))
        pieces.append(repaired)
        position = match.end()

    pieces.append(text[position:])
    return CleanedText(text="".join(pieces), repairs=repairs)


def clean_document_text(text: str) -> CleanedText:
    """The whole step: tidy the layout, then repair scanner confusions."""
    tidy = normalise_whitespace(text)
    result = repair_ocr_digits(tidy)
    if result.repair_count() > 0:
        result.notes.append(
            "repaired %d scanner confusion(s); treat the numbers as less certain"
            % result.repair_count()
        )
    return result
