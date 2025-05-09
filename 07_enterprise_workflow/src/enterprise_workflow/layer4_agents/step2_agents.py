"""
LAYER 4, STEP 2 - THE THREE AGENTS
==================================
Three places in this workflow where judgement is needed, and nothing else.

    INTAKE     turn a manager's free-text request into structured fields
    POLICY     explain, in a sentence a human will read, why approval is needed
    DRAFTING   write the welcome note

Everything else - validating, creating, retrying, approving, compensating - is
ordinary code, for the reason project 05 spent a whole project on: a model is
excellent at reading an unstructured sentence and has no business deciding
whether a payment already happened.

Each agent has a deterministic offline path, and the offline path is not a
stub - it is a real, if blunter, implementation. That matters more here than
elsewhere: the engine's recovery behaviour is what this project is about, and
none of it should depend on having a key.

WHAT AN AGENT IS NOT ALLOWED TO DO
----------------------------------
No agent writes to the database, decides whether a step succeeded, or chooses
what runs next. They take text and return text or a dict. The engine reads the
database. That separation is what makes a killed process recoverable: there is
no agent state to lose, because agents have none.
"""

import json
import re
from dataclasses import dataclass, field

from enterprise_workflow.layer4_agents.step1_client import ModelUnavailable

# ---------------------------------------------------------------- intake

INTAKE_SYSTEM = """You read hiring requests and return JSON.

Return exactly these keys:
  full_name     the new joiner's name
  email         their work email if stated, otherwise null
  department    one of: engineering, sales, support, finance - or null
  start_date    ISO format YYYY-MM-DD, or null
  salary        a number, or null
  equipment     a list of items requested, possibly empty
  manager       the requesting manager's name, or null

Copy what is written, with ONE exception: an unambiguous job title may be
mapped to its department, so "backend engineer" is engineering and "account
executive" is sales. A title that could sit in more than one department is null.

Everything else must be copied, not inferred. Do not guess a start date from
"soon", do not invent an email address from the name, and do not assume a salary
from a level. A null is a fact the workflow can act on; a plausible guess is a
wrong record somebody has to find later.

Return only the JSON object."""

INTAKE_USER = """Hiring request:

%s

JSON:"""

# The same rule the prompt states, written once so the two paths cannot drift.
# They already did: the prompt forbade inferring a department from a job title
# while this table did exactly that, so a live run rejected the very request an
# offline run accepted. Two implementations of one contract will disagree
# eventually; the fix is to make the contract explicit in both.
DEPARTMENT_WORDS = {
    "engineering": ["engineer", "engineering", "developer", "backend", "frontend",
                    "platform", "infrastructure", "data scientist", "sre"],
    "sales": ["sales", "account executive", "business development", "bdr"],
    "support": ["support", "customer success", "helpdesk", "service desk"],
    "finance": ["finance", "accountant", "accounts", "payroll", "bookkeeper"],
}

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ISO_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
# A number introduced by the word "salary", which is how people usually
# write it. Tried FIRST, because it is the only form that says what the
# number MEANS - a bare currency amount in a hiring request could just as
# easily be the equipment budget.
LABELLED_SALARY_PATTERN = re.compile(
    r"salary\s*(?:is|:|of|at)?\s*[\u00a3$\u20ac]?\s*([\d][\d,]*(?:\.\d{2})?)",
    re.IGNORECASE)

# A currency-marked or comma-grouped amount anywhere. The fallback: better
# than nothing, and weaker, which is why it is second.
MONEY_PATTERN = re.compile(r"[£$€]\s?([\d,]+(?:\.\d{2})?)|\b(\d{2,3},\d{3})\b")


@dataclass
class IntakeResult:
    fields: dict = field(default_factory=dict)
    source: str = "offline"
    missing: list[str] = field(default_factory=list)
    note: str = ""


def strip_json_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        newline = cleaned.find("\n")
        if newline >= 0:
            cleaned = cleaned[newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def parse_json_object(text: str) -> dict:
    cleaned = strip_json_fence(text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match is None:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


# Capitalised words that start a sentence and are not part of a name. Without
# these, "Hiring Ada Lovelace as a backend engineer" yields a new joiner called
# "Hiring Ada Lovelace" - which then becomes the name on their payroll record.
SENTENCE_STARTERS = {
    "hiring", "hi", "hello", "please", "we", "our", "the", "this", "new",
    "onboard", "onboarding", "welcome", "adding", "add", "start", "starting",
    "thanks", "regards", "dear", "team", "her", "his", "their", "she", "he",
}


def name_from_email(email: str) -> str:
    """
    "ada.lovelace@example.com" -> "Ada Lovelace".

    Tried first because it is the one part of the request with a reliable
    structure. A sentence can be phrased any number of ways; an email local part
    is either dotted or it is not.
    """
    if email in (None, "") or "@" not in email:
        return ""
    local = email.split("@")[0]
    pieces = []
    for piece in re.split(r"[._-]+", local):
        if piece.strip() == "" or piece.isdigit():
            continue
        pieces.append(piece[:1].upper() + piece[1:].lower())
    if len(pieces) < 2:
        return ""
    return " ".join(pieces)


def find_name(request_text: str, email: str | None) -> str | None:
    """The name from the email if possible, otherwise from the prose."""
    from_email = name_from_email(email or "")
    if from_email != "":
        return from_email

    for match in re.finditer(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", request_text):
        words = match.group(1).split()
        # Drop any leading sentence-starter, then see if a name remains.
        while len(words) > 0 and words[0].lower() in SENTENCE_STARTERS:
            words = words[1:]
        if len(words) >= 2:
            return " ".join(words)

    return None


def find_salary(request_text: str) -> float | None:
    """The labelled salary if there is one, otherwise any currency amount."""
    labelled = LABELLED_SALARY_PATTERN.search(request_text)
    if labelled is not None:
        try:
            return float(labelled.group(1).replace(",", ""))
        except ValueError:
            pass

    money = MONEY_PATTERN.search(request_text)
    if money is not None:
        raw = money.group(1) or money.group(2) or ""
        try:
            return float(raw.replace(",", ""))
        except ValueError:
            return None

    return None


def intake_offline(request_text: str) -> IntakeResult:
    """
    Pull what can be pulled with patterns.

    Deliberately conservative. It finds an email, an ISO date, a salary and a
    department word, and leaves everything else null - because a null sends the
    request to a human, and a wrong guess sends somebody the wrong salary.
    """
    lowered = request_text.lower()
    fields: dict = {
        "full_name": None, "email": None, "department": None,
        "start_date": None, "salary": None, "equipment": [], "manager": None,
    }

    email_match = EMAIL_PATTERN.search(request_text)
    if email_match is not None:
        fields["email"] = email_match.group(0)

    date_match = ISO_DATE_PATTERN.search(request_text)
    if date_match is not None:
        fields["start_date"] = date_match.group(1)

    for department, words in DEPARTMENT_WORDS.items():
        for word in words:
            if word in lowered:
                fields["department"] = department
                break
        if fields["department"] is not None:
            break

    fields["salary"] = find_salary(request_text)

    fields["full_name"] = find_name(request_text, fields["email"])

    for item in ("laptop", "monitor", "desk", "chair", "phone", "headset"):
        if item in lowered:
            fields["equipment"].append(item)

    missing = []
    for key in ("full_name", "email", "department", "start_date", "salary"):
        if fields.get(key) in (None, "", []):
            missing.append(key)

    return IntakeResult(fields=fields, source="offline", missing=missing,
                        note="read by pattern matching, with no model")


def run_intake(request_text: str, client) -> IntakeResult:
    if client is None:
        return intake_offline(request_text)

    try:
        reply = client.complete(INTAKE_SYSTEM, INTAKE_USER % request_text, max_tokens=500)
    except ModelUnavailable:
        # The engine decides what a model failure means for the step. The agent
        # just reports that it fell back, so the run is still usable.
        result = intake_offline(request_text)
        result.note = "the model was unavailable, so this was read by pattern matching"
        return result

    parsed = parse_json_object(reply.text)
    if len(parsed) == 0:
        result = intake_offline(request_text)
        result.note = "the model's reply was not JSON, so this was read by pattern matching"
        return result

    fields: dict = {}
    for key in ("full_name", "email", "department", "start_date", "salary",
                "equipment", "manager"):
        fields[key] = parsed.get(key)
    if not isinstance(fields.get("equipment"), list):
        fields["equipment"] = []

    missing = []
    for key in ("full_name", "email", "department", "start_date", "salary"):
        if fields.get(key) in (None, "", []):
            missing.append(key)

    return IntakeResult(fields=fields, source="model", missing=missing,
                        note="read by the model")


# ---------------------------------------------------------------- policy

POLICY_SYSTEM = """You write one sentence for a manager approving a purchase.

Say what is being bought, what it costs, and why it needs approval. No greeting,
no sign-off, no speculation about whether it is reasonable - the manager decides
that. One sentence."""

POLICY_USER = """Approval needed.

Person:     %s
Department: %s
Items:      %s
Total:      %.2f
Limit:      %.2f

One sentence:"""


def run_policy_explanation(name: str, department: str, items: list[str],
                           total: float, limit: float, client) -> str:
    plain = ("%s in %s needs %s costing %.2f, which is above the %.2f that can "
             "be approved automatically."
             % (name, department, ", ".join(items) or "equipment", total, limit))

    if client is None:
        return plain

    try:
        reply = client.complete(
            POLICY_SYSTEM,
            POLICY_USER % (name, department, ", ".join(items) or "equipment", total, limit),
            max_tokens=150,
        )
    except ModelUnavailable:
        return plain

    written = reply.text.strip()
    if written == "":
        return plain
    return written


# ---------------------------------------------------------------- drafting

WELCOME_SYSTEM = """You write a short, warm welcome note for somebody starting a new job.

Three or four sentences. Mention their first day and their team. Do not invent
any detail that is not given to you - no office address, no manager's name you
were not told, no schedule. Sign off as "The People Team"."""

WELCOME_USER = """Write the welcome note.

Name:       %s
Department: %s
Start date: %s
Equipment:  %s"""


def run_welcome_draft(name: str, department: str, start_date: str,
                      equipment: list[str], client) -> str:
    plain = (
        "Hello %s,\n\n"
        "Welcome to the team. You are joining %s and your first day is %s. "
        "Your equipment (%s) will be waiting for you, and your accounts are "
        "already set up.\n\n"
        "The People Team"
        % (name, department, start_date, ", ".join(equipment) or "a laptop")
    )

    if client is None:
        return plain

    try:
        reply = client.complete(
            WELCOME_SYSTEM,
            WELCOME_USER % (name, department, start_date,
                            ", ".join(equipment) or "a laptop"),
            max_tokens=350,
        )
    except ModelUnavailable:
        return plain

    written = reply.text.strip()
    if written == "":
        return plain
    return written
