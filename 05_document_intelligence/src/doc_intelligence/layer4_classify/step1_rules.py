"""
LAYER 4, STEP 1 - CLASSIFY BY RULES
===================================
Decide what a document is by counting the phrases that only appear on that kind
of document.

Nobody needs a language model to notice the words "PURCHASE ORDER" at the top of
a page. Paying one to do it is the exact mistake this project exists to teach,
so the rules run first and the model is only consulted when the rules cannot
separate two candidates.

Two details matter more than the word list:

1. Phrases are weighted. "purchase order" is worth 4 because nothing else says
   it. "total" is worth nothing at all, because every one of these documents has
   a total - a signal shared by every class carries no information, exactly like
   the IDF weighting in project 01.

2. The decision needs a *margin*, not just a high score. A receipt scores well
   on invoice words too, so "invoice won by 1 point" is not a decision, it is a
   coin toss that happened to land. Without a margin the classifier is confident
   and wrong, which is worse than unsure and honest.
"""

from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import DocumentType

# Phrase -> weight. Weight means "how much only this kind of document says this".
INVOICE_SIGNALS = {
    "tax invoice": 4.0,
    "invoice number": 3.0,
    "invoice no": 3.0,
    "invoice date": 2.5,
    "amount due": 2.0,
    "remittance": 2.0,
    "invoice": 1.5,
    "payment terms": 1.0,
    "bill to": 1.0,
    "due date": 1.0,
    "iban": 0.5,
}

RECEIPT_SIGNALS = {
    "receipt no": 4.0,
    "receipt number": 4.0,
    "receipt": 3.0,
    "change due": 2.5,
    "cashier": 2.0,
    "paid by card": 2.0,
    "card ending": 2.0,
    "till": 1.5,
    "thank you": 0.5,
}

PURCHASE_ORDER_SIGNALS = {
    "purchase order": 4.0,
    "po number": 4.0,
    "po no": 3.0,
    "authorised by": 2.0,
    "authorized by": 2.0,
    "required by": 2.0,
    "order date": 2.0,
    "ship to": 1.5,
    "supplier": 1.0,
}

CONTRACT_SIGNALS = {
    "governing law": 4.0,
    "this agreement": 3.5,
    "services agreement": 3.5,
    "in witness whereof": 3.5,
    "hereby": 2.0,
    "termination": 2.0,
    "signed for": 2.0,
    "clause": 1.5,
    "the parties": 1.5,
    "written notice": 1.5,
    "agreement": 1.5,
}

SIGNALS_BY_TYPE = {
    DocumentType.INVOICE: INVOICE_SIGNALS,
    DocumentType.RECEIPT: RECEIPT_SIGNALS,
    DocumentType.PURCHASE_ORDER: PURCHASE_ORDER_SIGNALS,
    DocumentType.CONTRACT: CONTRACT_SIGNALS,
}

# A document scoring at least this much, and beating the runner-up by at least
# the margin, is decided without asking a model.
CONFIDENT_SCORE = 4.0
CONFIDENT_MARGIN = 2.0


@dataclass
class RuleVerdict:
    document_type: DocumentType
    score: float = 0.0
    runner_up: DocumentType = DocumentType.UNKNOWN
    runner_up_score: float = 0.0
    matched: list[str] = field(default_factory=list)
    is_confident: bool = False
    reason: str = ""

    def margin(self) -> float:
        return self.score - self.runner_up_score


def score_one_type(lowered_text: str, signals: dict) -> tuple[float, list[str]]:
    total = 0.0
    matched: list[str] = []
    for phrase, weight in signals.items():
        if phrase in lowered_text:
            total = total + weight
            matched.append("%s (+%.1f)" % (phrase, weight))
    return total, matched


def classify_by_rules(text: str) -> RuleVerdict:
    lowered = text.lower()

    scores: list[tuple[DocumentType, float, list[str]]] = []
    for document_type, signals in SIGNALS_BY_TYPE.items():
        score, matched = score_one_type(lowered, signals)
        scores.append((document_type, score, matched))

    # Highest score first. Sorting on the score alone would make ties depend on
    # dictionary order, so the type name breaks ties to keep runs repeatable.
    def sort_key(entry):
        return (-entry[1], entry[0].value)

    scores.sort(key=sort_key)

    best_type, best_score, best_matched = scores[0]
    second_type, second_score, _ = scores[1]

    verdict = RuleVerdict(
        document_type=best_type,
        score=best_score,
        runner_up=second_type,
        runner_up_score=second_score,
        matched=best_matched,
    )

    if best_score < CONFIDENT_SCORE:
        verdict.document_type = DocumentType.UNKNOWN
        verdict.is_confident = False
        verdict.reason = (
            "no document type scored above %.1f (best was %s at %.1f), so the "
            "rules cannot say what this is"
            % (CONFIDENT_SCORE, best_type.value, best_score)
        )
        return verdict

    if verdict.margin() < CONFIDENT_MARGIN:
        verdict.is_confident = False
        verdict.reason = (
            "%s scored %.1f but %s scored %.1f - a margin of %.1f is too close "
            "to call from words alone"
            % (best_type.value, best_score, second_type.value, second_score,
               verdict.margin())
        )
        return verdict

    verdict.is_confident = True
    verdict.reason = (
        "%s scored %.1f, clear of %s at %.1f; matched %s"
        % (best_type.value, best_score, second_type.value, second_score,
           ", ".join(best_matched[:4]))
    )
    return verdict


# The shape of the confidence curve. These are separated out because the first
# version of this function summed to exactly 1.00 for eleven of the twelve
# sample documents - a number that is the same for every input is not a
# measurement, it is decoration, and it would have hidden a genuinely marginal
# document among the certain ones.
BASE_CONFIDENCE = 0.55            # earned by clearing the score and margin gates
EVIDENCE_WEIGHT = 0.28            # how much distinctive evidence was found
EVIDENCE_FULL_SCORE = 10.0        # score at which the evidence part maxes out
MARGIN_WEIGHT = 0.14              # how far clear of the runner-up it landed
MARGIN_FULL_SCORE = 10.0

# Never 1.00. A phrase counter cannot be certain, and writing 1.00 would invite
# every later stage to stop questioning the answer.
CONFIDENCE_CEILING = 0.97


def confidence_from_score(verdict: RuleVerdict) -> float:
    """
    Turn a phrase score into a 0-1 confidence.

    This is deliberately not the model's own opinion of itself. It is built from
    two observable facts: how much distinctive evidence was found, and how far
    clear of the second-best answer it landed. Both are things you can check by
    reading the document; "how confident are you?" is not.
    """
    if verdict.document_type == DocumentType.UNKNOWN:
        return 0.0

    evidence_fraction = verdict.score / EVIDENCE_FULL_SCORE
    if evidence_fraction > 1.0:
        evidence_fraction = 1.0
    if evidence_fraction < 0.0:
        evidence_fraction = 0.0

    margin_fraction = verdict.margin() / MARGIN_FULL_SCORE
    if margin_fraction > 1.0:
        margin_fraction = 1.0
    if margin_fraction < 0.0:
        margin_fraction = 0.0

    confidence = (BASE_CONFIDENCE
                  + evidence_fraction * EVIDENCE_WEIGHT
                  + margin_fraction * MARGIN_WEIGHT)
    if confidence > CONFIDENCE_CEILING:
        confidence = CONFIDENCE_CEILING
    return round(confidence, 3)
