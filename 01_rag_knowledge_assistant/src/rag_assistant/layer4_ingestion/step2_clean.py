"""
LAYER 4 - INGESTION, STEP 2: CLEAN
==================================
Raw extracted text is messy: page headers repeat, lines break mid-sentence,
blank lines pile up. Cleaning it before chunking directly improves retrieval,
because the chunks end up containing real sentences.

This step is deliberately conservative. It never deletes content it is unsure
about, because deleted content can never be retrieved.
"""

import re

MULTIPLE_BLANK_LINES = re.compile(r"\n{3,}")
TRAILING_SPACES = re.compile(r"[ \t]+\n")
BULLET_MARKERS = re.compile(r"^\s*[•·●]\s*", re.MULTILINE)


def join_broken_lines(text: str) -> str:
    """
    PDFs break a sentence across lines. If a line does not end with punctuation
    and the next line starts lowercase, they were one sentence: join them.
    """
    lines = text.split("\n")
    joined: list[str] = []

    for line in lines:
        stripped = line.rstrip()

        if len(joined) == 0:
            joined.append(stripped)
            continue

        previous = joined[-1]
        previous_ends_sentence = previous.endswith((".", "!", "?", ":", ";"))
        previous_is_blank = previous.strip() == ""
        current_starts_lowercase = len(stripped) > 0 and stripped[0].islower()

        if not previous_is_blank and not previous_ends_sentence and current_starts_lowercase:
            joined[-1] = previous + " " + stripped.lstrip()
        else:
            joined.append(stripped)

    return "\n".join(joined)


def remove_repeated_lines(text: str, minimum_repeats: int = 4) -> str:
    """
    Drop lines that repeat on nearly every page - running headers and footers.
    Short lines only, so we never remove a real sentence.
    """
    lines = text.split("\n")

    counts: dict[str, int] = {}
    for line in lines:
        key = line.strip()
        if key == "" or len(key) > 70:
            continue
        if key not in counts:
            counts[key] = 0
        counts[key] = counts[key] + 1

    noisy: set[str] = set()
    for key in counts:
        if counts[key] >= minimum_repeats:
            noisy.add(key)

    kept: list[str] = []
    for line in lines:
        if line.strip() in noisy:
            continue
        kept.append(line)

    return "\n".join(kept)


def clean_text(text: str) -> str:
    """Run the whole cleaning sequence."""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = cleaned.replace(" ", " ")          # non-breaking space
    cleaned = BULLET_MARKERS.sub("- ", cleaned)        # normalise bullets
    cleaned = TRAILING_SPACES.sub("\n", cleaned)
    cleaned = remove_repeated_lines(cleaned)
    cleaned = join_broken_lines(cleaned)
    cleaned = MULTIPLE_BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()
