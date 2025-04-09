"""
LAYER 0 - SHARED: PLAIN TEXT HELPERS
====================================
Used by schema retrieval to match a question against table and column names.
"""

import re

STOP_WORDS = {
    "a", "about", "all", "an", "and", "any", "are", "as", "at", "be", "by",
    "can", "did", "do", "does", "each", "for", "from", "get", "give", "had",
    "has", "have", "how", "i", "in", "is", "it", "many", "me", "much", "of",
    "on", "or", "our", "show", "that", "the", "their", "them", "there", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "with",
    "list", "tell", "please", "want", "would",
}

WORD_PATTERN = re.compile(r"[a-z0-9]+")


def to_words(text: str) -> list[str]:
    # Underscores separate words in column names, so they become spaces first.
    return WORD_PATTERN.findall(text.lower().replace("_", " "))


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
    """Cut plurals so 'orders' matches the table 'order_items'."""
    if len(word) <= 3:
        return word

    stem = word
    if stem.endswith("ies") and len(stem) > 4:
        stem = stem[:-3] + "y"
    elif stem.endswith(("sses", "shes", "ches", "xes")):
        stem = stem[:-2]
    elif stem.endswith("s") and not stem.endswith(("ss", "us", "is")):
        stem = stem[:-1]

    return stem


def to_stems(text: str) -> list[str]:
    stems: list[str] = []
    for word in to_keywords(text):
        stems.append(stem_word(word))
    return stems


def shorten(text: str, max_characters: int = 200) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_characters:
        return cleaned
    return cleaned[:max_characters] + "..."
