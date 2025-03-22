"""
LAYER 6 - GENERATION: GROUNDEDNESS
==================================
"Groundedness" answers a different question from "is the answer correct?":

    Is every sentence of this answer actually supported by the passages we gave it?

An answer can be correct and ungrounded (the model knew it already), and that is
still a bug: it means the system is not really using your documents, so tomorrow
it will make something up with the same confidence.

This check runs on EVERY request, so it has to be cheap. It is therefore lexical:
for each sentence of the answer, find the best-matching cited passage and measure
the overlap. No extra model call, no extra cost, no extra latency worth noticing.

Layer 8 has the expensive version - a model judging groundedness - which we use
offline over a fixed test set to confirm this cheap one agrees with it.
"""

import re

from rag_assistant.layer0_shared.text_tools import to_sentences, to_stems
from rag_assistant.layer2_models.schemas import ScoredChunk

CITATION_MARKER = re.compile(r"\[(\d+)\]")

# A sentence needs this much of its vocabulary present in a passage to count as
# supported. Below it, the sentence contains words that came from nowhere.
SENTENCE_SUPPORT_THRESHOLD = 0.55


def strip_citations(text: str) -> str:
    """Remove the [1] [2] markers so they do not affect word matching."""
    return CITATION_MARKER.sub(" ", text)


def find_citation_markers(text: str) -> list[int]:
    """Every passage number mentioned in the answer, in order, without repeats."""
    markers: list[int] = []
    for found in CITATION_MARKER.findall(text):
        number = int(found)
        if number not in markers:
            markers.append(number)
    return markers


def sentence_support(sentence: str, passage_text: str) -> float:
    """
    What share of the sentence's meaningful words appear in the passage.

    Note the direction: we ask how much of the SENTENCE is covered by the
    passage, not the reverse. A long passage should not be rewarded for
    containing a lot of other text.
    """
    sentence_stems = to_stems(strip_citations(sentence))
    if len(sentence_stems) == 0:
        return 1.0   # nothing claimed, nothing to support

    passage_stems = set(to_stems(passage_text))

    matched = 0
    checked: set[str] = set()
    for stem in sentence_stems:
        if stem in checked:
            continue
        checked.add(stem)
        if stem in passage_stems:
            matched = matched + 1

    if len(checked) == 0:
        return 1.0
    return matched / len(checked)


def measure_groundedness(answer_text: str, passages: list[ScoredChunk]) -> tuple[float, list[str]]:
    """
    Returns (score from 0.0 to 1.0, list of unsupported sentences).

    The score is the share of the answer's sentences that are supported by at
    least one retrieved passage.
    """
    sentences = to_sentences(answer_text)
    if len(sentences) == 0 or len(passages) == 0:
        return (0.0, [])

    supported_count = 0
    unsupported: list[str] = []

    for sentence in sentences:
        best_support = 0.0
        for passage in passages:
            support = sentence_support(sentence, passage.chunk.text)
            if support > best_support:
                best_support = support

        if best_support >= SENTENCE_SUPPORT_THRESHOLD:
            supported_count = supported_count + 1
        else:
            unsupported.append(sentence)

    return (round(supported_count / len(sentences), 4), unsupported)
