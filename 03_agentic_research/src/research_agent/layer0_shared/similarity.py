"""
LAYER 0 - SHARED: ARE THESE TWO CLAIMS THE SAME?
================================================
Used to collapse duplicate evidence, and - more interestingly - to find evidence
that CONFLICTS.

------------------------------------------------------------------------------
THE MISTAKE THIS FILE EXISTS TO AVOID
------------------------------------------------------------------------------
Consider two findings:

    "The trial found productivity rose by 12 percent."
    "The trial found productivity rose by 3 percent."

Measured by wording they are 90% identical, so any ordinary duplicate detector
merges them and keeps one. You have just deleted a disagreement between two
studies and replaced it with whichever arrived first.

That is the single worst thing a research system can do, because the output looks
*more* confident than the evidence justifies. The reader has no way to tell.

So the check is in two parts:

    similar wording AND compatible numbers  -> duplicate, merge them
    similar wording AND different numbers   -> CONFLICT, keep both and say so

A claim with no numbers in it is compared on wording alone.
"""

from research_agent.layer0_shared.text_tools import extract_numbers, to_stems

# Two numbers within this fraction of each other are treated as the same figure,
# so "about 12%" and "12.4%" do not become a fake disagreement.
NUMBER_TOLERANCE = 0.12

# THRESHOLDS DEPEND ON HOW SIMILARITY IS MEASURED, so each way of measuring
# brings its own pair. Reusing one number for both is how you get a detector that
# works in tests and merges everything in production.
#
#   lexical   compares words. Paraphrases score low, so the bar is low.
#             "rose by 12 percent" and "increased 12.4 percent" only share three
#             words and score about 0.51 - a real finding it will miss.
#   embedding compares meaning. Paraphrases score 0.85 or higher, so the bar is
#             high and the misses above disappear.
LEXICAL_DUPLICATE_THRESHOLD = 0.60
LEXICAL_CONFLICT_THRESHOLD = 0.30
EMBEDDING_DUPLICATE_THRESHOLD = 0.82
EMBEDDING_CONFLICT_THRESHOLD = 0.62

# SUB-QUESTIONS NEED THEIR OWN, MUCH HIGHER, BAR.
#
# Every sub-question of one research question is about the same topic, so they
# are all similar to each other by construction. Measured by meaning:
#
#     "what do studies show about four-day week productivity?"
#     "what are the costs of moving to a four-day week?"        ~0.85
#     "what does research show about four-day week productivity?" ~0.96
#
# Only the second pair is a duplicate. Reusing the evidence threshold of 0.82
# here merged the entire plan into a single sub-question - the decomposition ran,
# reported success, and produced one sub-question identical to the original.
#
# Comparing words the numbers are lower and the bar can be lower with them.
LEXICAL_SUBQUESTION_DUPLICATE_THRESHOLD = 0.72
EMBEDDING_SUBQUESTION_DUPLICATE_THRESHOLD = 0.93


def jaccard_similarity(first_terms: list[str], second_terms: list[str]) -> float:
    """Shared words divided by total distinct words. 0.0 to 1.0."""
    first_set = set(first_terms)
    second_set = set(second_terms)

    if len(first_set) == 0 and len(second_set) == 0:
        return 1.0
    if len(first_set) == 0 or len(second_set) == 0:
        return 0.0

    shared = 0
    for term in first_set:
        if term in second_set:
            shared = shared + 1

    total = len(first_set) + len(second_set) - shared
    if total == 0:
        return 0.0
    return shared / total


def containment_similarity(first_terms: list[str], second_terms: list[str]) -> float:
    """
    How much of the SHORTER claim appears in the longer one.

    Jaccard punishes a short claim quoted inside a longer one, even though one
    genuinely contains the other. This catches that case.
    """
    first_set = set(first_terms)
    second_set = set(second_terms)

    if len(first_set) == 0 or len(second_set) == 0:
        return 0.0

    shared = 0
    for term in first_set:
        if term in second_set:
            shared = shared + 1

    smaller = min(len(first_set), len(second_set))
    return shared / smaller


def wording_similarity(first_text: str, second_text: str) -> float:
    """How alike two claims are, ignoring numbers entirely."""
    first_terms = to_stems(first_text)
    second_terms = to_stems(second_text)

    by_jaccard = jaccard_similarity(first_terms, second_terms)
    by_containment = containment_similarity(first_terms, second_terms)

    # Containment is the more forgiving measure, so it is weighted lower.
    return max(by_jaccard, 0.85 * by_containment)


def numbers_agree(first_text: str, second_text: str) -> bool:
    """
    Do the numbers in these two claims tell the same story?

    True when neither has numbers, or when every number in the smaller set has a
    close match in the other. False when both quote figures and they differ.
    """
    first_numbers = extract_numbers(first_text)
    second_numbers = extract_numbers(second_text)

    if len(first_numbers) == 0 or len(second_numbers) == 0:
        # Nothing to disagree about.
        return True

    for value in first_numbers:
        matched = False
        for other in second_numbers:
            if values_are_close(value, other):
                matched = True
                break
        if not matched:
            return False

    return True


def values_are_close(first: float, second: float) -> bool:
    """Within NUMBER_TOLERANCE of each other, proportionally."""
    if first == second:
        return True

    largest = max(abs(first), abs(second))
    if largest == 0:
        return True

    difference = abs(first - second)
    return (difference / largest) <= NUMBER_TOLERANCE


class ClaimComparison:
    """The verdict on one pair of claims."""

    def __init__(
        self,
        similarity: float,
        numbers_match: bool,
        duplicate_threshold: float,
        conflict_threshold: float,
        measured_by: str,
        shared_terms: int = 0,
        shared_subject_terms: int = 0,
    ) -> None:
        self.similarity = round(similarity, 4)
        self.numbers_match = numbers_match
        self.duplicate_threshold = duplicate_threshold
        self.conflict_threshold = conflict_threshold
        self.measured_by = measured_by
        self.shared_terms = shared_terms
        self.shared_subject_terms = shared_subject_terms

    def is_duplicate(self) -> bool:
        """Saying the same thing, with no contradicting figures."""
        return self.similarity >= self.duplicate_threshold and self.numbers_match

    def is_conflict(self) -> bool:
        """
        About the same thing, but the figures disagree.

        The bar is lower than for a duplicate on purpose. Two studies of the same
        question rarely word their findings alike, and a disagreement is worth
        showing even when we are less sure the two are strictly comparable.
        Missing a real conflict is worse than showing one the reader dismisses in
        two seconds.

        WHEN COMPARING WORDS, A LOW BAR NEEDS A SECOND CONDITION.
        Two genuinely conflicting study findings score only about 0.32 by wording
        - "productivity rose by 12 percent relative to the baseline" and
        "productivity fell by 3 percent relative to matched controls" share just
        three words. Dropping the bar far enough to catch that also catches any
        two sentences that happen to contain different numbers.

        So in word mode the pair must also share at least two content words, which
        is what "about the same thing" means when you cannot measure meaning. In
        meaning mode the similarity score already carries that, and the extra
        condition would block conflicts that are worded completely differently.
        """
        if self.similarity < self.conflict_threshold:
            return False
        if self.numbers_match:
            return False

        # BOTH MODES: the two claims must be about the same thing. Without this,
        # any two findings shaped like "X fell by N percent" look like a
        # disagreement, whatever X is. See count_shared_subject_terms.
        if self.shared_subject_terms < 1:
            return False

        if self.measured_by.startswith("words"):
            return self.shared_terms >= 2

        return True


class ClaimSimilarityEngine:
    """
    Compares claims, using embeddings when they are available and words when
    they are not.

    prepare() embeds every claim in ONE batch before any comparison happens.
    Comparing n claims pairwise is n-squared comparisons; embedding them one pair
    at a time would be n-squared API calls for n pieces of information.
    """

    def __init__(self, embedder=None) -> None:
        self.embedder = embedder
        self.vector_by_claim: dict[str, list[float]] = {}

        if embedder is None:
            self.duplicate_threshold = LEXICAL_DUPLICATE_THRESHOLD
            self.conflict_threshold = LEXICAL_CONFLICT_THRESHOLD
            self.subquestion_duplicate_threshold = LEXICAL_SUBQUESTION_DUPLICATE_THRESHOLD
            self.measured_by = "words"
        else:
            self.duplicate_threshold = EMBEDDING_DUPLICATE_THRESHOLD
            self.conflict_threshold = EMBEDDING_CONFLICT_THRESHOLD
            self.subquestion_duplicate_threshold = EMBEDDING_SUBQUESTION_DUPLICATE_THRESHOLD
            self.measured_by = "meaning"

    def prepare(self, claims: list[str]) -> int:
        """Embed every claim in one call. Returns the tokens it cost."""
        if self.embedder is None:
            return 0

        wanted: list[str] = []
        for claim in claims:
            if claim not in self.vector_by_claim and claim not in wanted:
                wanted.append(claim)

        if len(wanted) == 0:
            return 0

        try:
            result = self.embedder.embed(wanted)
        except Exception:
            # Falling back to words is worse but still works. Failing the whole
            # research run because a similarity check could not be made is not
            # a trade anyone would choose.
            self.embedder = None
            self.duplicate_threshold = LEXICAL_DUPLICATE_THRESHOLD
            self.conflict_threshold = LEXICAL_CONFLICT_THRESHOLD
            self.subquestion_duplicate_threshold = LEXICAL_SUBQUESTION_DUPLICATE_THRESHOLD
            self.measured_by = "words (embedding failed)"
            return 0

        position = 0
        while position < len(wanted):
            self.vector_by_claim[wanted[position]] = result.vectors[position]
            position = position + 1

        return result.tokens

    def similarity(self, first_text: str, second_text: str) -> float:
        first_vector = self.vector_by_claim.get(first_text)
        second_vector = self.vector_by_claim.get(second_text)

        if first_vector is not None and second_vector is not None:
            return cosine_similarity(first_vector, second_vector)

        return wording_similarity(first_text, second_text)

    def compare(self, first_text: str, second_text: str) -> ClaimComparison:
        return ClaimComparison(
            similarity=self.similarity(first_text, second_text),
            numbers_match=numbers_agree(first_text, second_text),
            duplicate_threshold=self.duplicate_threshold,
            conflict_threshold=self.conflict_threshold,
            measured_by=self.measured_by,
            shared_terms=count_shared_terms(first_text, second_text),
            shared_subject_terms=count_shared_subject_terms(first_text, second_text),
        )


# Words that describe MEASURING rather than what was measured.
#
# Every research finding contains several of these, so two findings will always
# appear to share vocabulary even when they are about completely different
# things. They are excluded when deciding whether two claims are about the same
# subject.
MEASUREMENT_VOCABULARY = {
    "percent", "percentag", "point", "rose", "fell", "increas", "decreas",
    "declin", "improv", "reduc", "chang", "relativ", "compar", "baselin",
    "control", "match", "measur", "report", "found", "show", "studi", "study",
    "trial", "pilot", "evaluat", "result", "score", "rate", "averag", "total",
    "per", "than", "less", "more", "about", "approximately", "standard",
    "deviat", "significant", "substantially", "essentially", "unchang",
    "year", "month", "week", "day", "hour", "period", "time",
}


def count_shared_terms(first_text: str, second_text: str) -> int:
    """How many meaningful words the two claims have in common."""
    first_set = set(to_stems(first_text))
    second_set = set(to_stems(second_text))

    shared = 0
    for term in first_set:
        if term in second_set:
            shared = shared + 1
    return shared


def count_shared_subject_terms(first_text: str, second_text: str) -> int:
    """
    How many words the two claims share that name WHAT was measured.

    THE FALSE CONFLICT THIS PREVENTS:

        "Employee burnout scores fell by 71 percent of a standard deviation."
        "Staff turnover fell by 57 percent."

    Same shape, same measuring words, different numbers - so the conflict test
    flagged them as a disagreement. They are not: they are two different things
    that both went down. A study reporting several results would otherwise be
    shown as contradicting itself, which is the opposite of useful.

    Stripping the measuring vocabulary leaves what the claim is ABOUT.
    Burnout and turnover share nothing. Two claims about productivity share
    "productivity", which is exactly the signal wanted.
    """
    first_subjects: set[str] = set()
    for term in to_stems(first_text):
        if term in MEASUREMENT_VOCABULARY:
            continue
        if term.isdigit():
            continue
        first_subjects.add(term)

    second_subjects: set[str] = set()
    for term in to_stems(second_text):
        if term in MEASUREMENT_VOCABULARY:
            continue
        if term.isdigit():
            continue
        second_subjects.add(term)

    shared = 0
    for term in first_subjects:
        if term in second_subjects:
            shared = shared + 1
    return shared


def cosine_similarity(first: list[float], second: list[float]) -> float:
    """Dot product of two unit-length vectors."""
    if len(first) != len(second):
        return 0.0

    total = 0.0
    position = 0
    while position < len(first):
        total = total + (first[position] * second[position])
        position = position + 1
    return total


def compare_claims(first_text: str, second_text: str, threshold: float = LEXICAL_DUPLICATE_THRESHOLD):
    """Word-based comparison with no engine. Used by the tests."""
    return ClaimComparison(
        similarity=wording_similarity(first_text, second_text),
        numbers_match=numbers_agree(first_text, second_text),
        duplicate_threshold=threshold,
        conflict_threshold=LEXICAL_CONFLICT_THRESHOLD,
        measured_by="words",
        shared_terms=count_shared_terms(first_text, second_text),
        shared_subject_terms=count_shared_subject_terms(first_text, second_text),
    )
