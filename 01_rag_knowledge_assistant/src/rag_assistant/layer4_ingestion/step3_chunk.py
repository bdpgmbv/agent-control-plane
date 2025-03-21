"""
LAYER 4 - INGESTION, STEP 3: CHUNK
==================================
A chunk is the unit of retrieval. Chunking well is the single highest-value
thing you can do for answer quality, and it is where most tutorials stop caring.

Two ideas, applied in order:

  1. STRUCTURE FIRST.
     Split on headings. A section about refunds and a section about shipping
     should never end up in the same chunk, because a chunk with two topics
     matches neither question well.

  2. SIZE SECOND.
     Any section still too long is cut into overlapping word windows. The
     overlap matters: without it, a sentence that straddles a boundary is lost
     from both chunks.

Each chunk carries the heading it came from. That heading is prepended to the
chunk text, which gives the embedding model context it would otherwise lack
("30 days" means nothing; "Refund Policy: 30 days" means a lot).
"""

import re

MARKDOWN_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)[.)]?\s+([A-Z][^\n]{2,80})$")


class Section:
    """A run of text under one heading."""

    def __init__(self, heading: str, text: str) -> None:
        self.heading = heading
        self.text = text


class TextChunk:
    """A chunk before it becomes a stored Chunk (no ids or vectors yet)."""

    def __init__(self, text: str, heading: str, index: int) -> None:
        self.text = text
        self.heading = heading
        self.index = index


def looks_like_heading(line: str) -> str | None:
    """Return the heading text if this line is a heading, otherwise None."""
    stripped = line.strip()
    if stripped == "":
        return None

    markdown_match = MARKDOWN_HEADING.match(stripped)
    if markdown_match is not None:
        return markdown_match.group(2).strip()

    numbered_match = NUMBERED_HEADING.match(stripped)
    if numbered_match is not None:
        return numbered_match.group(2).strip()

    # ALL CAPS short lines are headings in a lot of policy documents.
    if len(stripped) <= 70 and stripped.upper() == stripped:
        letters = 0
        for character in stripped:
            if character.isalpha():
                letters = letters + 1
        if letters >= 3:
            return stripped.title()

    return None


def split_into_sections(text: str) -> list[Section]:
    """Break a document at its headings."""
    sections: list[Section] = []
    current_heading = ""
    current_lines: list[str] = []

    for line in text.split("\n"):
        heading = looks_like_heading(line)

        if heading is not None:
            # Close the section we were building.
            body = "\n".join(current_lines).strip()
            if body != "":
                sections.append(Section(heading=current_heading, text=body))
            current_heading = heading
            current_lines = []
            continue

        current_lines.append(line)

    body = "\n".join(current_lines).strip()
    if body != "":
        sections.append(Section(heading=current_heading, text=body))

    if len(sections) == 0:
        sections.append(Section(heading="", text=text.strip()))

    return sections


def split_into_word_windows(
    text: str,
    size_words: int,
    overlap_words: int,
    minimum_tail_words: int = 12,
) -> list[str]:
    """
    Cut text into overlapping windows of whole words.

    Example with size 5 and overlap 2:
        words 0-4, then 3-7, then 6-10 ...

    The tail matters. If the final window would be a tiny scrap of text, we
    stretch the previous window to cover the rest of the section instead. The
    alternative - dropping the scrap - silently loses the end of every section,
    and content that was never indexed can never be retrieved.
    """
    words = text.split()
    if len(words) == 0:
        return []

    if len(words) <= size_words:
        return [" ".join(words)]

    step = size_words - overlap_words
    if step < 1:
        step = 1

    windows: list[str] = []
    start = 0
    while start < len(words):
        end = start + size_words

        # Would the leftover after this window be too small to stand alone?
        # If so, absorb it into this window and stop.
        words_left_after = len(words) - end
        if words_left_after > 0 and words_left_after < minimum_tail_words:
            end = len(words)

        window_words = words[start:end]
        if len(window_words) == 0:
            break
        windows.append(" ".join(window_words))

        if end >= len(words):
            break
        start = start + step

    return windows


def chunk_document(
    text: str,
    size_words: int = 180,
    overlap_words: int = 40,
    minimum_words: int = 6,
) -> list[TextChunk]:
    """
    Turn a whole cleaned document into chunks.

    minimum_words drops fragments too small to answer anything - a stray
    "Page 4" line becomes noise in the index if you keep it.
    """
    chunks: list[TextChunk] = []
    index = 0

    for section in split_into_sections(text):
        for window in split_into_word_windows(section.text, size_words, overlap_words):
            if len(window.split()) < minimum_words:
                continue

            # Prepend the heading so the chunk can stand on its own.
            if section.heading != "":
                chunk_text = section.heading + ": " + window
            else:
                chunk_text = window

            chunks.append(TextChunk(text=chunk_text, heading=section.heading, index=index))
            index = index + 1

    return chunks
