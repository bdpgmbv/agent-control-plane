"""
LAYER 9 - EVALUATION: WHAT IS CHECKED
=====================================
A research report is graded on four different things, and they are not equally
important.

  DID IT FIND THE RIGHT SOURCES
      Source recall and precision against the documents that actually answer the
      question. Scored as a percentage - this is ordinary quality work.

  DID IT NOTICE THE DISAGREEMENTS
      The corpus has planted conflicts. Missing one means the report reads as
      more settled than the evidence is.

  IS EVERY CLAIM REAL
      CRITICAL. Every quote must appear in the document it is attributed to, and
      every figure must appear in the evidence. A report that invents a quote is
      not a slightly worse report; it is worse than no report, because it looks
      sourced.

  IS IT HONEST ABOUT WHAT IT DID NOT DO
      CRITICAL. A run that hit a budget limit must come back marked partial with
      its gaps listed. A partial report that looks complete is the failure that
      makes the whole system untrustworthy.

Critical checks are counted, not scored. The target is zero failures.
"""

from research_agent.layer5_workers.step2_extract_evidence import quote_is_in_document
from research_agent.layer7_synthesis.step2_verify_report import (
    numbers_are_supported,
)


class CheckOutcome:
    def __init__(self, name: str, passed: bool, detail: str = "", critical: bool = False) -> None:
        self.name = name
        self.passed = passed
        self.detail = detail
        self.critical = critical

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "critical": self.critical}


def cited_document_ids(response) -> list[str]:
    """Which corpus documents the report actually cites."""
    document_ids: list[str] = []

    for _citation in response.report.citations:
        # The citation carries the evidence id; find the document behind it.
        for _section in response.report.sections:
            pass

    # The citation list holds the url and title, but the document id lives on the
    # evidence. Matching on title is exact here because titles are unique in the
    # corpus, and it avoids threading evidence objects through the API response.
    from research_agent.layer3_sources.registry import get_sources

    title_to_id: dict[str, str] = {}
    for source in get_sources():
        for document in getattr(source, "documents", []):
            title_to_id[document.title] = document.document_id

    for citation in response.report.citations:
        document_id = title_to_id.get(citation.title, "")
        if document_id != "" and document_id not in document_ids:
            document_ids.append(document_id)

    return document_ids


def check_quotes_are_real(response) -> CheckOutcome:
    """
    THE MOST IMPORTANT CHECK IN THIS SUITE.

    Every quote in the finished report is looked up in the document it claims to
    come from. A quote that is not there means the citation is decoration.
    """
    from research_agent.layer3_sources.registry import get_sources

    body_by_title: dict[str, str] = {}
    for source in get_sources():
        for document in getattr(source, "documents", []):
            body_by_title[document.title] = document.body

    invented: list[str] = []
    for citation in response.report.citations:
        body = body_by_title.get(citation.title, "")
        if body == "":
            invented.append("[%d] cites a document that does not exist" % citation.marker)
            continue
        if not quote_is_in_document(citation.quote, body):
            invented.append("[%d] quote is not in %s" % (citation.marker, citation.title[:40]))

    return CheckOutcome(
        name="every_quote_is_real",
        passed=len(invented) == 0,
        detail="; ".join(invented),
        critical=True,
    )


def check_figures_are_supported(response) -> CheckOutcome:
    """Every number in the report must appear in the evidence."""
    allowed: list[float] = []
    for citation in response.report.citations:
        from research_agent.layer0_shared.text_tools import extract_numbers

        for number in extract_numbers(citation.quote):
            allowed.append(number)
        for number in extract_numbers(citation.published_date):
            allowed.append(number)

    # A separate name, because these are the figures that could NOT be
    # supported - strings quoted back to the reader, not floats.
    unsupported: list[str] = []
    for figure in numbers_are_supported(response.report.summary, allowed):
        unsupported.append(figure)
    for section in response.report.sections:
        for figure in numbers_are_supported(section.findings, allowed):
            if figure not in unsupported:
                unsupported.append(figure)

    return CheckOutcome(
        name="every_figure_is_in_the_evidence",
        passed=len(unsupported) == 0,
        detail="not in any source: " + ", ".join(unsupported),
        critical=True,
    )


def check_budget_was_respected(response) -> CheckOutcome:
    """The run must never exceed a limit it was given."""
    budget = response.trace.budget
    problems: list[str] = []

    if budget["tokens"]["spent"] > budget["tokens"]["limit"]:
        problems.append(
            "spent %d tokens against a limit of %d"
            % (budget["tokens"]["spent"], budget["tokens"]["limit"])
        )
    if budget["tool_calls"]["made"] > budget["tool_calls"]["limit"]:
        problems.append(
            "made %d tool calls against a limit of %d"
            % (budget["tool_calls"]["made"], budget["tool_calls"]["limit"])
        )

    return CheckOutcome(
        name="budget_was_respected",
        passed=len(problems) == 0,
        detail="; ".join(problems),
        critical=True,
    )


def check_partial_is_declared(response, expected_partial: bool) -> CheckOutcome:
    """
    A run that hit a limit must say so.

    This is the honesty check. A partial report that presents itself as complete
    is worse than a failed run, because the reader acts on it.
    """
    budget = response.trace.budget
    hit_a_limit = budget["stopped_because"] != "none"
    declared = response.report.partial

    if hit_a_limit and not declared:
        return CheckOutcome(
            name="partial_is_declared",
            passed=False,
            detail="the run stopped on the %s limit but the report is not marked partial"
            % budget["stopped_because"],
            critical=True,
        )

    return CheckOutcome(
        name="partial_is_declared",
        passed=declared == expected_partial,
        detail="partial=%s, expected %s" % (declared, expected_partial),
        critical=True,
    )


def check_question(expectation: dict, response) -> list[CheckOutcome]:
    """Run every check this question asks for, plus the ones that always run."""
    checks: list[CheckOutcome] = []

    # --- always ---
    checks.append(check_quotes_are_real(response))
    checks.append(check_figures_are_supported(response))
    checks.append(check_budget_was_respected(response))

    if "expect_partial" in expectation:
        checks.append(check_partial_is_declared(response, expectation["expect_partial"]))

    cited = cited_document_ids(response)

    # --- source recall ---
    if "expect_sources" in expectation and len(expectation["expect_sources"]) > 0:
        wanted = expectation["expect_sources"]

        found = 0
        missing: list[str] = []
        for document_id in wanted:
            if document_id in cited:
                found = found + 1
            else:
                missing.append(document_id)

        recall = found / len(wanted)
        minimum = expectation.get("expect_minimum_source_recall", 0.5)

        checks.append(
            CheckOutcome(
                name="source_recall",
                passed=recall >= minimum,
                detail="recall %.2f (wanted at least %.2f), missing %s" % (recall, minimum, missing),
            )
        )

    # --- source precision: irrelevant documents must not be cited ---
    if "forbid_sources" in expectation:
        wrongly_cited: list[str] = []
        for document_id in expectation["forbid_sources"]:
            if document_id in cited:
                wrongly_cited.append(document_id)

        checks.append(
            CheckOutcome(
                name="irrelevant_sources_avoided",
                passed=len(wrongly_cited) == 0,
                detail="cited unrelated documents: %s" % wrongly_cited,
            )
        )

    # --- citation count ---
    if "expect_minimum_citations" in expectation:
        wanted = expectation["expect_minimum_citations"]
        checks.append(
            CheckOutcome(
                name="enough_citations",
                passed=len(response.report.citations) >= wanted,
                detail="%d citations, wanted at least %d" % (len(response.report.citations), wanted),
            )
        )

    if "expect_maximum_citations" in expectation:
        wanted = expectation["expect_maximum_citations"]
        checks.append(
            CheckOutcome(
                name="did_not_over_cite",
                passed=len(response.report.citations) <= wanted,
                detail="%d citations, wanted at most %d" % (len(response.report.citations), wanted),
                critical=True,
            )
        )

    # --- conflicts ---
    if expectation.get("expect_conflict") is True:
        checks.append(
            CheckOutcome(
                name="disagreement_was_surfaced",
                passed=len(response.report.conflicts) > 0,
                detail="the corpus contains a planted disagreement for this question and none was reported",
            )
        )

    # --- honesty about an unanswerable question ---
    if expectation.get("expect_low_confidence") is True:
        checks.append(
            CheckOutcome(
                name="low_confidence_when_nothing_was_found",
                passed=response.report.confidence <= 0.6,
                detail="confidence %.2f on a question the corpus cannot answer" % response.report.confidence,
                critical=True,
            )
        )

    # --- budget-limited runs ---
    if expectation.get("expect_gaps") is True:
        checks.append(
            CheckOutcome(
                name="gaps_are_listed",
                passed=len(response.report.gaps) > 0,
                detail="the run was cut short but listed no gaps",
                critical=True,
            )
        )

    if expectation.get("expect_report_anyway") is True:
        checks.append(
            CheckOutcome(
                name="still_produced_a_report",
                passed=len(response.report.summary.strip()) > 40,
                detail="running out of budget produced no report at all",
                critical=True,
            )
        )

    return checks
