"""
LAYER 5 - WORKERS, STEP 3: RESEARCHING ONE SUB-QUESTION
=======================================================
One worker, one sub-question, start to finish:

    search  ->  read each result  ->  keep the evidence that is good enough

Every worker checks the budget before each step and stops cleanly when there is
none left. It does not raise: a worker that runs out mid-way returns whatever it
already found, and the sub-question is marked so the report can say it was cut
short rather than silently thin.

WHY A WORKER DOES NOT GET ITS OWN BUDGET
    Because four workers with a quarter of the budget each is not the same thing
    as four workers sharing one budget. In the first case an easy sub-question
    leaves its quarter unspent while a hard one is starved. In the second, work
    goes where it is needed and the total still holds.
"""

import time

from research_agent.layer0_shared.budget import Budget, BudgetLimit
from research_agent.layer0_shared.logging_setup import current_sub_question, get_logger, log_event
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer2_models.schemas import Evidence, SubQuestion, SubQuestionStatus
from research_agent.layer5_workers.step1_search import SearchService
from research_agent.layer5_workers.step2_extract_evidence import extract_evidence


class WorkerResult:
    """What one worker produced."""

    def __init__(
        self,
        sub_question: SubQuestion,
        evidence: list[Evidence],
        documents_seen: int,
        stopped_by: BudgetLimit,
    ) -> None:
        self.sub_question = sub_question
        self.evidence = evidence
        self.documents_seen = documents_seen
        self.stopped_by = stopped_by


class ResearchWorker:
    """Researches one sub-question."""

    def __init__(
        self,
        budget: Budget,
        model,
        search_service: SearchService,
        top_k: int,
        keep_per_subquestion: int,
        minimum_relevance: float,
        today_year: int,
    ) -> None:
        self.budget = budget
        self.model = model
        self.search_service = search_service
        self.top_k = top_k
        self.keep_per_subquestion = keep_per_subquestion
        self.minimum_relevance = minimum_relevance
        self.today_year = today_year
        self.log = get_logger(__name__)

    def run(self, sub_question: SubQuestion) -> WorkerResult:
        started = time.perf_counter()
        token = current_sub_question.set(sub_question.sub_question_id)

        try:
            return self.research(sub_question, started)
        finally:
            current_sub_question.reset(token)

    def research(self, sub_question: SubQuestion, started: float) -> WorkerResult:
        # --- is there any budget at all? ---
        limit = self.budget.check()
        if limit != BudgetLimit.NONE:
            self.budget.record_refusal("sub-question: " + sub_question.text[:60], limit)
            sub_question.status = SubQuestionStatus.SKIPPED_NO_BUDGET
            sub_question.note = "Skipped: the run reached its %s limit before this could start." % limit.value
            metrics.increment("subquestions_skipped_total")
            return WorkerResult(sub_question, [], 0, limit)

        # --- search ---
        hits = self.search_service.search(
            query=sub_question.text,
            top_k=self.top_k,
            describe="search for: " + sub_question.text[:60],
        )
        sub_question.searches_run.append(sub_question.text)

        if len(hits) == 0:
            sub_question.status = SubQuestionStatus.NO_EVIDENCE
            sub_question.note = "No sources matched this sub-question."
            sub_question.worker_seconds = round(time.perf_counter() - started, 3)
            return WorkerResult(sub_question, [], 0, BudgetLimit.NONE)

        # --- read each result ---
        collected: list[Evidence] = []
        documents_seen = 0
        stopped_by = BudgetLimit.NONE

        for hit in hits:
            if hit.relevance < self.minimum_relevance:
                # Reading a document that barely matched costs a model call and
                # produces evidence that will be dropped later anyway.
                continue

            limit = self.budget.check()
            if limit != BudgetLimit.NONE:
                stopped_by = limit
                self.budget.record_refusal(
                    "reading further sources for: " + sub_question.text[:50], limit
                )
                break

            documents_seen = documents_seen + 1

            found = extract_evidence(
                sub_question=sub_question.text,
                hit=hit,
                model=self.model,
                today_year=self.today_year,
            )

            for evidence in found:
                evidence.sub_question_id = sub_question.sub_question_id
                collected.append(evidence)

        # --- keep the best ---
        collected.sort(key=lambda item: item.score, reverse=True)
        kept = collected[: self.keep_per_subquestion]

        for evidence in kept:
            sub_question.evidence_ids.append(evidence.evidence_id)

        if len(kept) == 0:
            sub_question.status = SubQuestionStatus.NO_EVIDENCE
            sub_question.note = "Sources were found but none contained a usable finding."
        else:
            sub_question.status = SubQuestionStatus.RESEARCHED
            if stopped_by != BudgetLimit.NONE:
                sub_question.note = (
                    "Cut short by the %s limit after reading %d of %d sources."
                    % (stopped_by.value, documents_seen, len(hits))
                )

        sub_question.worker_seconds = round(time.perf_counter() - started, 3)

        log_event(
            self.log,
            "worker.finished",
            sub_question=sub_question.text[:70],
            documents_read=documents_seen,
            evidence_kept=len(kept),
            evidence_found=len(collected),
            status=sub_question.status.value,
            seconds=sub_question.worker_seconds,
        )

        metrics.increment("subquestions_researched_total")
        metrics.observe("worker_seconds", sub_question.worker_seconds)

        return WorkerResult(sub_question, kept, documents_seen, stopped_by)
