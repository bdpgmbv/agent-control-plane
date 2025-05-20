"""
LAYER 1 - SETTINGS
==================
Every limit the gateway runs under, next to the reason it has that value.

Two of these are worth reading twice.

SEMANTIC_THRESHOLD decides when two questions count as the same question. Set it
too high and the semantic cache never hits, which costs money. Set it too low
and the gateway confidently answers a question nobody asked - which costs
trust, and is much harder to notice. Project 01 measured this rather than
guessing; `scripts/tune_threshold.py` here does the same.

CONFIDENCE_LEVEL decides when the platform is willing to call an A/B test.
0.95 is conventional rather than correct, and the point of having it as a
setting is that "how sure do we need to be" is a decision somebody should make
on purpose.
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


def resolve_writable_path(value: str) -> str:
    """
    Relative paths resolve against the WORKING DIRECTORY, not the package.

    The package lives in site-packages once installed, so resolving against it
    puts the database inside the Python installation - which fails as a non-root
    user in a container. The container sets WORKDIR, so a relative path means
    the right thing in both places.
    """
    if value == ":memory:":
        return value
    path = Path(value)
    if path.is_absolute():
        return str(path)
    return str((Path.cwd() / path).resolve())


def find_beside_project(name: str) -> Path:
    """A folder shipped alongside the package - ui, datasets."""
    candidates = [Path.cwd() / name, PROJECT_ROOT / name]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0]


class Settings:
    def __init__(self) -> None:
        # ---- 1. providers ----
        self.llm_provider = read_text("LLM_PROVIDER", "offline").lower()
        self.openai_api_key = read_text("OPENAI_API_KEY", "")
        self.openai_timeout_seconds = read_number("OPENAI_TIMEOUT_SECONDS", 40.0)

        # ---- 2. storage ----
        self.sqlite_path = resolve_writable_path(read_text("SQLITE_PATH", "./data/gateway.db"))

        # ---- 3. caching ----
        self.cache_enabled = read_flag("CACHE_ENABLED", True)
        self.cache_ttl_seconds = read_number("CACHE_TTL_SECONDS", 3600.0)
        self.semantic_cache_enabled = read_flag("SEMANTIC_CACHE_ENABLED", True)
        # A threshold is a property of the EMBEDDING SPACE, not of the gateway.
        # Real embeddings cluster high, so 0.93 separates meaning from
        # coincidence. The offline hashing embedder scores lower across the
        # board and separates at about 0.71 - measured by
        # scripts/tune_threshold.py, which reports both.
        #
        # One number for both would be wrong for one of them: 0.93 offline
        # means the semantic cache never hits, and 0.71 live means it hits on
        # questions that merely share a topic. An explicit SEMANTIC_THRESHOLD
        # in .env overrides whichever default applies.
        # 0.88, not the 0.93 this started with. Measured: the lowest-scoring
        # pair that SHOULD match came in at 0.9187, so 0.93 was quietly
        # rejecting a legitimate match and paying for the answer again. The
        # highest-scoring pair that should NOT match was 0.8431, so 0.88 sits
        # in the gap with room on both sides.
        self.semantic_threshold_live = read_number("SEMANTIC_THRESHOLD", 0.88)
        self.semantic_threshold_offline = read_number("SEMANTIC_THRESHOLD_OFFLINE", 0.71)

        # ---- 4. limits ----
        self.requests_per_minute = read_number("REQUESTS_PER_MINUTE", 60.0)
        self.burst = read_number("BURST", 10.0)
        self.daily_budget_usd = read_number("DAILY_BUDGET_USD", 5.0)

        # ---- 5. retries and fallback ----
        self.max_attempts_per_provider = int(read_number("MAX_ATTEMPTS_PER_PROVIDER", 2))
        self.retry_base_seconds = read_number("RETRY_BASE_SECONDS", 0.5)

        # ---- 6. experiments ----
        self.confidence_level = read_number("CONFIDENCE_LEVEL", 0.95)
        self.minimum_samples_per_arm = int(read_number("MINIMUM_SAMPLES_PER_ARM", 30))

        # ---- 7. access ----
        self.api_keys_raw = read_text("API_KEYS", "demo-key:demo,team-key:team")

        # ---- 8. server ----
        self.host = read_text("HOST", "127.0.0.1")
        self.port = int(read_number("PORT", 8080))

    def is_offline(self) -> bool:
        if self.llm_provider != "openai":
            return True
        return self.openai_api_key == ""

    def semantic_threshold(self) -> float:
        """The threshold for whichever embedder is actually in use."""
        if self.is_offline():
            return self.semantic_threshold_offline
        return self.semantic_threshold_live

    def api_key_owners(self) -> dict:
        """Parse "demo-key:demo,team-key:team" into {key: owner}."""
        owners = {}
        for pair in self.api_keys_raw.split(","):
            pair = pair.strip()
            if pair == "" or ":" not in pair:
                continue
            key, owner = pair.split(":", 1)
            owners[key.strip()] = owner.strip()
        return owners

    def describe(self) -> str:
        lines = []
        if self.is_offline():
            lines.append("mode:       OFFLINE (deterministic providers, no API calls)")
        else:
            lines.append("mode:       LIVE (a real OpenAI key is present)")
        lines.append("database:   %s" % self.sqlite_path)
        lines.append("cache:      exact %s, semantic %s above %.2f"
                     % ("on" if self.cache_enabled else "off",
                        "on" if self.semantic_cache_enabled else "off",
                        self.semantic_threshold()))
        lines.append("limits:     %.0f/min, burst %.0f, $%.2f per key per day"
                     % (self.requests_per_minute, self.burst, self.daily_budget_usd))
        lines.append("experiments: %.0f%% confidence, at least %d samples per arm"
                     % (self.confidence_level * 100, self.minimum_samples_per_arm))
        return "\n".join(lines)


SETTINGS = Settings()
