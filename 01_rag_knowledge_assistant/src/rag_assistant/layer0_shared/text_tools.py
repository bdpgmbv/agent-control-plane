"""
LAYER 0 - SHARED: PLAIN TEXT HELPERS
====================================
Small, boring, dependency-free text functions used by several layers.
Keeping them here means only one copy exists.
"""

import re

# Words too common to help retrieval.
STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "did", "do",
    "does", "for", "from", "had", "has", "have", "how", "i", "if", "in", "into",
    "is", "it", "its", "my", "no", "not", "of", "on", "or", "our", "so", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "to", "was",
    "we", "were", "what", "when", "where", "which", "who", "why", "will", "with",
    "you", "your",
    # Function words that appear in almost every passage. Leaving them in makes
    # an irrelevant passage look relevant, which is worse than missing a match.
    "about", "above", "after", "again", "all", "along", "already", "also", "am",
    "among", "another", "any", "anything", "around", "because", "before", "being",
    "below", "between", "both", "come", "could", "done", "down", "during", "each",
    "either", "else", "even", "ever", "every", "get", "gets", "getting", "give",
    "go", "just", "know", "like", "long", "make", "many", "may", "me", "might",
    "more", "most", "much", "must", "need", "now", "off", "once", "one", "only",
    "other", "out", "over", "own", "please", "put", "same", "say", "see", "she",
    "should", "since", "some", "someone", "something", "still", "such", "take",
    "tell", "than", "thing", "things", "through", "thus", "together", "too",
    "under", "until", "up", "upon", "us", "use", "used", "using", "very", "want",
    "way", "well", "whether", "while", "whose", "without", "would",
}

WORD_PATTERN = re.compile(r"[a-z0-9]+")
SENTENCE_PATTERN = re.compile(r"(?<=[.!?])\s+")


def to_words(text: str) -> list[str]:
    """Lowercase the text and return its words. Punctuation is dropped."""
    return WORD_PATTERN.findall(text.lower())


def to_keywords(text: str) -> list[str]:
    """
    Words with the unhelpful ones removed. Used everywhere retrieval happens.

    Two kinds are removed:
      * ordinary stop words - they appear in every passage, so they cannot
        distinguish one passage from another.
      * question-framing words ("ask", "happens", "quickly") - see
        layer0_shared/vocabulary.py for why these are actively harmful.
    """
    from rag_assistant.layer0_shared.vocabulary import QUESTION_FRAMING_WORDS

    keywords: list[str] = []
    for word in to_words(text):
        if word in STOP_WORDS:
            continue
        if word in QUESTION_FRAMING_WORDS:
            continue
        if len(word) < 2:
            continue
        keywords.append(word)
    return keywords


def to_sentences(text: str) -> list[str]:
    """Split text into sentences. Good enough for prose documents."""
    cleaned = text.replace("\n", " ").strip()
    if cleaned == "":
        return []

    sentences: list[str] = []
    for piece in SENTENCE_PATTERN.split(cleaned):
        piece = piece.strip()
        if piece != "":
            sentences.append(piece)
    return sentences


def collapse_whitespace(text: str) -> str:
    """Turn any run of spaces, tabs or newlines into a single space."""
    return re.sub(r"\s+", " ", text).strip()


def stem_word(word: str) -> str:
    """
    Cut common English endings so that "refunds", "refunded" and "refunding"
    all become "refund". This is a deliberately simple stemmer: it is easy to
    read, fast, and good enough to make keyword search match real questions.
    """
    if len(word) <= 4:
        return word

    for ending in ("ing", "ies", "ied", "es", "ed", "s"):
        if word.endswith(ending):
            trimmed = word[: len(word) - len(ending)]
            if len(trimmed) >= 3:
                if ending == "ies":
                    return trimmed + "y"
                return trimmed
    return word


def to_stems(text: str) -> list[str]:
    """Meaningful words, stemmed. This is the vocabulary retrieval works on."""
    stems: list[str] = []
    for word in to_keywords(text):
        stems.append(stem_word(word))
    return stems


def word_overlap_score(question: str, passage: str) -> float:
    """
    How much of the question's meaningful vocabulary appears in the passage.
    Returns a number from 0.0 to 1.0.

    This is a simple but surprisingly effective relevance signal, and it needs
    no model, so it also works with no API key.
    """
    question_stems = to_stems(question)
    if len(question_stems) == 0:
        return 0.0

    passage_stems = set(to_stems(passage))

    matched = 0
    seen: set[str] = set()
    for stem in question_stems:
        if stem in seen:
            continue
        seen.add(stem)
        if stem in passage_stems:
            matched = matched + 1

    if len(seen) == 0:
        return 0.0
    return matched / len(seen)


def shorten(text: str, max_characters: int = 280) -> str:
    """Cut text to a readable length for display, on a word boundary."""
    cleaned = collapse_whitespace(text)
    if len(cleaned) <= max_characters:
        return cleaned

    cut = cleaned[:max_characters]
    last_space = cut.rfind(" ")
    if last_space > 40:
        cut = cut[:last_space]
    return cut + "..."
