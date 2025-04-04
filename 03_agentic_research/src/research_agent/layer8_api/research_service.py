"""
LAYER 8 - THE RESEARCH SERVICE
==============================
One question in, one cited report out. The order of operations is the design:

     1. open a budget for this run - one, shared by everything below
     2. PLAN      decompose the question, then validate the plan
     3. RESEARCH  run the sub-questions in parallel, all spending that one budget
     4. DEDUPE    collapse findings that say the same thing
     5. CONFLICTS find findings that disagree, and explain them
     6. SYNTHESISE write the sections, then the summary, from the reserve
     7. VERIFY    strip invented citations, check every figure, score confidence
     8. REPORT    including what was NOT done, and why

Step 8 is the one that separates this from a demo. A run that hit its time limit
after four of six sub-questions produces a report that says so, lists the two it
did not reach, and scores its own confidence lower. A demo produces the same
four sections and says nothing.

The same service is used by the API, the tests and the evaluation suite. If they
used different code paths, the evaluation would not describe the product.
"""

import time
from datetime import UTC, datetime

from research_agent.layer0_shared.budget import Budget, build_budget
from research_agent.layer0_shared.embeddings import build_embedder
from research_agent.layer0_shared.llm_client import BudgetedModel, build_chat_client
from research_agent.layer0_shared.logging_setup import (
    current_run_id,
    get_logger,
    log_event,
    new_run_id,
)
from research_agent.layer0_shared.metrics import metrics
from research_agent.layer0_shared.similarity import ClaimSimilarityEngine
from research_agent.layer1_config.settings import settings
from research_agent.layer2_models.schemas import (
    Evidence,
    ResearchPlan,
    ResearchResponse,
    RunTrace,
    StageTrace,
    SubQuestionStatus,
)
from research_agent.layer4_planner.step1_decompose import decompose
from research_agent.layer4_planner.step2_validate_plan import build_plan
from research_agent.layer5_workers.step1_search import SearchService
from research_agent.layer5_workers.step3_worker import ResearchWorker
from research_agent.layer5_workers.step4_pool import research_in_parallel
from research_agent.layer6_evidence.step1_deduplicate import deduplicate
from research_agent.layer6_evidence.step2_detect_conflicts import explain_conflicts, find_conflicts
from research_agent.layer7_synthesis.step1_build_report import build_report
from research_agent.layer7_synthesis.step2_verify_report import score_confidence, verify_report

log = get_logger(__name__)


class StageTimer:
    """Times one stage and records what it cost, for the trace."""

    def __init__(self, name: str, budget: Budget) -> None:
        self.name = name
        self.budget = budget
        self.started = time.perf_counter()
        self.tokens_at_start = budget.spent_tokens
        self.cost_at_start = budget.spent_cost_usd
        self.calls_at_start = budget.model_calls_made

    def finish(self, detail: str = "") -> StageTrace:
        return StageTrace(
            name=self.name,
            seconds=round(time.perf_counter() - self.started, 3),
            model_calls=self.budget.model_calls_made - self.calls_at_start,
            tokens=self.budget.spent_tokens - self.tokens_at_start,
            cost_usd=round(self.budget.spent_cost_usd - self.cost_at_start, 6),
            detail=detail,
        )


class ResearchService:
    """Runs one research question."""

    def __init__(self, chat_client=None, embedder=None) -> None:
        if chat_client is None:
            chat_client = build_chat_client()
        self.chat_client = chat_client

        # Built once. None when no key is configured, in which case similarity
        # falls back to comparing words and the report says so.
        if embedder is None:
            embedder = build_embedder()
        self.embedder = embedder

    def research(self, request) -> ResearchResponse:
        run_id = new_run_id()
        token = current_run_id.set(run_id)
        started = time.perf_counter()

        try:
            return self.run(run_id, request, started)
        finally:
            current_run_id.reset(token)

    def run(self, run_id: str, request, started: float) -> ResearchResponse:
        metrics.increment("runs_total")

        # ---------- 1. one budget for the whole run ----------
        budget = build_budget()
        if request.max_seconds is not None:
            budget.max_seconds = request.max_seconds
        if request.max_tokens is not None:
            budget.max_tokens = request.max_tokens
        if request.max_tool_calls is not None:
            budget.max_tool_calls = request.max_tool_calls

        model = BudgetedModel(self.chat_client, budget)
        engine = ClaimSimilarityEngine(self.embedder)

        max_subquestions = settings.budget_max_subquestions
        if request.max_subquestions is not None:
            max_subquestions = min(request.max_subquestions, settings.budget_max_subquestions)

        trace = RunTrace(run_id=run_id, question=request.question)

        log_event(
            log,
            "run.started",
            question=request.question[:100],
            budget=budget.snapshot(),
            comparing_by=engine.measured_by,
        )

        # ---------- 2. plan ----------
        timer = StageTimer("plan", budget)
        raw_plan = decompose(request.question, max_subquestions, model)
        plan, validation = build_plan(
            question=request.question,
            raw_plan=raw_plan,
            max_subquestions=max_subquestions,
            similarity=self.similarity_function(engine),
            duplicate_threshold=engine.subquestion_duplicate_threshold,
        )
        trace.stages.append(timer.finish(plan.planner_note))
        metrics.increment("subquestions_planned_total", len(plan.sub_questions))

        # ---------- 3. research, in parallel ----------
        timer = StageTimer("research", budget)
        worker = ResearchWorker(
            budget=budget,
            model=model,
            search_service=SearchService(budget),
            top_k=settings.search_top_k,
            keep_per_subquestion=settings.evidence_per_subquestion,
            minimum_relevance=settings.min_evidence_relevance,
            today_year=datetime.now(UTC).year,
        )

        results = research_in_parallel(
            sub_questions=plan.sub_questions,
            worker=worker,
            budget=budget,
            max_parallel=settings.budget_max_parallel_workers,
        )

        all_evidence: list[Evidence] = []
        documents_seen = 0
        workers_run = 0
        workers_skipped = 0

        for result in results:
            documents_seen = documents_seen + result.documents_seen
            for evidence in result.evidence:
                all_evidence.append(evidence)

            if result.sub_question.status == SubQuestionStatus.SKIPPED_NO_BUDGET:
                workers_skipped = workers_skipped + 1
            else:
                workers_run = workers_run + 1

        trace.stages.append(
            timer.finish("%d sub-questions, %d documents read" % (len(results), documents_seen))
        )
        trace.documents_seen = documents_seen
        trace.evidence_collected = len(all_evidence)
        trace.workers_run = workers_run
        trace.workers_skipped = workers_skipped

        # ---------- 4. deduplicate ----------
        timer = StageTimer("deduplicate", budget)
        deduplication = deduplicate(all_evidence, engine)
        trace.stages.append(timer.finish(str(deduplication.summary())))
        trace.evidence_after_dedupe = len(deduplication.kept)
        trace.duplicates_removed = len(deduplication.removed)

        # A removed duplicate must not stay attached to its sub-question, or the
        # report cites a piece of evidence that is no longer in the citation list.
        kept_ids: set[str] = set()
        for evidence in deduplication.kept:
            kept_ids.add(evidence.evidence_id)

        for sub_question in plan.sub_questions:
            surviving: list[str] = []
            for evidence_id in sub_question.evidence_ids:
                if evidence_id in kept_ids:
                    surviving.append(evidence_id)
            sub_question.evidence_ids = surviving

            # A sub-question can lose ALL of its evidence here, when everything it
            # found was already reported under another sub-question. It is then
            # still marked "researched" while having nothing to show, and the
            # summary happily includes its empty section. Its status has to follow
            # the evidence.
            if (
                sub_question.status == SubQuestionStatus.RESEARCHED
                and len(surviving) == 0
            ):
                sub_question.status = SubQuestionStatus.NO_EVIDENCE
                sub_question.note = (
                    "Everything this sub-question found was already covered by "
                    "another sub-question."
                )

        evidence_by_id: dict[str, Evidence] = {}
        for evidence in deduplication.kept:
            evidence_by_id[evidence.evidence_id] = evidence

        # ---------- 5. conflicts ----------
        timer = StageTimer("conflicts", budget)
        conflicts = find_conflicts(deduplication.kept, engine)
        conflicts = explain_conflicts(conflicts, model)
        trace.stages.append(timer.finish("%d conflicts" % len(conflicts)))
        trace.conflicts_found = len(conflicts)

        # ---------- 6. synthesise ----------
        timer = StageTimer("synthesise", budget)
        report = build_report(
            question=request.question,
            sub_questions=plan.sub_questions,
            evidence_by_id=evidence_by_id,
            conflicts=conflicts,
            model=model,
        )
        trace.stages.append(timer.finish("%d sections" % len(report.sections)))

        # ---------- 7. verify ----------
        timer = StageTimer("verify", budget)
        verification = verify_report(report, evidence_by_id)

        report.partial = budget.was_exhausted() or workers_skipped > 0
        report.gaps = self.collect_gaps(plan, budget)

        confidence, reason = score_confidence(
            report=report,
            verification=verification,
            sub_question_count=len(plan.sub_questions),
            researched_count=plan.researched_count(),
            budget_exhausted=budget.was_exhausted(),
        )
        report.confidence = confidence
        report.confidence_reason = reason

        trace.stages.append(timer.finish(str(verification.to_dict())))

        # ---------- 8. finish ----------
        trace.budget = budget.snapshot()
        elapsed = round(time.perf_counter() - started, 3)

        if budget.was_exhausted():
            metrics.increment("runs_budget_exhausted_total")
        if report.partial:
            metrics.increment("reports_partial_total")

        metrics.observe("run_seconds", elapsed)
        metrics.observe("run_tokens", budget.spent_tokens)
        metrics.observe("run_cost_usd", budget.spent_cost_usd)
        metrics.observe("run_confidence", confidence)

        log_event(
            log,
            "run.finished",
            seconds=elapsed,
            sub_questions=len(plan.sub_questions),
            researched=plan.researched_count(),
            evidence=len(deduplication.kept),
            duplicates_removed=len(deduplication.removed),
            conflicts=len(conflicts),
            partial=report.partial,
            confidence=confidence,
            budget=budget.snapshot(),
        )

        return ResearchResponse(
            run_id=run_id,
            question=request.question,
            plan=plan,
            report=report,
            trace=trace,
            seconds=elapsed,
            cost_usd=round(budget.spent_cost_usd, 6),
        )

    # ------------------------------------------------------------------

    def similarity_function(self, engine: ClaimSimilarityEngine):
        """
        The function the plan validator uses to spot near-duplicate sub-questions.

        The engine has no vectors yet at planning time - nothing has been embedded
        - so this embeds the sub-questions on demand. There are only a handful, so
        one extra small call is worth avoiding a whole duplicated sub-question.
        """

        def compare(first: str, second: str) -> float:
            if engine.embedder is not None:
                engine.prepare([first, second])
            return engine.similarity(first, second)

        return compare

    def collect_gaps(self, plan: ResearchPlan, budget: Budget) -> list[str]:
        """
        What the report does not cover, in plain words.

        This is the honesty section. Without it a partial report looks exactly
        like a complete one, and the reader has no way to tell that two of the six
        angles were never looked at.
        """
        gaps: list[str] = []

        for sub_question in plan.sub_questions:
            if sub_question.status == SubQuestionStatus.SKIPPED_NO_BUDGET:
                gaps.append("Not researched (ran out of budget): " + sub_question.text)
            elif sub_question.status == SubQuestionStatus.NO_EVIDENCE:
                gaps.append("No sources found for: " + sub_question.text)
            elif sub_question.note != "" and "Cut short" in sub_question.note:
                gaps.append(sub_question.note + " Sub-question: " + sub_question.text)

        for refusal in budget.refusals:
            if refusal not in gaps:
                gaps.append(refusal)

        return gaps
