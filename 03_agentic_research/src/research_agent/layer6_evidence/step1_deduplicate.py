"""
LAYER 6 - EVIDENCE, STEP 1: THE SAME FINDING, TWICE
===================================================
Search returns the same study more than once. A journal publishes it, a newspaper
writes it up, an industry report cites it. Left alone, the report ends up making
the same point three times and looking like it has three times the support.

So near-identical findings are collapsed to one, and the others are recorded as
corroboration rather than thrown away.

------------------------------------------------------------------------------
CORROBORATION IS NOT AUTOMATIC. MOST OF IT IS NOT INDEPENDENT.
------------------------------------------------------------------------------
The obvious move is to raise a finding's score for each source that repeats it.
It is also wrong most of the time.

    A journal publishes a trial finding 12 percent.
    A newspaper reports the journal's finding of 12 percent.

That is ONE study and one write-up of it, not two studies agreeing. Counting the
newspaper as corroboration makes a single result look twice as well supported -
which is precisely the error that makes a research report more confident than the
research is.

So the bonus applies only between sources that could plausibly have found the
same thing independently: two peer-reviewed studies, or a study and a government
evaluation. A news article or a blog repeating a finding is recorded as
"also reported by", and adds nothing to the score.
"""

from research_agent.layer0_shared.logging_setup import get_logger, log_event
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer2_models.schemas import Evidence, SourceType

log = get_logger(__name__)

# Source types that do original work. Two of these agreeing is real corroboration.
PRIMARY_SOURCE_TYPES = {
    SourceType.PEER_REVIEWED.value,
    SourceType.GOVERNMENT.value,
    SourceType.INDUSTRY_REPORT.value,
}

# How much a genuinely independent second source is worth. Deliberately modest:
# two studies agreeing is better than one, but not twice as good.
CORROBORATION_BONUS = 0.08
MAXIMUM_CORROBORATION_BONUS = 0.16


class DeduplicationResult:
    def __init__(self, kept: list[Evidence], removed: list[Evidence], measured_by: str) -> None:
        self.kept = kept
        self.removed = removed
        self.measured_by = measured_by

    def summary(self) -> dict:
        return {
            "kept": len(self.kept),
            "removed_as_duplicates": len(self.removed),
            "compared_by": self.measured_by,
        }


def sources_are_independent(first: Evidence, second: Evidence) -> bool:
    """
    Could these two plausibly have found the same thing separately?

    Both must do original work, and they must be different organisations. Two
    papers from the same journal on the same finding are very often the same
    research group, so the journal name has to differ too.
    """
    if first.source_type.value not in PRIMARY_SOURCE_TYPES:
        return False
    if second.source_type.value not in PRIMARY_SOURCE_TYPES:
        return False
    return first.source_name.strip().lower() != second.source_name.strip().lower()


def deduplicate(evidence_items: list[Evidence], engine) -> DeduplicationResult:
    """
    Collapse findings that say the same thing.

    The highest-scoring item in each group is kept. Which one that is matters:
    keeping the journal rather than the newspaper means the report cites the
    study itself, which is what a reader following the citation wants to reach.
    """
    if len(evidence_items) == 0:
        return DeduplicationResult([], [], engine.measured_by)

    # Embed every claim in one batch before any comparison.
    claims: list[str] = []
    for item in evidence_items:
        claims.append(item.claim)
    engine.prepare(claims)

    # Best first, so the strongest member of a group is the one that survives.
    ordered = sorted(evidence_items, key=lambda item: item.score, reverse=True)

    kept: list[Evidence] = []
    removed: list[Evidence] = []

    for candidate in ordered:
        matched_existing = None

        for existing in kept:
            verdict = engine.compare(candidate.claim, existing.claim)
            if verdict.is_duplicate():
                matched_existing = existing
                break

        if matched_existing is None:
            kept.append(candidate)
            continue

        candidate.duplicate_of = matched_existing.evidence_id
        removed.append(candidate)

        matched_existing.corroborated_by.append(candidate.evidence_id)

        if sources_are_independent(matched_existing, candidate):
            bonus = min(
                MAXIMUM_CORROBORATION_BONUS,
                CORROBORATION_BONUS * len(matched_existing.corroborated_by),
            )
            matched_existing.score = round(min(1.0, matched_existing.score + bonus), 4)

            log_event(
                log,
                "evidence.corroborated",
                kept=matched_existing.source_name,
                by=candidate.source_name,
                new_score=matched_existing.score,
            )
        else:
            log_event(
                log,
                "evidence.repeated_not_corroborated",
                kept=matched_existing.source_name,
                repeated_by=candidate.source_name,
                reason="not an independent source",
            )

    metrics.increment("duplicates_removed_total", len(removed))

    return DeduplicationResult(kept=kept, removed=removed, measured_by=engine.measured_by)
