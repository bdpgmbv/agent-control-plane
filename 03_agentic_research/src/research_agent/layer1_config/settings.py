"""
LAYER 1 - CONFIGURATION
=======================
Every knob, in one place.

Section 2 is the one to read. A research agent decides for itself how much work
to do, which means it decides how much to spend. Those limits are configuration,
enforced in code, and they are shared across every agent in a run.
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
    key: str
    role: str

    def is_admin(self) -> bool:
        return self.role == "admin"


def parse_api_keys(raw: str) -> list[ApiKeyRecord]:
    records: list[ApiKeyRecord] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if entry == "":
            continue
        parts = entry.split(":")
        if len(parts) < 2:
            continue
        records.append(ApiKeyRecord(key=parts[0].strip(), role=parts[1].strip()))
    return records


@dataclass
class Settings:
    # --- 1. the model ---
    llm_provider: str = read_text("LLM_PROVIDER", "offline")
    openai_api_key: str = read_text("OPENAI_API_KEY", "")
    llm_model: str = read_text("LLM_MODEL", "gpt-4o-mini")
    llm_temperature: float = read_float("LLM_TEMPERATURE", 0.0)

    # --- 2. budgets, shared by every agent in a run ---
    budget_max_tokens: int = read_int("BUDGET_MAX_TOKENS", 120000)
    budget_max_tool_calls: int = read_int("BUDGET_MAX_TOOL_CALLS", 40)
    budget_max_seconds: float = read_float("BUDGET_MAX_SECONDS", 120.0)
    budget_max_depth: int = read_int("BUDGET_MAX_DEPTH", 2)
    budget_max_parallel_workers: int = read_int("BUDGET_MAX_PARALLEL_WORKERS", 4)
    budget_max_subquestions: int = read_int("BUDGET_MAX_SUBQUESTIONS", 6)

    # --- 3. research tuning ---
    search_top_k: int = read_int("SEARCH_TOP_K", 5)
    evidence_per_subquestion: int = read_int("EVIDENCE_PER_SUBQUESTION", 4)
    duplicate_similarity_threshold: float = read_float("DUPLICATE_SIMILARITY_THRESHOLD", 0.82)
    min_evidence_relevance: float = read_float("MIN_EVIDENCE_RELEVANCE", 0.25)

    # --- 4. storage ---
    sqlite_path: str = read_text("SQLITE_PATH", "./data/research.db")

    # --- 5. access ---
    require_api_key: bool = read_bool("REQUIRE_API_KEY", False)
    api_keys_raw: str = read_text("API_KEYS", "demo-key:researcher,admin-key:admin")

    def api_keys(self) -> list[ApiKeyRecord]:
        return parse_api_keys(self.api_keys_raw)

    def find_api_key(self, presented: str) -> ApiKeyRecord | None:
        for record in self.api_keys():
            if record.key == presented:
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
        if self.llm_provider != "openai":
            return False
        if self.openai_api_key == "":
            return False
        if self.openai_api_key.startswith("sk-paste"):
            return False
        return True

    def describe(self) -> dict:
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_is_live": self.using_real_llm(),
            "budgets": {
                "max_tokens": self.budget_max_tokens,
                "max_tool_calls": self.budget_max_tool_calls,
                "max_seconds": self.budget_max_seconds,
                "max_depth": self.budget_max_depth,
                "max_parallel_workers": self.budget_max_parallel_workers,
                "max_subquestions": self.budget_max_subquestions,
            },
            "research": {
                "search_top_k": self.search_top_k,
                "evidence_per_subquestion": self.evidence_per_subquestion,
                "duplicate_similarity_threshold": self.duplicate_similarity_threshold,
                "min_evidence_relevance": self.min_evidence_relevance,
            },
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
