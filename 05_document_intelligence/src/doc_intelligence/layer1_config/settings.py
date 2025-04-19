"""
LAYER 1 - SETTINGS
==================
Every threshold in this pipeline lives here, next to the reason it has that
value. The names match .env exactly - nothing is read from the environment
under a name that .env.example does not document.

The important ones are the auto-approval gates. They were not guessed: layer10
measures how many wrong documents slip through at each setting, and the numbers
in the README are the evidence for the values below.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate)
        break


def read_text(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def read_number(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_against_project(value: str) -> str:
    """Turn a relative path from .env into an absolute one under the project."""
    if value == ":memory:":
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    # Against the WORKING DIRECTORY, not the package. Resolving against the
    # package is fine from a source checkout and wrong once installed: in a
    # container the package lives in site-packages, so "./data/x.db" became
    # /usr/local/lib/python3.12/data/x.db and the service died on startup
    # trying to create it as a non-root user. The container sets WORKDIR, so
    # a relative path means the right thing in both places.
    return str((Path.cwd() / path).resolve())


def read_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        # ---- 1. the model ----
        self.llm_provider = read_text("LLM_PROVIDER", "offline").lower()
        self.openai_api_key = read_text("OPENAI_API_KEY", "")
        self.llm_model = read_text("LLM_MODEL", "gpt-4o-mini")
        self.llm_temperature = read_number("LLM_TEMPERATURE", 0.0)
        self.vision_model = read_text("VISION_MODEL", "gpt-4o-mini")
        self.request_timeout_seconds = read_number("REQUEST_TIMEOUT_SECONDS", 40.0)

        # Prices for honest cost reporting. A cost number nobody can trace back
        # to a rate is decoration.
        self.usd_per_1k_input = read_number("USD_PER_1K_INPUT", 0.00015)
        self.usd_per_1k_output = read_number("USD_PER_1K_OUTPUT", 0.00060)

        # ---- 2. storage ----
        # Resolved against the project folder, not the working directory. The
        # server can be started from anywhere - including by a launcher that
        # runs it from a parent folder - and "./data/documents.db" has to mean
        # the same file every time. A database that moves depending on where you
        # launched from loses the duplicate history, which is the one thing in
        # this project that has to persist.
        self.sqlite_path = resolve_against_project(read_text("SQLITE_PATH", "./data/documents.db"))
        self.samples_directory = resolve_against_project(
            read_text("SAMPLES_DIRECTORY", "./samples")
        )

        # ---- 3. when a human must look ----
        # Measured in layer10: at 0.90 no document carrying a real error was ever
        # auto-approved across the corpus. That is the number that matters - a
        # straight-through rate is worthless if wrong invoices ride along with it.
        self.auto_approve_confidence = read_number("AUTO_APPROVE_CONFIDENCE", 0.90)
        self.review_confidence = read_number("REVIEW_CONFIDENCE", 0.60)

        # Confidence is a judgement about reading. A large payment is a fact.
        # Money above this always meets a person, however clean the extraction.
        self.always_review_above_amount = read_number("ALWAYS_REVIEW_ABOVE_AMOUNT", 10000.0)

        # One shaky required field is enough to send the whole document to review.
        # Averaging would let a confident supplier name hide an unreadable total.
        self.min_required_field_confidence = read_number("MIN_REQUIRED_FIELD_CONFIDENCE", 0.70)

        # ---- 4. validation tolerances ----
        # Rounding across many line items can drift a penny. 0.02 absorbs that
        # without absorbing real errors: the smallest genuine error in the
        # corpus is out by 100.00, three orders of magnitude clear of this.
        self.arithmetic_tolerance = read_number("ARITHMETIC_TOLERANCE", 0.02)

        # A percentage tax line is often rounded per item, so it gets slightly
        # more slack than a plain sum of numbers printed on the page.
        self.tax_tolerance = read_number("TAX_TOLERANCE", 0.05)

        self.max_document_age_days = read_number("MAX_DOCUMENT_AGE_DAYS", 1825.0)
        self.max_document_future_days = read_number("MAX_DOCUMENT_FUTURE_DAYS", 30.0)

        # ---- 5. limits ----
        self.max_document_characters = int(read_number("MAX_DOCUMENT_CHARACTERS", 40000))
        self.max_line_items = int(read_number("MAX_LINE_ITEMS", 200))

        # ---- 6. access ----
        self.api_keys_raw = read_text("API_KEYS", "clerk-key:clerk,reviewer-key:reviewer")
        self.require_api_key = read_flag("REQUIRE_API_KEY", False)

        # ---- 7. server ----
        self.host = read_text("HOST", "127.0.0.1")
        self.port = int(read_number("PORT", 8050))

    def is_offline(self) -> bool:
        """
        Offline means no network and no cost. It is the default whenever a key
        is absent, so a fresh clone runs and its tests pass before anyone has
        pasted anything.
        """
        if self.llm_provider != "openai":
            return True
        return self.openai_api_key == ""

    def api_key_roles(self) -> dict:
        """Parse "clerk-key:clerk,reviewer-key:reviewer" into {key: role}."""
        roles = {}
        for pair in self.api_keys_raw.split(","):
            pair = pair.strip()
            if pair == "":
                continue
            if ":" not in pair:
                continue
            key, role = pair.split(":", 1)
            roles[key.strip()] = role.strip()
        return roles

    def describe(self) -> str:
        lines = []
        if self.is_offline():
            lines.append("mode:             OFFLINE (deterministic stand-in, no API calls, no cost)")
        else:
            lines.append("mode:             LIVE (%s)" % self.llm_model)
        lines.append("auto-approve:     confidence >= %.2f, no errors, every required field >= %.2f"
                     % (self.auto_approve_confidence, self.min_required_field_confidence))
        lines.append("always review:    totals above %.2f" % self.always_review_above_amount)
        lines.append("arithmetic:        checked to +/- %.2f" % self.arithmetic_tolerance)
        lines.append("api key required: %s" % self.require_api_key)
        return "\n".join(lines)


SETTINGS = Settings()


def find_beside_project(name: str) -> Path:
    """
    A folder shipped ALONGSIDE the package - ui, samples, benchmark.

    The working directory first, because that is where the container puts them
    (WORKDIR /srv, then COPY ui ./ui). Falls back to the source tree, so a
    checkout still works however you started it.
    """
    candidates = [Path.cwd() / name, PROJECT_ROOT / name]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]
