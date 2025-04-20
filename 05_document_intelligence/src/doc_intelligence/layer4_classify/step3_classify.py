"""
LAYER 4, STEP 3 - THE CLASSIFICATION DECISION
=============================================
Rules, then the model, then honest failure. In that order, and never the other
way round.

Across the twelve sample documents the rules settle eleven on their own. Putting
the model first would have cost twelve calls to learn the same eleven answers.
"""

from dataclasses import dataclass

from doc_intelligence.layer0_shared.usage import UsageAccumulator
from doc_intelligence.layer2_models.schemas import DocumentType
from doc_intelligence.layer4_classify.step1_rules import (
    classify_by_rules,
    confidence_from_score,
)
from doc_intelligence.layer4_classify.step2_model import classify_by_model


@dataclass
class Classification:
    document_type: DocumentType
    confidence: float
    reason: str
    decided_by: str          # "rules" | "model" | "neither"


def classify_document(text: str, client=None,
                      usage: UsageAccumulator | None = None) -> Classification:
    verdict = classify_by_rules(text)

    if verdict.is_confident:
        return Classification(
            document_type=verdict.document_type,
            confidence=confidence_from_score(verdict),
            reason=verdict.reason,
            decided_by="rules",
        )

    if client is None:
        # No model available. The honest answer is "I do not know", and that is
        # a usable answer: layer 8 puts it in front of a person, who can tell
        # in two seconds what the pipeline could not.
        return Classification(
            document_type=DocumentType.UNKNOWN,
            confidence=0.0,
            reason=verdict.reason + " (no model available to ask, so this needs a person)",
            decided_by="neither",
        )

    model_verdict = classify_by_model(text, client)

    if model_verdict.usage is not None and usage is not None:
        usage.record("classify", model_verdict.usage.input_tokens,
                     model_verdict.usage.output_tokens)

    if model_verdict.document_type == DocumentType.UNKNOWN:
        return Classification(
            document_type=DocumentType.UNKNOWN,
            confidence=0.0,
            reason=verdict.reason + "; " + model_verdict.reason,
            decided_by="neither",
        )

    return Classification(
        document_type=model_verdict.document_type,
        confidence=model_verdict.confidence,
        reason=model_verdict.reason,
        decided_by="model",
    )
