"""
LAYER 5, STEP 2 - THE SYSTEMS THIS WORKFLOW TALKS TO
====================================================
Stand-ins for an identity provider, a licence vendor and a payroll system.

They are simulated, but not simplified in the way that matters: each one can be
told to fail a given number of times before succeeding, and the instruction
comes from the workflow's own input. That is what makes every recovery path in
this project testable and repeatable -

    {"fail_licences_times": 2}   the licence vendor fails twice, then works
    {"fail_payroll_times": 99}   payroll never works, forcing compensation

- rather than something you can only observe by waiting for a real outage.

None of them are idempotent. That is deliberate and it is the point: real
systems mostly are not, so the idempotency has to live in the step and the
database rather than being assumed of the service.
"""

import hashlib


class ExternalServiceError(Exception):
    """The service was reachable but refused, or was not reachable at all."""

    def __init__(self, message: str, transient: bool = True) -> None:
        super().__init__(message)
        self.transient = transient


def deterministic_id(prefix: str, seed: str) -> str:
    """
    An id derived from the input, not from a clock or a counter.

    A real service would return whatever id it liked. This one derives it, so
    that a test can assert the SAME id comes back after a crash and a resume -
    which is how you tell resumption from quiet duplication.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()
    return "%s-%s" % (prefix, digest)


class FailureBudget:
    """
    Counts how many times each service has been asked to fail.

    Held in memory on purpose. A process restart resets it, which is exactly
    what a real transient outage looks like from the workflow's point of view:
    the thing that was broken is working again, and nothing in the database
    should record that it was ever otherwise.
    """

    def __init__(self) -> None:
        self.used: dict[str, int] = {}

    def should_fail(self, key: str, budget: int) -> bool:
        if budget <= 0:
            return False
        used = self.used.get(key, 0)
        if used >= budget:
            return False
        self.used[key] = used + 1
        return True

    def reset(self) -> None:
        self.used = {}


FAILURES = FailureBudget()


class IdentityService:
    """Creates and deletes user accounts."""

    def create_account(self, email: str, full_name: str, fail_times: int = 0) -> dict:
        if FAILURES.should_fail("identity:" + email, fail_times):
            raise ExternalServiceError(
                "the identity provider did not respond in time", transient=True)

        if "@" not in email:
            raise ExternalServiceError(
                "%r is not an email address the directory will accept" % email,
                transient=False)

        return {
            "account_id": deterministic_id("ACC", email),
            "email": email,
            "full_name": full_name,
        }

    def delete_account(self, account_id: str) -> str:
        return "deleted directory account %s" % account_id


class LicenceService:
    """Buys and releases software licences. Each one costs money."""

    CATALOGUE = {
        "engineering": ["ide", "ci", "design"],
        "sales": ["crm", "dialer"],
        "support": ["helpdesk", "crm"],
        "finance": ["ledger", "reporting"],
    }
    PRICE_EACH = 40.0

    def licences_for(self, department: str) -> list[str]:
        return list(self.CATALOGUE.get(department, ["email"]))

    def provision(self, account_id: str, licences: list[str], fail_times: int = 0) -> dict:
        if FAILURES.should_fail("licence:" + account_id, fail_times):
            raise ExternalServiceError(
                "the licence vendor returned 503 Service Unavailable", transient=True)

        return {
            "order_id": deterministic_id("LIC", account_id + ",".join(sorted(licences))),
            "licences": licences,
            "monthly_cost": round(len(licences) * self.PRICE_EACH, 2),
        }

    def release(self, order_id: str) -> str:
        return "released licence order %s" % order_id


class PayrollService:
    """
    Enrols somebody for pay.

    The one service in this workflow where a duplicate is not an untidiness but
    a second salary. It is the reason the side_effects table exists.
    """

    def enrol(self, account_id: str, salary: float, start_date: str,
              fail_times: int = 0) -> dict:
        if FAILURES.should_fail("payroll:" + account_id, fail_times):
            raise ExternalServiceError(
                "payroll is in end-of-month close and is refusing writes",
                transient=True)

        if salary <= 0:
            raise ExternalServiceError(
                "payroll will not accept a salary of %.2f" % salary, transient=False)

        return {
            "payroll_id": deterministic_id("PAY", account_id),
            "salary": salary,
            "start_date": start_date,
        }

    def remove(self, payroll_id: str) -> str:
        return "removed payroll enrolment %s" % payroll_id


IDENTITY = IdentityService()
LICENCES = LicenceService()
PAYROLL = PayrollService()
