"""
LAYER 0 - SHARED: PLAIN TEXT HELPERS
====================================
Small dependency-free text functions used by search, dedup and scoring.
"""

import re

STOP_WORDS = {
    "a", "about", "above", "after", "all", "also", "an", "and", "any", "are", "as",
    "at", "be", "because", "been", "being", "between", "both", "but", "by", "can",
    "could", "did", "do", "does", "during", "each", "for", "from", "further", "had",
    "has", "have", "how", "however", "i", "if", "in", "into", "is", "it", "its",
    "just", "may", "might", "more", "most", "much", "must", "no", "not", "of", "on",
    "only", "or", "other", "our", "out", "over", "own", "per", "same", "should",
    "since", "so", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "to", "under", "until",
    "up", "use", "used", "using", "very", "was", "we", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "would", "you", "your",
}

WORD_PATTERN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")
NUMBER_PATTERN = re.compile(r"-?\d+(?:\.\d+)?")


def to_words(text: str) -> list[str]:
    return WORD_PATTERN.findall(text.lower())


def to_keywords(text: str) -> list[str]:
    keywords: list[str] = []
    for word in to_words(text):
        if word in STOP_WORDS:
            continue
        if len(word) < 2:
            continue
        keywords.append(word)
    return keywords


def stem_word(word: str) -> str:
    """
    Cut common endings so that different forms of a word match.

    THE BUG THIS SHAPE AVOIDS. A naive version strips "es" before "s", so
    "employees" becomes "employe" while "employee" stays "employee" - and the two
    never match, silently. Plurals are handled by ending, and the trailing "e" is
    dropped last so "improve" and "improved" both land on "improv".
    """
    if len(word) <= 3:
        return word

    stem = word

    # --- plurals ---
    if stem.endswith("ies") and len(stem) > 4:
        stem = stem[:-3] + "y"
    elif stem.endswith(("sses", "shes", "ches", "xes", "zes")):
        stem = stem[:-2]
    elif stem.endswith("s") and not stem.endswith(("ss", "us", "is")):
        stem = stem[:-1]

    # --- verb endings ---
    if stem.endswith("ing") and len(stem) > 5:
        stem = stem[:-3]
    elif stem.endswith("ed") and len(stem) > 4:
        stem = stem[:-2]

    # --- a trailing "e" last, so "improve" and "improv" agree ---
    if stem.endswith("e") and len(stem) > 4:
        stem = stem[:-1]

    return stem


def to_stems(text: str) -> list[str]:
    stems: list[str] = []
    for word in to_keywords(text):
        stems.append(stem_word(word))
    return stems


def to_sentences(text: str) -> list[str]:
    cleaned = text.replace("\n", " ").strip()
    if cleaned == "":
        return []

    sentences: list[str] = []
    for piece in SENTENCE_PATTERN.split(cleaned):
        piece = piece.strip()
        if piece != "":
            sentences.append(piece)
    return sentences


def extract_numbers(text: str) -> list[float]:
    """
    Every number in the text.

    Used by the duplicate check. Two sentences can be almost identical in wording
    and still be saying opposite things, and the difference is always a number.
    """
    numbers: list[float] = []
    for raw in NUMBER_PATTERN.findall(text):
        try:
            numbers.append(float(raw))
        except ValueError:
            continue
    return numbers


def shorten(text: str, max_characters: int = 240) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_characters:
        return cleaned

    cut = cleaned[:max_characters]
    last_space = cut.rfind(" ")
    if last_space > 40:
        cut = cut[:last_space]
    return cut + "..."


def years_since(published_date: str, today_year: int) -> float:
    """Rough age in years, from a YYYY-MM-DD string."""
    if len(published_date) < 4:
        return 99.0
    try:
        year = int(published_date[:4])
    except ValueError:
        return 99.0
    return float(today_year - year)
