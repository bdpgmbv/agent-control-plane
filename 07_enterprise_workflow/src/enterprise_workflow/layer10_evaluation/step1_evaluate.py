"""
LAYER 10 - MEASURING THE ENGINE
===============================
Four numbers, in the order they matter.

  1. duplicated side effects        must be zero, and nothing else counts
                                    until it is
  2. runs that ended correctly      including the ones that were meant to fail
  3. cleanup after failure          did a failed run leave anything behind
  4. attempts, cost, wall time      what it costs to run

Number 1 leads for the same reason as in every other project in this series. A
workflow engine that completes 95% of runs and pays one person twice has not
saved anybody anything: somebody now has to reconcile payroll by hand, which is
the work the engine was supposed to remove.

The process-level proof is separate, in scripts/crash_test.py, because it needs
to kill real processes. This suite covers the paths; that script covers the
interruptions.
"""

import time
from dataclasses import dataclass, field

from enterprise_workflow.layer3_database.store import WorkflowStore
from enterprise_workflow.layer5_steps.step2_external import FAILURES
from enterprise_workflow.layer5_steps.step3_onboarding import REGISTRY, WORKFLOWS
from enterprise_workflow.layer6_engine.step1_engine import Engine, EngineSettings
from enterprise_workflow.layer7_recovery.step1_compensate import Compensator
from enterprise_workflow.layer10_evaluation.scenarios import SCENARIOS, Scenario


@dataclass
class ScenarioResult:
    name: str
    description: str = ""
    expected_state: str = ""
    actual_state: str = ""
    ok: bool = False
    problems: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    uncompensated: list[str] = field(default_factory=list)
    attempts: int = 0
    seconds: float = 0.0


@dataclass
class EvaluationReport:
    results: list[ScenarioResult] = field(default_factory=list)
    offline: bool = True
    seconds: float = 0.0

    def passed(self) -> int:
        count = 0
        for result in self.results:
            if result.ok:
                count = count + 1
        return count

    def total_duplicates(self) -> int:
        count = 0
        for result in self.results:
            count = count + len(result.duplicates)
        return count

    def total_uncompensated(self) -> int:
        count = 0
        for result in self.results:
            count = count + len(result.uncompensated)
        return count

    def is_safe(self) -> bool:
        """The one result that cannot be traded against any other."""
        return self.total_duplicates() == 0 and self.total_uncompensated() == 0


def build(offline: bool, client=None):
    store = WorkflowStore(":memory:")
    settings = EngineSettings(retry_base_seconds=0.0, retry_max_seconds=0.0,
                              lease_seconds=30.0, max_attempts=3)
    compensator = Compensator(store, REGISTRY, client, settings)
    engine = Engine(store, REGISTRY, WORKFLOWS, settings, client=client,
                    worker_id="evaluator", compensator=compensator)
    return store, engine


def run_scenario(scenario: Scenario, offline: bool, client=None) -> ScenarioResult:
    FAILURES.reset()
    started = time.time()
    store, engine = build(offline, client)

    result = ScenarioResult(name=scenario.name, description=scenario.description,
                            expected_state=scenario.expected_state)

    run = engine.start("employee_onboarding", scenario.run_input)

    if scenario.cancel_after_steps > 0:
        # Advance a few steps, then cancel, then let it notice.
        for _ in range(scenario.cancel_after_steps):
            engine.tick()
        store.request_cancel(run.run_id)

    engine.run_until_idle()

    if scenario.expect_approval:
        pending = store.list_approvals(run.run_id, pending_only=True)
        if len(pending) == 0:
            result.problems.append("expected the run to park for approval, and it did not")
        elif scenario.approve is not None:
            store.decide_approval(pending[0].approval_id, scenario.approve,
                                  "evaluator", "")
            engine.run_until_idle()

    final = store.get_run(run.run_id)
    result.actual_state = final.state.value if final is not None else "gone"
    result.seconds = round(time.time() - started, 3)

    # ---- the state it ended in ----
    if result.actual_state != scenario.expected_state:
        result.problems.append("ended as %s, expected %s"
                               % (result.actual_state, scenario.expected_state))

    # ---- what happened in the outside world ----
    seen: dict = {}
    for effect in store.list_side_effects(run.run_id):
        result.effects.append(effect.kind)
        seen[effect.idempotency_key] = seen.get(effect.idempotency_key, 0) + 1
        if scenario.expected_state in ("compensated", "cancelled"):
            if not effect.compensated and effect.kind in scenario.expected_compensated:
                result.uncompensated.append(effect.idempotency_key)

    for key, count in seen.items():
        if count > 1:
            result.duplicates.append("%s x%d" % (key, count))

    expected_effects = sorted(scenario.expected_effects)
    if sorted(result.effects) != expected_effects:
        result.problems.append("effects were %s, expected %s"
                               % (sorted(result.effects), expected_effects))

    # ---- was anything retried that should have been ----
    for step in store.get_steps(run.run_id):
        result.attempts = result.attempts + step.attempts
        if scenario.expect_retries_on == step.step_name and step.attempts < 2:
            result.problems.append("%s was expected to be retried and was not"
                                   % step.step_name)
        if scenario.name == "permanent_failure_no_retries" and step.attempts > 1:
            result.problems.append("%s was retried, and a permanent failure "
                                   "should not be" % step.step_name)

    result.ok = (len(result.problems) == 0 and len(result.duplicates) == 0
                 and len(result.uncompensated) == 0)
    store.close()
    return result


def evaluate(offline: bool = True, client=None) -> EvaluationReport:
    started = time.time()
    report = EvaluationReport(offline=offline)
    for scenario in SCENARIOS:
        report.results.append(run_scenario(scenario, offline, client))
    report.seconds = round(time.time() - started, 3)
    return report


def render(report: EvaluationReport) -> str:
    lines = []
    lines.append("=" * 84)
    lines.append("  WORKFLOW ENGINE  -  %s"
                 % ("OFFLINE, no model calls" if report.offline else "LIVE"))
    lines.append("=" * 84)
    lines.append("")

    lines.append("  %-28s %-14s %-14s %s"
                 % ("scenario", "expected", "actually", "notes"))
    lines.append("  " + "-" * 80)
    for result in report.results:
        note = ""
        if len(result.problems) > 0:
            note = result.problems[0][:34]
        elif len(result.duplicates) > 0:
            note = "DUPLICATED " + result.duplicates[0]
        mark = "" if result.ok else "   <-"
        lines.append("  %-28s %-14s %-14s %s%s"
                     % (result.name, result.expected_state, result.actual_state,
                        note, mark))
    lines.append("")

    lines.append("  1. SAFETY")
    if report.is_safe():
        lines.append("     no irreversible effect happened twice")
        lines.append("     no failed run left anything behind")
    else:
        lines.append("     *** %d DUPLICATED EFFECT(S) ***" % report.total_duplicates())
        lines.append("     *** %d THING(S) LEFT BEHIND AFTER A FAILURE ***"
                     % report.total_uncompensated())
    lines.append("")

    lines.append("  2. SCENARIOS          %d of %d ended as they should"
                 % (report.passed(), len(report.results)))
    for result in report.results:
        if result.ok:
            continue
        for problem in result.problems:
            lines.append("       %s: %s" % (result.name, problem))
    lines.append("")

    total_attempts = 0
    for result in report.results:
        total_attempts = total_attempts + result.attempts
    lines.append("  3. WORK               %d step attempts across %d run(s)"
                 % (total_attempts, len(report.results)))
    lines.append("     time               %.2f s total" % report.seconds)
    lines.append("")
    lines.append("  The process-level proof is scripts/crash_test.py, which kills real")
    lines.append("  worker processes. This suite covers the paths; that one covers the")
    lines.append("  interruptions.")
    lines.append("")
    lines.append("=" * 84)
    return "\n".join(lines)
