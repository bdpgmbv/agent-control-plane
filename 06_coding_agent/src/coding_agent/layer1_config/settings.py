"""
LAYER 1 - SETTINGS
==================
Every limit the agent runs under, next to the reason it has that value.

The budget settings are the important ones. An agent is a loop that calls a
model, edits files and runs commands, and the only thing between that and an
unbounded bill is an explicit stopping condition. There are four here -
iterations, tokens, seconds, and a per-test-run timeout - because a loop can run
away in more than one direction: many cheap rounds, one enormous prompt, or a
single test command that hangs forever.
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


def read_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def read_list(name: str, default: str) -> list[str]:
    values = []
    for piece in read_text(name, default).split(","):
        piece = piece.strip()
        if piece != "":
            values.append(piece)
    return values


def resolve_against_project(value: str) -> str:
    """A relative path in .env means "inside the project", not "wherever you ran this"."""
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


class Settings:
    def __init__(self) -> None:
        # ---- 1. the model ----
        self.llm_provider = read_text("LLM_PROVIDER", "offline").lower()
        self.openai_api_key = read_text("OPENAI_API_KEY", "")
        self.llm_model = read_text("LLM_MODEL", "gpt-4o-mini")
        self.llm_temperature = read_number("LLM_TEMPERATURE", 0.0)
        self.request_timeout_seconds = read_number("REQUEST_TIMEOUT_SECONDS", 90.0)

        self.usd_per_1k_input = read_number("USD_PER_1K_INPUT", 0.00015)
        self.usd_per_1k_output = read_number("USD_PER_1K_OUTPUT", 0.00060)

        # ---- 2. the budget ----
        # Four separate limits, because a loop can run away in four directions.
        self.max_iterations = int(read_number("MAX_ITERATIONS", 6))
        self.max_tokens_per_task = int(read_number("MAX_TOKENS_PER_TASK", 120000))
        self.max_seconds_per_task = read_number("MAX_SECONDS_PER_TASK", 300.0)

        # A hanging test command is the one failure that no token budget catches.
        self.test_timeout_seconds = read_number("TEST_TIMEOUT_SECONDS", 60.0)

        # ---- 3. context compression ----
        # The agent cannot read the repository, so it has to choose. These are
        # the numbers that force the choice.
        self.max_files_in_context = int(read_number("MAX_FILES_IN_CONTEXT", 6))
        self.max_file_characters = int(read_number("MAX_FILE_CHARACTERS", 8000))
        self.max_test_output_characters = int(read_number("MAX_TEST_OUTPUT_CHARACTERS", 4000))

        # ---- 4. the sandbox ----
        self.workspace_root = resolve_against_project(read_text("WORKSPACE_ROOT", "./workspaces"))
        self.benchmark_repo = resolve_against_project(read_text("BENCHMARK_REPO", "./benchmark"))
        # The test files, and anything that can change which tests run. A
        # pytest.ini containing `addopts = -k "not test_the_failing_one"` makes
        # the suite green without touching a single assertion, and it is not
        # obviously a cheat until you notice the suite got smaller.
        self.protected_globs = read_list(
            "PROTECTED_GLOBS",
            "tests/*,test_*.py,*_test.py,conftest.py,pytest.ini,setup.cfg,"
            "tox.ini,pyproject.toml",
        )
        self.max_edit_file_bytes = int(read_number("MAX_EDIT_FILE_BYTES", 400000))

        # ---- 5. server ----
        self.host = read_text("HOST", "127.0.0.1")
        self.port = int(read_number("PORT", 8060))

    def is_offline(self) -> bool:
        if self.llm_provider != "openai":
            return True
        return self.openai_api_key == ""

    def describe(self) -> str:
        lines = []
        if self.is_offline():
            lines.append("mode:        OFFLINE (recorded plans, no API calls)")
        else:
            lines.append("mode:        LIVE (%s)" % self.llm_model)
        lines.append("budget:      %d iterations, %d tokens, %.0f seconds"
                     % (self.max_iterations, self.max_tokens_per_task,
                        self.max_seconds_per_task))
        lines.append("test runs:   killed after %.0f seconds" % self.test_timeout_seconds)
        lines.append("context:     at most %d files, %d characters each"
                     % (self.max_files_in_context, self.max_file_characters))
        lines.append("protected:   %s" % ", ".join(self.protected_globs))
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
