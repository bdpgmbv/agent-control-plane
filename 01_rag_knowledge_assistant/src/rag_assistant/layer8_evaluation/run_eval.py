"""
LAYER 8 - EVALUATION: THE RUNNER
================================
Runs every case in the golden dataset through the SAME service the API uses, and
prints a report.

Run it from the command line:

    python -m rag_assistant.layer8_evaluation.run_eval

or from the UI's "Run evaluation" button, which calls the same function.

The report is the deliverable of this whole project. "I built a RAG app" is a
claim; "recall@5 is 0.94, the system abstains correctly on 4 of 4 unanswerable
questions, p95 latency is 380ms and it costs $0.0004 per question" is engineering.
"""

import json
import time
from pathlib import Path

from rag_assistant.layer0_shared.cache import build_cache
from rag_assistant.layer0_shared.embeddings import build_embedder
from rag_assistant.layer0_shared.llm_client import build_chat_client, build_judge_client
from rag_assistant.layer1_config.settings import settings
from rag_assistant.layer2_models.schemas import AskRequest
from rag_assistant.layer3_storage.factory import build_store, get_store
from rag_assistant.layer7_api.assistant_service import AssistantService
from rag_assistant.layer8_evaluation.answer_metrics import evaluate_one_answer
from rag_assistant.layer8_evaluation.llm_judge import check_the_judge_can_say_no, judge_one_answer
from rag_assistant.layer8_evaluation.retrieval_metrics import average_of, evaluate_one_retrieval

DATASET_PATH = Path(__file__).resolve().parent / "golden_dataset.json"
RESULTS_FOLDER = Path(__file__).resolve().parents[3] / "eval_results"


def load_dataset() -> dict:
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def ordered_unique_sources(passages) -> list[str]:
    """
    The source file names in the order retrieval ranked them, without repeats.

    Retrieval returns chunks; the golden dataset names documents. Collapsing to
    documents in rank order is what lets us compute recall and MRR.
    """
    sources: list[str] = []
    for passage in passages:
        if passage.chunk.source not in sources:
            sources.append(passage.chunk.source)
    return sources


def find_forbidden_sources(store, caller_tags: list[str]) -> list[str]:
    """
    Every document in the knowledge base that this caller may NOT read.

    Used to prove no answer ever cites one of them.
    """
    every_tag = ["public", "internal", "secret"]

    forbidden: list[str] = []
    for summary in store.list_documents(every_tag):
        if summary.access_tag not in caller_tags:
            if summary.source not in forbidden:
                forbidden.append(summary.source)
    return forbidden


def run_evaluation(use_judge: bool = False, k: int = 5) -> dict:
    """Run the whole suite and return the report as a dictionary."""
    dataset = load_dataset()
    store = get_store()

    service = AssistantService(
        store=store,
        embedder=build_embedder(),
        chat_client=build_chat_client(),
        cache=build_cache(),
    )
    judge_client = build_judge_client()

    case_reports: list[dict] = []
    started_all = time.perf_counter()

    for case in dataset["cases"]:
        caller_tags = case["caller_tags"]
        forbidden_sources = find_forbidden_sources(store, caller_tags)

        # ---------- retrieval, measured on its own ----------
        retrieval_started = time.perf_counter()
        outcome = service.retrieve_only(
            question=case["question"],
            allowed_tags=caller_tags,
            top_k=k,
        )
        retrieval_ms = int((time.perf_counter() - retrieval_started) * 1000)

        retrieved_sources = ordered_unique_sources(outcome.passages)
        retrieval_scores = evaluate_one_retrieval(
            retrieved_sources=retrieved_sources,
            relevant_sources=case["relevant_sources"],
            k=k,
        )

        # ---------- the answer, through the real service ----------
        # use_cache is off so every run measures the pipeline, not the cache.
        response = service.ask(
            AskRequest(question=case["question"], top_k=k, use_cache=False),
            allowed_tags=caller_tags,
        )

        answer_scores = evaluate_one_answer(
            response=response,
            must_contain=case["must_contain"],
            should_abstain=case["should_abstain"],
            relevant_sources=case["relevant_sources"],
            forbidden_sources=forbidden_sources,
        )

        # ---------- optional second opinion ----------
        judge_result = {"status": "not requested"}
        if use_judge:
            passage_dicts: list[dict] = []
            for passage in outcome.passages:
                passage_dicts.append(
                    {
                        "title": passage.chunk.document_title,
                        "source": passage.chunk.source,
                        "text": passage.chunk.text,
                    }
                )
            judge_result = judge_one_answer(
                question=case["question"],
                answer_text=response.answer,
                passages=passage_dicts,
                judge_client=judge_client,
            )

        case_reports.append(
            {
                "id": case["id"],
                "question": case["question"],
                "note": case.get("note", ""),
                "caller_tags": caller_tags,
                "expected_sources": case["relevant_sources"],
                "retrieved_sources": retrieved_sources,
                "retrieval": retrieval_scores,
                "retrieval_latency_ms": retrieval_ms,
                "answer": response.answer,
                "answer_scores": answer_scores,
                "usage": response.usage.model_dump(),
                "judge": judge_result,
            }
        )

    # Check the instrument before reading the measurement.
    judge_calibration = {"status": "not requested"}
    if use_judge:
        judge_calibration = check_the_judge_can_say_no(judge_client)

    total_seconds = round(time.perf_counter() - started_all, 2)
    summary = build_summary(case_reports, k=k)
    summary["judge"] = summarise_judge(case_reports, judge_calibration)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "configuration": settings.describe(),
        "cases_run": len(case_reports),
        "total_seconds": total_seconds,
        "judge_used": use_judge,
        "summary": summary,
        "cases": case_reports,
    }

    RESULTS_FOLDER.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_FOLDER / ("eval_%s.json" % time.strftime("%Y%m%d_%H%M%S"))
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    report["saved_to"] = str(output_path)

    return report


def build_summary(case_reports: list[dict], k: int) -> dict:
    """Turn the per-case results into the handful of numbers that matter."""
    recalls: list[float] = []
    precisions: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    groundedness_values: list[float] = []
    citation_precisions: list[float] = []
    latencies: list[float] = []
    costs: list[float] = []
    total_tokens: list[float] = []

    answerable_total = 0
    answerable_correct = 0
    abstain_total = 0
    abstain_correct = 0
    hallucinated = 0
    wrongly_refused = 0
    leaks: list[str] = []

    for case in case_reports:
        retrieval = case["retrieval"]
        answer = case["answer_scores"]
        usage = case["usage"]

        # Retrieval metrics only make sense where something was expected.
        if len(case["expected_sources"]) > 0:
            recalls.append(retrieval["recall_at_k"])
            precisions.append(retrieval["precision_at_k"])
            reciprocal_ranks.append(retrieval["reciprocal_rank"])
            ndcgs.append(retrieval["ndcg_at_k"])

        if answer["should_abstain"]:
            abstain_total = abstain_total + 1
            if answer["abstained_correctly"]:
                abstain_correct = abstain_correct + 1
            if answer["answered_when_it_should_not"]:
                hallucinated = hallucinated + 1
        else:
            answerable_total = answerable_total + 1
            if answer["correct"]:
                answerable_correct = answerable_correct + 1
            if answer["abstained_when_it_should_not"]:
                wrongly_refused = wrongly_refused + 1
            groundedness_values.append(answer["groundedness"])
            citation_precisions.append(answer["citation_precision"])

        for leaked in answer["leaked_forbidden_sources"]:
            if leaked not in leaks:
                leaks.append(leaked)

        latencies.append(usage["latency_ms"])
        costs.append(usage["estimated_cost_usd"])
        total_tokens.append(usage["total_tokens"])

    latencies_sorted = sorted(latencies)
    if len(latencies_sorted) > 0:
        p50_position = int(round(0.50 * (len(latencies_sorted) - 1)))
        p95_position = int(round(0.95 * (len(latencies_sorted) - 1)))
        p50 = latencies_sorted[p50_position]
        p95 = latencies_sorted[p95_position]
    else:
        p50 = 0
        p95 = 0

    total_cost = 0.0
    for cost in costs:
        total_cost = total_cost + cost

    if answerable_total > 0:
        answer_accuracy = round(answerable_correct / answerable_total, 4)
    else:
        answer_accuracy = 0.0

    if abstain_total > 0:
        abstain_accuracy = round(abstain_correct / abstain_total, 4)
    else:
        abstain_accuracy = 0.0

    return {
        "retrieval": {
            "recall_at_%d" % k: average_of(recalls),
            "precision_at_%d" % k: average_of(precisions),
            "mean_reciprocal_rank": average_of(reciprocal_ranks),
            "ndcg_at_%d" % k: average_of(ndcgs),
            "cases_measured": len(recalls),
        },
        "answers": {
            "answer_accuracy": answer_accuracy,
            "answerable_cases": answerable_total,
            "answerable_correct": answerable_correct,
            "wrongly_refused": wrongly_refused,
            "average_groundedness": average_of(groundedness_values),
            "average_citation_precision": average_of(citation_precisions),
        },
        "honesty": {
            "abstain_accuracy": abstain_accuracy,
            "abstain_cases": abstain_total,
            "hallucinated_answers": hallucinated,
            "access_control_leaks": leaks,
            "leak_count": len(leaks),
        },
        "performance": {
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "average_tokens_per_question": average_of(total_tokens),
            "total_cost_usd": round(total_cost, 6),
            "average_cost_per_question_usd": round(average_of(costs), 8),
        },
    }


def summarise_judge(case_reports: list[dict], calibration: dict) -> dict:
    """Average the judge's scores, and report whether the judge is trustworthy."""
    grounded_scores: list[float] = []
    relevant_scores: list[float] = []
    judged = 0

    for case in case_reports:
        verdict = case["judge"]
        if verdict.get("status") != "ok":
            continue
        judged = judged + 1
        grounded_scores.append(verdict["judge_grounded"])
        relevant_scores.append(verdict["judge_relevant"])

    return {
        "cases_judged": judged,
        "average_grounded": average_of(grounded_scores),
        "average_relevant": average_of(relevant_scores),
        "calibration": calibration,
    }


def print_report(report: dict) -> None:
    """Print the report in a form a person can read in ten seconds."""
    summary = report["summary"]

    print("")
    print("=" * 74)
    print(" RAG EVALUATION REPORT")
    print("=" * 74)

    configuration = report["configuration"]
    print(" model        : %s   (live: %s)" % (configuration["llm_model"], configuration["llm_is_live"]))
    print(" embeddings   : %s   (live: %s)" % (configuration["embedding_provider"], configuration["embedding_is_live"]))
    print(" storage      : %s" % configuration["storage_backend"])
    print(" cases run    : %d in %.2fs" % (report["cases_run"], report["total_seconds"]))
    print("")

    retrieval = summary["retrieval"]
    print(" RETRIEVAL  (measured with no answer generated, %d cases)" % retrieval["cases_measured"])
    print("   recall@5             %.3f    <- was the right document found at all" % retrieval["recall_at_5"])
    print("   precision@5          %.3f    <- how much of the context was useful" % retrieval["precision_at_5"])
    print("   MRR                  %.3f    <- how high up the first correct hit was" % retrieval["mean_reciprocal_rank"])
    print("   nDCG@5               %.3f" % retrieval["ndcg_at_5"])
    print("")

    answers = summary["answers"]
    print(" ANSWERS  (%d answerable cases)" % answers["answerable_cases"])
    print("   accuracy             %.3f    <- contained every required fact" % answers["answer_accuracy"])
    print("   groundedness         %.3f    <- every sentence backed by a passage" % answers["average_groundedness"])
    print("   citation precision   %.3f    <- cited the right documents" % answers["average_citation_precision"])
    print("   wrongly refused      %d" % answers["wrongly_refused"])
    print("")

    honesty = summary["honesty"]
    print(" HONESTY  (%d questions that must be refused)" % honesty["abstain_cases"])
    print("   abstain accuracy     %.3f    <- said 'I don't know' when it should" % honesty["abstain_accuracy"])
    print("   hallucinated answers %d        <- answered with no evidence (want 0)" % honesty["hallucinated_answers"])
    print("   access control leaks %d        <- cited a forbidden document (want 0)" % honesty["leak_count"])
    if honesty["leak_count"] > 0:
        print("   LEAKED: %s" % ", ".join(honesty["access_control_leaks"]))
    print("")

    judge = summary.get("judge", {})
    if judge.get("cases_judged", 0) > 0:
        print(" SECOND OPINION FROM THE JUDGE MODEL  (%d cases)" % judge["cases_judged"])
        print("   judged grounded      %.3f" % judge["average_grounded"])
        print("   judged relevant      %.3f" % judge["average_relevant"])

        calibration = judge.get("calibration", {})
        if calibration.get("status") == "ok":
            if calibration["passed"]:
                verdict_text = "TRUSTWORTHY"
            else:
                verdict_text = "NOT TRUSTWORTHY"
            print("   judge calibration    %s - it scored a deliberately unsupported"
                  % verdict_text)
            print("                        answer %.2f (must be at most 0.50)"
                  % calibration["score_given_to_a_deliberately_bad_answer"])
        print("")

    performance = summary["performance"]
    print(" PERFORMANCE AND COST")
    print("   p50 latency          %d ms" % performance["p50_latency_ms"])
    print("   p95 latency          %d ms" % performance["p95_latency_ms"])
    print("   tokens per question  %.0f" % performance["average_tokens_per_question"])
    print("   cost per question    $%.6f" % performance["average_cost_per_question_usd"])
    print("   total run cost       $%.6f" % performance["total_cost_usd"])
    print("")

    print(" FAILURES")
    any_failure = False
    for case in report["cases"]:
        scores = case["answer_scores"]
        if scores["correct"]:
            continue
        any_failure = True
        print("   [%s] %s" % (case["id"], case["question"]))
        if scores["missing_facts"]:
            print("        missing: %s" % ", ".join(scores["missing_facts"]))
        if scores["answered_when_it_should_not"]:
            print("        answered a question it should have refused")
        if scores["abstained_when_it_should_not"]:
            print("        refused a question it should have answered")
        print("        retrieved: %s" % (", ".join(case["retrieved_sources"]) or "nothing"))
        print("        answer: %s" % case["answer"][:110])
    if not any_failure:
        print("   none")
    print("")
    print(" saved to %s" % report.get("saved_to", "-"))
    print("=" * 74)


def main() -> int:
    """
    Refuse to evaluate an empty knowledge base.

    Run against an empty index this suite happily reported that the assistant
    refused every question, scored it, and exited zero - a green run that tested
    nothing except that refusal works when there is nothing to find. The same
    shape of failure has turned up three times across this series, and it is
    always more dangerous than a red run, because a red run gets looked at.

    Seeding is `python scripts/seed_demo.py`, and CI does it before calling this.
    """
    store = build_store()
    chunk_count = store.count_chunks()
    if chunk_count == 0:
        print("The knowledge base is empty, so there is nothing to evaluate.")
        print("Every question would be refused and the result would mean nothing.")
        print()
        print("Load the sample corpus first:")
        print("    python scripts/seed_demo.py")
        return 1

    use_judge = settings.using_real_llm()
    report = run_evaluation(use_judge=use_judge)
    print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
