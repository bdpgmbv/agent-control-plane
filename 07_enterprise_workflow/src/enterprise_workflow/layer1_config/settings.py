"""
LAYER 1 - SETTINGS
==================
Every limit the engine runs under, next to the reason it has that value.

The lease is the one worth reading twice. A worker claims a step for
LEASE_SECONDS; if it dies, the lease expires and another worker may take over.
Set it too short and a slow-but-healthy step gets taken off a worker that is
still running it, so the step runs twice. Set it too long and a crash stalls the
workflow until the lease expires.

There is no value that avoids both. That is not a flaw in the setting - it is
the actual shape of the problem, and it is why every step that touches the
outside world has to be idempotent regardless of what the lease is set to.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Where the package happens to be installed. Useful for finding a .env next to
# a source checkout, and NOT a place to write anything: when the package is
# installed rather than run from source this is inside site-packages.
PACKAGE_ROOT = Path(__file__).resolve().parents[3]

# .env from wherever you are first, then next to the source. Neither is
# required - every default below works without one.
for candidate in (Path.cwd() / ".env", PACKAGE_ROOT / ".env"):
    if candidate.is_file():
        load_dotenv(candidate)
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


def read_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def resolve_writable_path(value: str) -> str:
    """
    Turn a relative path from .env into an absolute one.

    Against the WORKING DIRECTORY, not the package. The obvious version resolved
    against the package location, which is fine from a source checkout and wrong
    everywhere else: installed in a container the package lives in
    site-packages, so "./data/workflows.db" became
    /usr/local/lib/python3.12/data/workflows.db and the service died on startup
    trying to create it as a non-root user.

    The container sets WORKDIR, so "./data" means /srv/data there and the
    project folder here, which is what both of them meant all along.
    """
    if value == ":memory:":
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((Path.cwd() / path).resolve())


class Settings:
    def __init__(self) -> None:
        # ---- 1. the model ----
        self.llm_provider = read_text("LLM_PROVIDER", "offline").lower()
        self.openai_api_key = read_text("OPENAI_API_KEY", "")
        self.llm_model = read_text("LLM_MODEL", "gpt-4o-mini")
        self.llm_temperature = read_number("LLM_TEMPERATURE", 0.0)
        self.request_timeout_seconds = read_number("REQUEST_TIMEOUT_SECONDS", 40.0)

        self.usd_per_1k_input = read_number("USD_PER_1K_INPUT", 0.00015)
        self.usd_per_1k_output = read_number("USD_PER_1K_OUTPUT", 0.00060)

        # ---- 2. where the truth lives ----
        self.sqlite_path = resolve_writable_path(read_text("SQLITE_PATH", "./data/workflows.db"))

        # ---- 3. leases ----
        self.lease_seconds = read_number("LEASE_SECONDS", 30.0)
        self.poll_seconds = read_number("POLL_SECONDS", 0.5)

        # ---- 4. retries ----
        self.max_attempts = int(read_number("MAX_ATTEMPTS", 3))
        self.retry_base_seconds = read_number("RETRY_BASE_SECONDS", 1.0)
        self.retry_max_seconds = read_number("RETRY_MAX_SECONDS", 30.0)

        # ---- 5. approvals ----
        self.approval_required_above = read_number("APPROVAL_REQUIRED_ABOVE", 2000.0)
        self.approval_expiry_hours = read_number("APPROVAL_EXPIRY_HOURS", 72.0)

        # ---- 6. server ----
        self.host = read_text("HOST", "127.0.0.1")
        self.port = int(read_number("PORT", 8070))

    def is_offline(self) -> bool:
        if self.llm_provider != "openai":
            return True
        return self.openai_api_key == ""

    def describe(self) -> str:
        lines = []
        if self.is_offline():
            lines.append("mode:      OFFLINE (deterministic stand-ins, no API calls)")
        else:
            lines.append("mode:      LIVE (%s)" % self.llm_model)
        lines.append("database:  %s" % self.sqlite_path)
        lines.append("lease:     %.0fs, then another worker may take the step"
                     % self.lease_seconds)
        lines.append("retries:   %d attempts, %.0fs backoff up to %.0fs"
                     % (self.max_attempts, self.retry_base_seconds, self.retry_max_seconds))
        lines.append("approval:  needed above %.2f" % self.approval_required_above)
        return "\n".join(lines)


SETTINGS = Settings()
