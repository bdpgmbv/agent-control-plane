"""
LAYER 1 - CONFIGURATION
=======================
Every knob of the support agent, in one place.

Pay attention to section 4. A support agent can move real money, so the limits
that stop it are configuration, not something buried in a prompt. A prompt is a
request; a limit checked in code is a rule.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate)
        break


def read_text(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value == "":
        return default
    return value


def read_int(name: str, default: int) -> int:
    try:
        return int(read_text(name, str(default)))
    except ValueError:
        return default


def read_float(name: str, default: float) -> float:
    try:
        return float(read_text(name, str(default)))
    except ValueError:
        return default


def read_bool(name: str, default: bool) -> bool:
    if default is True:
        raw = read_text(name, "true")
    else:
        raw = read_text(name, "false")
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass
class ApiKeyRecord:
    """Who is talking to the agent, and on whose behalf."""

    key: str
    role: str            # "customer" or "agent_human"
    customer_id: str     # a specific id, or "*" for a human support agent

    def is_human_agent(self) -> bool:
        """Human support staff can see any customer and approve refunds."""
        return self.role == "agent_human"

    def may_act_for(self, customer_id: str) -> bool:
        """
        Can this caller act on behalf of that customer?

        This is the check that stops customer Bob reading customer Alice's order
        by asking nicely. See layer4_tools/base.py for where it is enforced.
        """
        if self.is_human_agent():
            return True
        return self.customer_id == customer_id


def parse_api_keys(raw: str) -> list[ApiKeyRecord]:
    """Parse 'key:role:customer_id,key:role:customer_id'."""
    records: list[ApiKeyRecord] = []
    if raw.strip() == "":
        return records

    for entry in raw.split(","):
        entry = entry.strip()
        if entry == "":
            continue

        parts = entry.split(":")
        if len(parts) < 3:
            continue

        records.append(
            ApiKeyRecord(key=parts[0].strip(), role=parts[1].strip(), customer_id=parts[2].strip())
        )
    return records


@dataclass
class Settings:
    """All configuration for the support agent."""

    # --- 1. the model ---
    llm_provider: str = read_text("LLM_PROVIDER", "offline")
    openai_api_key: str = read_text("OPENAI_API_KEY", "")
    llm_model: str = read_text("LLM_MODEL", "gpt-4o-mini")
    llm_temperature: float = read_float("LLM_TEMPERATURE", 0.0)
    llm_max_output_tokens: int = read_int("LLM_MAX_OUTPUT_TOKENS", 700)

    # --- 2. storage ---
    sqlite_path: str = read_text("SQLITE_PATH", "./data/support.db")

    # --- 3. the agent loop ---
    max_tool_steps: int = read_int("MAX_TOOL_STEPS", 5)
    tool_max_attempts: int = read_int("TOOL_MAX_ATTEMPTS", 3)
    tool_retry_base_seconds: float = read_float("TOOL_RETRY_BASE_SECONDS", 0.2)
    memory_recent_turns: int = read_int("MEMORY_RECENT_TURNS", 8)

    # --- 4. money limits ---
    refund_auto_approve_limit: float = read_float("REFUND_AUTO_APPROVE_LIMIT", 50.00)
    refund_hard_limit: float = read_float("REFUND_HARD_LIMIT", 2000.00)

    # --- 5. escalation ---
    escalate_after_tool_failures: int = read_int("ESCALATE_AFTER_TOOL_FAILURES", 2)
    escalate_after_turns: int = read_int("ESCALATE_AFTER_TURNS", 12)

    # --- 6. privacy ---
    redact_pii_in_logs: bool = read_bool("REDACT_PII_IN_LOGS", True)
    redact_pii_before_model: bool = read_bool("REDACT_PII_BEFORE_MODEL", False)

    # --- 7. access ---
    require_api_key: bool = read_bool("REQUIRE_API_KEY", True)
    api_keys_raw: str = read_text(
        "API_KEYS",
        "cust-alice-key:customer:CUST-1001,cust-bob-key:customer:CUST-1002,human-agent-key:agent_human:*",
    )

    def api_keys(self) -> list[ApiKeyRecord]:
        return parse_api_keys(self.api_keys_raw)

    def find_api_key(self, presented_key: str) -> ApiKeyRecord | None:
        for record in self.api_keys():
            if record.key == presented_key:
                return record
        return None

    def sqlite_file(self) -> Path:
        path = Path(self.sqlite_path)
        if path.is_absolute():
            return path
        # Against the WORKING DIRECTORY, not the package. Resolving against the
        # package is fine from a source checkout and wrong once installed: in a
        # container the package lives in site-packages, so "./data/x.db" became
        # /usr/local/lib/python3.12/data/x.db and the service died on startup
        # trying to create it as a non-root user. The container sets WORKDIR, so
        # a relative path means the right thing in both places.
        return Path.cwd() / path

    def using_real_llm(self) -> bool:
        """True when we will make real network calls to OpenAI."""
        if self.llm_provider != "openai":
            return False
        if self.openai_api_key == "":
            return False
        if self.openai_api_key.startswith("sk-paste"):
            return False
        return True

    def describe(self) -> dict:
        """A safe summary for /api/config. Never includes secrets."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_is_live": self.using_real_llm(),
            "max_tool_steps": self.max_tool_steps,
            "tool_max_attempts": self.tool_max_attempts,
            "refund_auto_approve_limit": self.refund_auto_approve_limit,
            "refund_hard_limit": self.refund_hard_limit,
            "escalate_after_tool_failures": self.escalate_after_tool_failures,
            "escalate_after_turns": self.escalate_after_turns,
            "redact_pii_in_logs": self.redact_pii_in_logs,
            "redact_pii_before_model": self.redact_pii_before_model,
            "require_api_key": self.require_api_key,
        }


settings = Settings()


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
