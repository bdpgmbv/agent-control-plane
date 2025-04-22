"""
LAYER 10 - MEASURING THE PIPELINE
=================================
Six numbers, in the order they matter.

  1. errors among auto-approved documents   must be zero, and nothing else
                                            matters until it is
  2. defect catch rate                      of the planted defects, how many fired
  3. false alarm rate                       of the clean documents, how many were
                                            accused of something
  4. classification accuracy                did it know what each document was
  5. field accuracy                         did it read the values correctly
  6. straight-through rate, cost, latency    what it costs to run

Number 1 leads for a reason. A pipeline that auto-approves 90% of documents and
lets one wrong payment through has not saved anybody anything - it has moved the
cost from data entry to the bank, where it is much larger. Straight-through rate
is a benefit; errors among the approved are a liability, and you cannot net them
off against each other.

Number 3 is the one people forget. Every false alarm spends a person's attention
on nothing, and a queue that is mostly nothing trains people to clear it without
looking - which quietly removes the last safeguard in the system.
"""

import time
from dataclasses import dataclass, field

from doc_intelligence.layer2_models.schemas import Decision, ProcessResult
from doc_intelligence.layer10_evaluation.golden import CLEAN_DOCUMENTS, GOLDEN, processing_order


@dataclass
class FieldScore:
    checked: int = 0
    correct: int = 0
    mistakes: list[str] = field(default_factory=list)

    def rate(self) -> float:
        if self.checked == 0:
            return 0.0
        return self.correct / self.checked


@dataclass
class EvaluationReport:
    documents: int = 0

    classification_correct: int = 0
    classification_mistakes: list[str] = field(default_factory=list)

    fields: FieldScore = field(default_factory=FieldScore)
    line_item_counts_correct: int = 0

    defects_expected: int = 0
    defects_caught: int = 0
    defects_missed: list[str] = field(default_factory=list)

    clean_documents: int = 0
    clean_with_false_alarms: int = 0
    false_alarms: list[str] = field(default_factory=list)

    decisions_correct: int = 0
    decision_mistakes: list[str] = field(default_factory=list)

    auto_approved: int = 0
    auto_approved_with_errors: list[str] = field(default_factory=list)

    model_calls: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    offline: bool = True

    def classification_accuracy(self) -> float:
        if self.documents == 0:
            return 0.0
        return self.classification_correct / self.documents

    def defect_catch_rate(self) -> float:
        if self.defects_expected == 0:
            return 1.0
        return self.defects_caught / self.defects_expected

    def false_alarm_rate(self) -> float:
        if self.clean_documents == 0:
            return 0.0
        return self.clean_with_false_alarms / self.clean_documents

    def decision_accuracy(self) -> float:
        if self.documents == 0:
            return 0.0
        return self.decisions_correct / self.documents

    def straight_through_rate(self) -> float:
        if self.documents == 0:
            return 0.0
        return self.auto_approved / self.documents

    def is_safe(self) -> bool:
        """The only pass/fail that cannot be traded away."""
        return len(self.auto_approved_with_errors) == 0


def issue_codes(result: ProcessResult) -> list[str]:
    codes = []
    for issue in result.issues:
        codes.append(issue.code)
    return codes


def score_one(report: EvaluationReport, name: str, expected: dict,
              result: ProcessResult) -> None:
    report.documents = report.documents + 1

    # ---- 1. did it know what this was?
    if result.document_type == expected["type"]:
        report.classification_correct = report.classification_correct + 1
    else:
        report.classification_mistakes.append(
            "%s: called it %s, it is %s"
            % (name, result.document_type.value, expected["type"].value)
        )

    # ---- 2. did it read the values correctly?
    for field_name, wanted in expected["fields"].items():
        report.fields.checked = report.fields.checked + 1
        got = result.value_of(field_name)
        if got == wanted:
            report.fields.correct = report.fields.correct + 1
        else:
            report.fields.mistakes.append(
                "%s.%s: read %r, should be %r" % (name, field_name, got, wanted)
            )

    if len(result.line_items) == expected["line_items"]:
        report.line_item_counts_correct = report.line_item_counts_correct + 1

    # ---- 3. did it catch the planted defects?
    found = issue_codes(result)
    for code in expected["defects"]:
        report.defects_expected = report.defects_expected + 1
        if code in found:
            report.defects_caught = report.defects_caught + 1
        else:
            report.defects_missed.append("%s: %s never fired" % (name, code))

    # ---- 4. did it accuse a clean document of anything?
    if name in CLEAN_DOCUMENTS:
        report.clean_documents = report.clean_documents + 1
        unexpected = []
        for code in found:
            if code not in expected["defects"]:
                unexpected.append(code)
        if len(unexpected) > 0:
            report.clean_with_false_alarms = report.clean_with_false_alarms + 1
            report.false_alarms.append("%s: %s" % (name, ", ".join(unexpected)))

    # ---- 5. did the right thing happen to it?
    if result.decision == expected["decision"]:
        report.decisions_correct = report.decisions_correct + 1
    else:
        report.decision_mistakes.append(
            "%s: %s, should be %s"
            % (name, result.decision.value, expected["decision"].value)
        )

    # ---- 6. the one that must be zero
    if result.decision == Decision.AUTO_APPROVE:
        report.auto_approved = report.auto_approved + 1
        if len(result.errors()) > 0:
            codes = []
            for issue in result.errors():
                codes.append(issue.code)
            report.auto_approved_with_errors.append(
                "%s was approved automatically while carrying: %s"
                % (name, ", ".join(codes))
            )

    report.model_calls = report.model_calls + result.model_calls
    report.cost_usd = report.cost_usd + result.cost_usd
    report.seconds = report.seconds + result.seconds


def evaluate(pipeline, samples_directory) -> EvaluationReport:
    report = EvaluationReport(offline=pipeline.is_offline())
    started = time.time()

    # A clean store, so a leftover document from a previous run cannot make the
    # duplicate check fire on something that is not a duplicate.
    pipeline.store.clear()

    for name in processing_order():
        expected = GOLDEN[name]
        result = pipeline.process_path(str(samples_directory) + "/" + name)
        score_one(report, name, expected, result)

    report.seconds = round(time.time() - started, 3)
    return report


def render(report: EvaluationReport) -> str:
    lines = []
    lines.append("=" * 74)
    lines.append("  EVALUATION  -  %s" % ("OFFLINE, no model calls" if report.offline
                                          else "LIVE"))
    lines.append("=" * 74)
    lines.append("")

    lines.append("  1. SAFETY  (the number nothing else can make up for)")
    if report.is_safe():
        lines.append("     %d documents processed automatically, %d of them carrying an error"
                     % (report.auto_approved, 0))
        lines.append("     -> no document with a validation error was ever auto-approved")
    else:
        lines.append("     *** %d DOCUMENT(S) WITH ERRORS WERE AUTO-APPROVED ***"
                     % len(report.auto_approved_with_errors))
        for line in report.auto_approved_with_errors:
            lines.append("       " + line)
    lines.append("")

    lines.append("  2. DEFECTS CAUGHT     %d of %d   (%.0f%%)"
                 % (report.defects_caught, report.defects_expected,
                    report.defect_catch_rate() * 100))
    for line in report.defects_missed:
        lines.append("       missed: " + line)
    lines.append("")

    lines.append("  3. FALSE ALARMS       %d of %d clean documents   (%.0f%%)"
                 % (report.clean_with_false_alarms, report.clean_documents,
                    report.false_alarm_rate() * 100))
    for line in report.false_alarms:
        lines.append("       " + line)
    lines.append("")

    lines.append("  4. CLASSIFICATION     %d of %d   (%.0f%%)"
                 % (report.classification_correct, report.documents,
                    report.classification_accuracy() * 100))
    for line in report.classification_mistakes:
        lines.append("       " + line)
    lines.append("")

    lines.append("  5. FIELD ACCURACY     %d of %d values   (%.1f%%)"
                 % (report.fields.correct, report.fields.checked,
                    report.fields.rate() * 100))
    lines.append("     line item counts    %d of %d documents"
                 % (report.line_item_counts_correct, report.documents))
    for line in report.fields.mistakes:
        lines.append("       " + line)
    lines.append("")

    lines.append("  6. DECISIONS          %d of %d   (%.0f%%)"
                 % (report.decisions_correct, report.documents,
                    report.decision_accuracy() * 100))
    for line in report.decision_mistakes:
        lines.append("       " + line)
    lines.append("")

    lines.append("     straight through    %d of %d   (%.0f%%)"
                 % (report.auto_approved, report.documents,
                    report.straight_through_rate() * 100))
    lines.append("     model calls         %d across %d documents"
                 % (report.model_calls, report.documents))
    if report.documents > 0:
        lines.append("     cost                $%.6f total, $%.6f per document"
                     % (report.cost_usd, report.cost_usd / report.documents))
        lines.append("     time                %.2f s total, %.3f s per document"
                     % (report.seconds, report.seconds / report.documents))
    lines.append("")
    lines.append("=" * 74)
    return "\n".join(lines)
