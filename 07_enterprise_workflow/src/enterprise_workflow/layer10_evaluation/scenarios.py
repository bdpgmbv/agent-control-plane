"""
LAYER 10 - THE SCENARIOS
========================
Every path the engine can take, written down as an expectation before it is run.

The important column is not "did the run succeed" - several of these are
supposed to fail. It is whether the run ended in the state it SHOULD have, and
whether the irreversible things that happened are the ones that should have
happened. A run that fails and leaves a licence subscription behind has not
failed safely, and "failed" alone does not tell you which kind it was.
"""

from dataclasses import dataclass, field

BASE_REQUEST = """Hiring Ada Lovelace as a backend engineer.
Her email will be ada.lovelace@example.com and she starts 2026-11-03.
Salary is 95000. She will need a headset.
Thanks, Grace Hopper"""

EXPENSIVE_REQUEST = BASE_REQUEST.replace(
    "She will need a headset.", "She will need a laptop, a desk and a chair.")

TODAY = "2026-09-25"


@dataclass
class Scenario:
    name: str
    description: str
    run_input: dict
    expected_state: str
    expected_effects: list[str] = field(default_factory=list)
    expected_compensated: list[str] = field(default_factory=list)
    approve: bool | None = None       # None = no approval expected
    expect_approval: bool = False
    expect_retries_on: str = ""
    cancel_after_steps: int = 0


SCENARIOS = [
    Scenario(
        name="happy_path",
        description="nothing goes wrong",
        run_input={"request_text": BASE_REQUEST, "today_override": TODAY},
        expected_state="succeeded",
        expected_effects=["directory_account", "equipment_order", "licence_order",
                          "payroll_enrolment", "notification"],
    ),
    Scenario(
        name="transient_then_ok",
        description="the licence vendor fails twice, then works",
        run_input={"request_text": BASE_REQUEST, "today_override": TODAY,
                   "fail_licences_times": 2},
        expected_state="succeeded",
        expected_effects=["directory_account", "equipment_order", "licence_order",
                          "payroll_enrolment", "notification"],
        expect_retries_on="provision_licences",
    ),
    Scenario(
        name="gives_up_and_undoes",
        description="payroll never works, so everything before it is undone",
        run_input={"request_text": BASE_REQUEST, "today_override": TODAY,
                   "fail_payroll_times": 99},
        expected_state="compensated",
        expected_effects=["directory_account", "equipment_order", "licence_order"],
        expected_compensated=["directory_account", "equipment_order", "licence_order"],
    ),
    Scenario(
        name="permanent_failure_no_retries",
        description="a start date in the past fails once and is not retried",
        run_input={"request_text": BASE_REQUEST.replace("2026-11-03", "2020-01-06"),
                   "today_override": TODAY},
        expected_state="failed",
        expected_effects=[],
    ),
    Scenario(
        name="approved",
        description="expensive equipment, and a human says yes",
        run_input={"request_text": EXPENSIVE_REQUEST, "today_override": TODAY},
        expected_state="succeeded",
        expect_approval=True,
        approve=True,
        expected_effects=["directory_account", "equipment_order", "licence_order",
                          "payroll_enrolment", "notification"],
    ),
    Scenario(
        name="rejected",
        description="expensive equipment, and a human says no",
        run_input={"request_text": EXPENSIVE_REQUEST, "today_override": TODAY},
        expected_state="compensated",
        expect_approval=True,
        approve=False,
        # The account was already made before the equipment step was reached,
        # so a rejection has to clean it up too.
        expected_effects=["directory_account"],
        expected_compensated=["directory_account"],
    ),
    Scenario(
        name="cancelled",
        description="somebody cancels it halfway through",
        run_input={"request_text": BASE_REQUEST, "today_override": TODAY},
        expected_state="cancelled",
        cancel_after_steps=3,
        # Three steps in, the account already exists. Cancelling does not
        # un-create it; it has to be undone, exactly as a failure would.
        expected_effects=["directory_account"],
        expected_compensated=["directory_account"],
    ),
    Scenario(
        name="empty_request",
        description="there is nothing to read",
        run_input={"request_text": "   ", "today_override": TODAY},
        expected_state="failed",
        expected_effects=[],
    ),
]
