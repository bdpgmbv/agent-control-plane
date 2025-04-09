"""
LAYER 1 - CONFIGURATION
=======================
Every knob, in one place.

Section 3 is the one that matters. SQL written by a model is executable code
from a source you cannot trust, and those limits are the ones that hold when the
SQL looks perfectly reasonable and is not.
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

    # --- 2. the database ---
    database_backend: str = read_text("DATABASE_BACKEND", "sqlite")
    sqlite_path: str = read_text("SQLITE_PATH", "./data/analytics.db")
    postgres_dsn: str = read_text("POSTGRES_DSN", "postgresql://readonly:readonly@localhost:5434/analytics")

    # --- 3. execution limits ---
    max_rows: int = read_int("MAX_ROWS", 500)
    query_timeout_seconds: float = read_float("QUERY_TIMEOUT_SECONDS", 5.0)
    max_joins: int = read_int("MAX_JOINS", 6)
    max_repair_attempts: int = read_int("MAX_REPAIR_ATTEMPTS", 2)

    # --- 4. schema retrieval ---
    schema_tables_in_prompt: int = read_int("SCHEMA_TABLES_IN_PROMPT", 6)

    # --- 5. access ---
    require_api_key: bool = read_bool("REQUIRE_API_KEY", False)
    api_keys_raw: str = read_text("API_KEYS", "analyst-key:analyst,admin-key:admin")

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
            "database_backend": self.database_backend,
            "limits": {
                "max_rows": self.max_rows,
                "query_timeout_seconds": self.query_timeout_seconds,
                "max_joins": self.max_joins,
                "max_repair_attempts": self.max_repair_attempts,
                "schema_tables_in_prompt": self.schema_tables_in_prompt,
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
