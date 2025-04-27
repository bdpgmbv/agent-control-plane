"""
LAYER 2 - DATA SHAPES
=====================
The objects every layer agrees on.

`FileEdit` carries the design of this project. An edit is an exact old string and
an exact new string in one named file - never a whole rewritten file.

That choice is not about elegance. Ask a model to return a corrected version of a
200-line module and it will return something plausible and 180 lines long, having
quietly dropped three functions it did not think were relevant. The loss is
invisible: the file parses, the target test passes, and something else breaks
next week. An exact-match edit cannot lose code it did not mention, and if the
old string is not found the edit fails loudly instead of guessing.
"""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


def now_utc_text() -> str:
    return datetime.now(UTC).isoformat()


class Outcome(str, Enum):
    SOLVED = "solved"                  # the target test passes and nothing broke
    NOT_SOLVED = "not_solved"          # the agent ran out of budget still failing
    REJECTED = "rejected"              # the patch was refused by the verifier
    ERROR = "error"                    # the harness itself failed


class EditStatus(str, Enum):
    APPLIED = "applied"
    NOT_FOUND = "not_found"            # the old string is not in the file
    AMBIGUOUS = "ambiguous"            # it appears more than once
    PROTECTED = "protected"            # the file may not be edited
    OUTSIDE_WORKSPACE = "outside"      # the path escapes the sandbox
    TOO_LARGE = "too_large"
    INVALID = "invalid"                # e.g. old and new are identical
    SYNTAX_ERROR = "syntax_error"      # the result would not parse


class FileEdit(BaseModel):
    """One exact replacement in one file."""

    path: str
    old_text: str
    new_text: str
    reason: str = ""

    def is_deletion(self) -> bool:
        return self.new_text.strip() == ""


class EditResult(BaseModel):
    edit: FileEdit
    status: EditStatus
    message: str = ""
    occurrences: int = 0

    def ok(self) -> bool:
        return self.status == EditStatus.APPLIED


class TestOutcome(str, Enum):
    # pytest collects any class whose name starts with "Test", so it tries to
    # collect these two as test suites and warns that it cannot. They are data
    # shapes about running tests, not tests. Enum ignores dunder names, so this
    # does not become a member.
    __test__ = False

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"                    # collection error, import error
    TIMEOUT = "timeout"


class TestRun(BaseModel):
    """What happened when the suite was run."""

    __test__ = False

    outcome: TestOutcome
    passed: list[str] = Field(default_factory=list)
    failed: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    output: str = ""
    seconds: float = 0.0
    command: str = ""
    return_code: int = 0

    def total(self) -> int:
        return len(self.passed) + len(self.failed) + len(self.errors)

    def is_green(self) -> bool:
        return (self.outcome == TestOutcome.PASSED
                and len(self.failed) == 0
                and len(self.errors) == 0)

    def summary(self) -> str:
        if self.outcome == TestOutcome.TIMEOUT:
            return "timed out after %.0f seconds" % self.seconds
        return "%d passed, %d failed, %d errors" % (
            len(self.passed), len(self.failed), len(self.errors))


class AgentStep(BaseModel):
    """
    One round of the loop, recorded so the whole run can be read back.

    Every step says what the agent looked at, what it changed, and what the
    tests then said. A coding agent that cannot show its working is impossible
    to trust and impossible to debug.
    """

    number: int
    action: str = ""                   # "explore" | "edit" | "test" | "give_up"
    thinking: str = ""                 # the agent's stated reason, in its words
    files_read: list[str] = Field(default_factory=list)
    edits: list[EditResult] = Field(default_factory=list)
    test_run: TestRun | None = None
    seconds: float = 0.0
    tokens: int = 0
    note: str = ""


class VerdictReason(BaseModel):
    code: str
    passed: bool
    detail: str = ""


class Verdict(BaseModel):
    """
    The verifier's answer. Four independent checks, all of which must pass.

    The most important one is `tests_unchanged`. An agent that cannot fix a bug
    can always make the suite green by deleting the assertion, and it will not
    describe that as cheating - it will describe it as fixing the test. The only
    defence is to compare the test files against what they were before and refuse
    the patch if they moved.
    """

    accepted: bool = False
    reasons: list[VerdictReason] = Field(default_factory=list)

    def failed_codes(self) -> list[str]:
        codes = []
        for reason in self.reasons:
            if not reason.passed:
                codes.append(reason.code)
        return codes

    def explain(self) -> str:
        lines = []
        for reason in self.reasons:
            mark = "ok  " if reason.passed else "FAIL"
            lines.append("%s %s: %s" % (mark, reason.code, reason.detail))
        return "\n".join(lines)


class Task(BaseModel):
    """A bug to fix."""

    task_id: str
    title: str
    issue: str                          # what a person would write in a ticket
    repo_path: str = ""
    target_tests: list[str] = Field(default_factory=list)
    hint_paths: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    """Everything one run produced."""

    task_id: str
    title: str = ""
    outcome: Outcome = Outcome.NOT_SOLVED
    summary: str = ""

    steps: list[AgentStep] = Field(default_factory=list)
    verdict: Verdict | None = None
    diff: str = ""
    files_changed: list[str] = Field(default_factory=list)

    # Accepted, but something about the patch is worth a person looking at it.
    # Separate from `outcome` on purpose: "this is wrong" and "this smells" are
    # different claims, and collapsing them would either reject honest work or
    # wave through dishonest work.
    flagged: bool = False

    baseline_run: TestRun | None = None
    final_run: TestRun | None = None

    iterations: int = 0
    model_calls: int = 0
    tokens: int = 0
    cost_usd: float = 0.0
    seconds: float = 0.0
    workspace: str = ""
    offline: bool = True
    created_at: str = Field(default_factory=now_utc_text)

    def solved(self) -> bool:
        return self.outcome == Outcome.SOLVED


class BenchmarkSummary(BaseModel):
    """How a whole run of tasks went."""

    tasks: int = 0
    solved: int = 0
    not_solved: int = 0
    rejected: int = 0
    errors: int = 0

    # The numbers that have to be zero.
    accepted_with_tampered_tests: int = 0
    accepted_with_regressions: int = 0

    # Accepted, but something in the patch is worth a person reading.
    accepted_but_flagged: int = 0

    total_iterations: int = 0
    total_model_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_seconds: float = 0.0
    offline: bool = True

    def resolve_rate(self) -> float:
        if self.tasks == 0:
            return 0.0
        return round(self.solved / self.tasks, 3)
