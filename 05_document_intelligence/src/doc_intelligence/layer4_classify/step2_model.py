"""
LAYER 4, STEP 2 - ASK THE MODEL WHAT THIS IS
============================================
Only reached when the rules could not decide. Two things make this safe.

First, "unknown" is an allowed answer, and the prompt says so out loud. A prompt
that offers four choices and demands one of them will confidently label a thank-you
letter a contract, because that is what it asked for. The freedom to decline is
what makes the answer worth anything.

Second, the reply is parsed against our own list of types, not trusted as-is. If
the model invents "delivery_note", that is not a document type this pipeline
knows how to extract, so it becomes UNKNOWN and a person looks at it.
"""

from dataclasses import dataclass

from doc_intelligence.layer0_shared.model_client import ModelReply, ModelUnavailable
from doc_intelligence.layer2_models.schemas import DocumentType

SYSTEM_PROMPT = """You identify business documents. You answer with one word only.

The allowed answers are exactly:
  invoice          a supplier asking to be paid
  receipt          proof that a payment already happened
  purchase_order   a buyer ordering goods, before any invoice exists
  contract         an agreement between parties, with terms and clauses
  unknown          anything else, including letters, emails and reports

Answer "unknown" whenever the document is not clearly one of the first four. A
wrong label is far more expensive here than an honest "unknown", because the
next stage will try to pull payment details out of whatever you say it is.

Reply with the single word. No punctuation, no explanation."""

USER_TEMPLATE = """Identify this document.

--- DOCUMENT START ---
%s
--- DOCUMENT END ---

One word:"""

# How much of the document to show. The top and bottom of a page carry almost
# all of the type signal - titles, headings, signature blocks - so sending the
# whole of a long contract would cost more to learn the same thing.
HEAD_CHARACTERS = 1500
TAIL_CHARACTERS = 600

# The model read the whole document, which is real evidence, but unlike the rule
# path there is no way to check its work. It is capped below the rules ceiling.
MODEL_CONFIDENCE = 0.78


@dataclass
class ModelVerdict:
    document_type: DocumentType
    confidence: float = 0.0
    reason: str = ""
    raw_reply: str = ""
    usage: ModelReply | None = None
    failed: bool = False


def shorten_for_classification(text: str) -> str:
    if len(text) <= HEAD_CHARACTERS + TAIL_CHARACTERS:
        return text
    head = text[:HEAD_CHARACTERS]
    tail = text[-TAIL_CHARACTERS:]
    return head + "\n\n[... middle of the document omitted ...]\n\n" + tail


def parse_type_reply(reply: str) -> DocumentType:
    """
    Match the reply against the types we actually support.

    Longest name first, so "purchase_order" is found before the bare word
    "order" could ever be mistaken for something else.
    """
    lowered = reply.strip().lower()

    candidates = [
        ("purchase_order", DocumentType.PURCHASE_ORDER),
        ("purchase order", DocumentType.PURCHASE_ORDER),
        ("invoice", DocumentType.INVOICE),
        ("receipt", DocumentType.RECEIPT),
        ("contract", DocumentType.CONTRACT),
        ("unknown", DocumentType.UNKNOWN),
    ]
    for word, document_type in candidates:
        if word in lowered:
            return document_type

    return DocumentType.UNKNOWN


def classify_by_model(text: str, client) -> ModelVerdict:
    """`client` is an OpenAIChatClient. Callers must not pass None here."""
    prompt = USER_TEMPLATE % shorten_for_classification(text)

    try:
        reply = client.complete(SYSTEM_PROMPT, prompt, max_tokens=10)
    except ModelUnavailable as error:
        return ModelVerdict(
            document_type=DocumentType.UNKNOWN,
            confidence=0.0,
            reason="the model could not be asked: %s" % error.friendly_message(),
            failed=True,
        )

    document_type = parse_type_reply(reply.text)

    if document_type == DocumentType.UNKNOWN:
        return ModelVerdict(
            document_type=DocumentType.UNKNOWN,
            confidence=0.0,
            reason="the model read the document and did not recognise a "
                   "supported type (it said '%s')" % reply.text.strip()[:40],
            raw_reply=reply.text,
            usage=reply,
        )

    return ModelVerdict(
        document_type=document_type,
        confidence=MODEL_CONFIDENCE,
        reason="the rules were unsure, so the model read the document and "
               "called it a %s" % document_type.value,
        raw_reply=reply.text,
        usage=reply,
    )
