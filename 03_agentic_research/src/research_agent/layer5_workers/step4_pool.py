"""
LAYER 5 - WORKERS, STEP 4: RUNNING THEM IN PARALLEL
===================================================
Sub-questions are independent by construction - that was the planner's job - so
they can be researched at the same time. Four sub-questions in parallel turn a
twelve second run into about four.

THE THING THAT MAKES THIS HARDER THAN A THREAD POOL
    All of them are spending from the same budget, at the same time, from
    different threads. Every counter in Budget is behind a lock for exactly this
    reason, and this file is where that stops being theoretical.

    The consequence worth understanding: workers do not get an equal share. They
    race. Three cheap sub-questions may finish on very little, leaving plenty for
    a fourth hard one - or an expensive first worker may leave the others with
    nothing, and they will say so rather than quietly returning less.

    That is the correct behaviour. The alternative, dividing the budget evenly up
    front, wastes whatever the easy sub-questions do not use and starves the hard
    one that needed it.

WHY THE POOL IS CAPPED SEPARATELY
    Parallelism is its own limit. Eight workers against a rate-limited search API
    is how you get blocked, even if every other budget is respected.
"""

import time
from concurrent.futures import ThreadPoolExecutor

from research_agent.layer0_shared.budget import Budget
from research_agent.layer0_shared.logging_setup import current_run_id, get_logger, log_event
from research_agent.layer2_models.schemas import SubQuestion
from research_agent.layer5_workers.step3_worker import ResearchWorker, WorkerResult

log = get_logger(__name__)


def research_in_parallel(
    sub_questions: list[SubQuestion],
    worker: ResearchWorker,
    budget: Budget,
    max_parallel: int,
) -> list[WorkerResult]:
    """
    Research every sub-question, several at a time.

    Results come back in the order the sub-questions were planned, not the order
    they finished, so the report reads in a sensible sequence regardless of which
    search happened to be slow.
    """
    if len(sub_questions) == 0:
        return []

    started = time.perf_counter()
    run_id = current_run_id.get()

    def run_one(sub_question: SubQuestion) -> WorkerResult:
        # Each thread gets its own copy of the context variables, so the run id
        # has to be set again inside the thread or every worker logs "-".
        current_run_id.set(run_id)
        return worker.run(sub_question)

    workers_to_use = min(max_parallel, len(sub_questions))

    results: list[WorkerResult] = []
    with ThreadPoolExecutor(max_workers=workers_to_use) as pool:
        # executor.map keeps input order, which is what we want for the report.
        for result in pool.map(run_one, sub_questions):
            results.append(result)

    elapsed = round(time.perf_counter() - started, 3)

    total_worker_seconds = 0.0
    for result in results:
        total_worker_seconds = total_worker_seconds + result.sub_question.worker_seconds

    log_event(
        log,
        "pool.finished",
        sub_questions=len(sub_questions),
        parallel_workers=workers_to_use,
        wall_clock_seconds=elapsed,
        total_worker_seconds=round(total_worker_seconds, 3),
        budget=budget.snapshot(),
    )

    return results
