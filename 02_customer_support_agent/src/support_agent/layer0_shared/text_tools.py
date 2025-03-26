"""
LAYER 0 - SHARED: PLAIN TEXT HELPERS
====================================
Small dependency-free text functions used by the tools and the router.
"""

import re

STOP_WORDS = {
    "a", "about", "am", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "get", "had", "has",
    "have", "he", "her", "him", "his", "how", "i", "if", "in", "into", "is", "it",
    "its", "just", "me", "my", "no", "not", "of", "on", "or", "our", "out", "please",
    "she", "so", "that", "the", "their", "them", "then", "there", "these", "they",
    "this", "to", "up", "was", "we", "were", "what", "when", "where", "which",
    "who", "why", "will", "with", "would", "you", "your",
}

WORD_PATTERN = re.compile(r"[a-z0-9]+")


def to_words(text: str) -> list[str]:
    """Lowercase the text and return its words."""
    return WORD_PATTERN.findall(text.lower())


def to_keywords(text: str) -> list[str]:
    """Words with the very common ones removed."""
    keywords: list[str] = []
    for word in to_words(text):
        if word in STOP_WORDS:
            continue
        if len(word) < 2:
            continue
        keywords.append(word)
    return keywords


def contains_any(text: str, phrases: list[str]) -> list[str]:
    """Which of these phrases appear in the text. Used by the router."""
    lowered = text.lower()

    found: list[str] = []
    for phrase in phrases:
        if phrase in lowered:
            found.append(phrase)
    return found


def shorten(text: str, max_characters: int = 200) -> str:
    """Cut text to a readable length, on a word boundary."""
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_characters:
        return cleaned

    cut = cleaned[:max_characters]
    last_space = cut.rfind(" ")
    if last_space > 30:
        cut = cut[:last_space]
    return cut + "..."
