"""
LAYER 5 - WORKERS, STEP 2: TURNING DOCUMENTS INTO EVIDENCE
==========================================================
A search returns documents. A report cites claims. This is the step in between,
and it is where a research system usually starts inventing things.

WHAT EVIDENCE IS
    A claim - one sentence saying what was found - plus the QUOTE from the source
    that supports it. Both, always. A claim without a quote cannot be checked,
    and a "cited" report whose citation points at a whole document is barely
    better than no citation.

------------------------------------------------------------------------------
EVERY QUOTE IS VERIFIED AGAINST THE SOURCE TEXT
------------------------------------------------------------------------------
Asked to quote a document, a model will sometimes produce a sentence that is
almost in the document: a tidied-up version, a merger of two sentences, or a
plausible sentence that was never there at all. The claim then looks perfectly
sourced and is not.

So after extraction, every quote is checked against the document it claims to
come from. A quote that is not there means the evidence is dropped and counted.
This is the same idea as enforcing citations in project 01: the prompt asks, and
the code checks.
"""

import json
import re
import uuid

from research_agent.layer0_shared.llm_client import TASK_EXTRACT
from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer0_shared.text_tools import shorten, to_sentences, to_stems, years_since
from research_agent.layer2_models.schemas import Evidence, SearchHit

log = get_logger(__name__)

EXTRACT_SYSTEM_PROMPT = """You pull findings out of a source document for a specific research question.

For each finding:
  claim - one sentence stating what the source found. Keep the numbers exactly as
          the source gives them. Do not round, soften or generalise.
  quote - the sentence from the document that supports the claim, copied word for
          word. It must appear in the document exactly.

Rules:
- At most 2 findings per document. Choose the ones that answer the question.
- If the document does not address the question, return an empty list. An empty
  answer is correct and useful; an invented one is not.
- Never combine two sentences into one quote.
- Never write a claim the document does not support.

Reply with JSON only:
{"findings": [{"claim": "...", "quote": "..."}]}"""

# Roughly this share of a quote's words must appear, in order, in the document.
QUOTE_MATCH_THRESHOLD = 0.85

WHITESPACE = re.compile(r"\s+")

# WHAT A FINDING LOOKS LIKE, versus what framing looks like.
#
# The rule-based extractor originally scored sentences purely on overlap with the
# sub-question, and lost every time. Given the question "evidence for the
# four-day week and productivity", this sentence won:
#
#     "We report outcomes from a six-month trial of a four-day working week
#      across 61 organisations and 2,900 employees."          overlap 0.40
#
# and this one lost:
#
#     "Self-reported productivity rose by 12 percent."         overlap 0.10
#
# The first repeats the question's words back; the second is the actual result.
# Overlap with the question rewards restating the question. So a sentence that
# pairs a number with a UNIT gets a large bonus: that is what a measured finding
# looks like, and framing sentences almost never do it.
MEASUREMENT_UNITS = [
    "percent", "per cent", "%", "percentage points", "times", "fold",
    "watt-hours", "watt hours", "kilogram", "hours", "minutes", "days", "weeks",
    "months", "years", "cycles", "dollars", "gigawatt",
]
RESULT_VERBS = [
    "rose", "fell", "increased", "decreased", "declined", "improved", "reduced",
    "found", "showed", "reached", "achieved", "produced", "recorded", "retained",
]
MEASUREMENT_BONUS = 0.45
RESULT_VERB_BONUS = 0.10


def looks_like_a_measured_finding(sentence: str) -> bool:
    """A number standing next to a unit. That is what a result looks like."""
    lowered = sentence.lower()

    has_number = False
    for character in lowered:
        if character.isdigit():
            has_number = True
            break

    if not has_number:
        return False

    for unit in MEASUREMENT_UNITS:
        if unit in lowered:
            return True
    return False


def has_result_verb(sentence: str) -> bool:
    lowered = sentence.lower()
    for verb in RESULT_VERBS:
        if verb in lowered:
            return True
    return False


def normalise_for_matching(text: str) -> str:
    """
    Lowercase, collapse whitespace, and settle the punctuation that varies.

    Hyphens become spaces. Models write "self reported" where the source says
    "self-reported", and a quote rejected over a hyphen is a correct citation
    thrown away - which pushes the system towards having no evidence rather than
    towards being careful. Curly quotes and dashes are normalised for the same
    reason: they differ between a PDF, a web page and a model's output, and none
    of those differences mean the quote is wrong.
    """
    lowered = text.lower()

    for fancy, plain in [
        ("\u2019", "'"), ("\u2018", "'"),
        ("\u201c", '"'), ("\u201d", '"'),
        ("\u2013", "-"), ("\u2014", "-"),
    ]:
        lowered = lowered.replace(fancy, plain)

    lowered = lowered.replace("-", " ")
    return WHITESPACE.sub(" ", lowered).strip()


def quote_is_in_document(quote: str, body: str) -> bool:
    """
    Is this quote really in the document?

    An exact match first, which is what an honest quote gives. Failing that, a
    word-overlap check against each sentence, so a quote differing only in
    punctuation or a trailing clause still passes. A quote that matches no
    sentence closely is treated as invented.
    """
    if quote.strip() == "":
        return False

    clean_quote = normalise_for_matching(quote)
    clean_body = normalise_for_matching(body)

    if clean_quote in clean_body:
        return True

    quote_words = clean_quote.split()
    if len(quote_words) < 4:
        # Too short to verify meaningfully, and too short to be worth citing.
        return False

    for sentence in to_sentences(clean_body):
        sentence_words = set(sentence.split())

        matched = 0
        for word in quote_words:
            if word in sentence_words:
                matched = matched + 1

        if (matched / len(quote_words)) >= QUOTE_MATCH_THRESHOLD:
            return True

    return False


def recency_score(published_date: str, today_year: int) -> float:
    """
    Newer is worth more, gently.

    Gently on purpose. A 2019 randomised trial usually beats a 2025 blog post,
    and a steep recency curve would invert that. Full marks for this year,
    falling to about 0.5 after five years and flattening out.
    """
    age = years_since(published_date, today_year)
    if age <= 0:
        return 1.0
    return round(1.0 / (1.0 + (age * 0.2)), 4)


def build_evidence(
    sub_question_id: str,
    hit: SearchHit,
    claim: str,
    quote: str,
    today_year: int,
) -> Evidence:
    document = hit.document
    credibility = document.credibility()
    recency = recency_score(document.published_date, today_year)

    # THE THREE SIGNALS, WEIGHTED AND VISIBLE.
    # Relevance dominates: evidence that does not answer the question is not
    # evidence, however credible its source. Credibility is the next largest, and
    # recency only breaks ties. These weights are arguable, which is why they are
    # here in one line rather than spread through the code.
    score = (0.50 * hit.relevance) + (0.35 * credibility) + (0.15 * recency)

    return Evidence(
        evidence_id="ev_" + uuid.uuid4().hex[:8],
        sub_question_id=sub_question_id,
        claim=claim.strip(),
        quote=quote.strip(),
        document_id=document.document_id,
        title=document.title,
        source_name=document.source_name,
        source_type=document.source_type,
        published_date=document.published_date,
        url=document.url,
        relevance=round(hit.relevance, 4),
        credibility=round(credibility, 4),
        recency=recency,
        score=round(score, 4),
    )


def extract_with_rules(sub_question: str, hit: SearchHit, today_year: int) -> list[Evidence]:
    """
    The no-model extractor: pick the sentences that best match the question.

    The claim and the quote are the same sentence, which is honest - it makes no
    attempt to paraphrase, so it cannot paraphrase wrongly. What it cannot do is
    combine two sentences into one finding, or notice a finding stated across a
    paragraph.
    """
    question_stems = set(to_stems(sub_question))
    if len(question_stems) == 0:
        return []

    scored: list[tuple[float, str]] = []
    for sentence in to_sentences(hit.document.body):
        sentence_stems = to_stems(sentence)
        if len(sentence_stems) < 4:
            continue

        matched = 0
        seen: set[str] = set()
        for stem in sentence_stems:
            if stem in seen:
                continue
            seen.add(stem)
            if stem in question_stems:
                matched = matched + 1

        if matched == 0:
            continue

        overlap = matched / len(question_stems)

        # See the note on MEASUREMENT_UNITS. Overlap alone rewards sentences that
        # restate the question, which are exactly the ones worth least.
        if looks_like_a_measured_finding(sentence):
            overlap = overlap + MEASUREMENT_BONUS
        if has_result_verb(sentence):
            overlap = overlap + RESULT_VERB_BONUS

        scored.append((overlap, sentence))

    scored.sort(key=lambda entry: entry[0], reverse=True)

    evidence: list[Evidence] = []
    for _overlap, sentence in scored[:2]:
        evidence.append(
            build_evidence(
                sub_question_id="",
                hit=hit,
                claim=sentence,
                quote=sentence,
                today_year=today_year,
            )
        )
    return evidence


def extract_with_model(sub_question: str, hit: SearchHit, model, today_year: int) -> list[Evidence]:
    """Ask the model, then verify every quote it produced."""
    user_prompt = (
        "RESEARCH QUESTION: " + sub_question + "\n\n"
        "DOCUMENT TITLE: " + hit.document.title + "\n"
        "SOURCE: " + hit.document.source_name + " (" + hit.document.source_type.value + ")\n"
        "PUBLISHED: " + hit.document.published_date + "\n\n"
        "DOCUMENT:\n" + hit.document.body
    )

    result = model.complete(
        system_prompt=EXTRACT_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        task=TASK_EXTRACT,
        json_mode=True,
        max_tokens=600,
        describe="reading " + hit.document.document_id,
    )

    if result is None:
        return []

    try:
        parsed = json.loads(result.text)
        findings = parsed.get("findings", [])
    except (json.JSONDecodeError, AttributeError):
        log.warning("could not parse extraction output for %s", hit.document.document_id)
        return []

    evidence: list[Evidence] = []
    for finding in findings[:2]:
        if not isinstance(finding, dict):
            continue

        claim = str(finding.get("claim", "")).strip()
        quote = str(finding.get("quote", "")).strip()

        if len(claim) < 10:
            continue

        # THE CHECK. A quote that is not in the document means the claim is not
        # sourced, whatever it says.
        if not quote_is_in_document(quote, hit.document.body):
            metrics.increment("invented_quotes_total")
            log_event(
                log,
                "evidence.quote_not_in_source",
                document_id=hit.document.document_id,
                quote=shorten(quote, 120),
            )
            continue

        evidence.append(
            build_evidence(
                sub_question_id="",
                hit=hit,
                claim=claim,
                quote=quote,
                today_year=today_year,
            )
        )

    return evidence


def extract_evidence(sub_question: str, hit: SearchHit, model, today_year: int) -> list[Evidence]:
    if model.is_live:
        return extract_with_model(sub_question, hit, model, today_year)
    return extract_with_rules(sub_question, hit, today_year)
