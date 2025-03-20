"""
LAYER 1 - CONFIGURATION
=======================
Every knob of the system lives here, and nowhere else.

Why this is its own layer:
    If settings are scattered through the code, you cannot tell what the system
    depends on, and you cannot change behaviour without editing logic.
    One settings object = one place to look.

Read from: .env file (see .env.example)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load the .env file that sits next to this project, if it exists.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
for _candidate in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
    if _candidate.is_file():
        load_dotenv(_candidate)
        break


def read_text(name: str, default: str) -> str:
    """Read one environment variable as text."""
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    if value == "":
        return default
    return value


def read_int(name: str, default: int) -> int:
    """Read one environment variable as a whole number."""
    raw = read_text(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def read_float(name: str, default: float) -> float:
    """Read one environment variable as a decimal number."""
    raw = read_text(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def read_bool(name: str, default: bool) -> bool:
    """Read one environment variable as true/false."""
    if default is True:
        raw = read_text(name, "true")
    else:
        raw = read_text(name, "false")
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass
class ApiKeyRecord:
    """One API key, who it belongs to, and what it is allowed to see."""

    key: str
    role: str
    allowed_tags: list[str] = field(default_factory=list)

    def may_write(self) -> bool:
        """Only admins may add or delete documents."""
        return self.role == "admin"


def parse_api_keys(raw: str) -> list[ApiKeyRecord]:
    """
    Turn the API_KEYS environment string into a list of records.

    Input format:
        key1:role:tagA|tagB,key2:role:tagC

    Example:
        demo-admin-key:admin:public|internal
    """
    records: list[ApiKeyRecord] = []
    if raw.strip() == "":
        return records

    for entry in raw.split(","):
        entry = entry.strip()
        if entry == "":
            continue

        parts = entry.split(":")
        if len(parts) < 2:
            # Not enough information to be useful, skip it.
            continue

        key = parts[0].strip()
        role = parts[1].strip()

        tags: list[str] = []
        if len(parts) >= 3:
            for tag in parts[2].split("|"):
                tag = tag.strip()
                if tag != "":
                    tags.append(tag)

        records.append(ApiKeyRecord(key=key, role=role, allowed_tags=tags))

    return records


@dataclass
class Settings:
    """All configuration for the RAG assistant."""

    # --- 1. LLM provider ---
    llm_provider: str = read_text("LLM_PROVIDER", "offline")
    openai_api_key: str = read_text("OPENAI_API_KEY", "")
    llm_model: str = read_text("LLM_MODEL", "gpt-4o-mini")
    judge_model: str = read_text("JUDGE_MODEL", "gpt-4o-mini")
    llm_temperature: float = read_float("LLM_TEMPERATURE", 0.0)
    llm_max_output_tokens: int = read_int("LLM_MAX_OUTPUT_TOKENS", 900)

    # --- 2. Embeddings ---
    embedding_provider: str = read_text("EMBEDDING_PROVIDER", "offline")
    embedding_model: str = read_text("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dimensions: int = read_int("EMBEDDING_DIMENSIONS", 1536)

    # --- 3. Storage ---
    storage_backend: str = read_text("STORAGE_BACKEND", "sqlite")
    sqlite_path: str = read_text("SQLITE_PATH", "./data/rag.db")
    postgres_dsn: str = read_text("POSTGRES_DSN", "postgresql://rag:rag@localhost:5433/rag")

    # --- 4. Cache ---
    cache_backend: str = read_text("CACHE_BACKEND", "memory")
    redis_url: str = read_text("REDIS_URL", "redis://localhost:6380/0")
    cache_ttl_seconds: int = read_int("CACHE_TTL_SECONDS", 900)
    semantic_cache_threshold: float = read_float("SEMANTIC_CACHE_THRESHOLD", 0.97)

    # --- 5. Retrieval tuning ---
    chunk_size_words: int = read_int("CHUNK_SIZE_WORDS", 180)
    chunk_overlap_words: int = read_int("CHUNK_OVERLAP_WORDS", 40)
    vector_top_k: int = read_int("VECTOR_TOP_K", 20)
    keyword_top_k: int = read_int("KEYWORD_TOP_K", 20)
    rerank_top_k: int = read_int("RERANK_TOP_K", 5)
    min_relevance_score: float = read_float("MIN_RELEVANCE_SCORE", 0.35)
    enable_query_rewrite: bool = read_bool("ENABLE_QUERY_REWRITE", True)
    enable_rerank: bool = read_bool("ENABLE_RERANK", True)

    # --- 6. Security ---
    require_api_key: bool = read_bool("REQUIRE_API_KEY", True)
    api_keys_raw: str = read_text("API_KEYS", "demo-admin-key:admin:public|internal,demo-user-key:user:public")

    def api_keys(self) -> list[ApiKeyRecord]:
        """The parsed list of API keys."""
        return parse_api_keys(self.api_keys_raw)

    def find_api_key(self, presented_key: str) -> ApiKeyRecord | None:
        """Look up one API key. Returns None when the key is unknown."""
        for record in self.api_keys():
            if record.key == presented_key:
                return record
        return None

    def sqlite_file(self) -> Path:
        """Absolute path of the SQLite database file."""
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
        """True when we will make real network calls to OpenAI for answers."""
        if self.llm_provider != "openai":
            return False
        if self.openai_api_key == "":
            return False
        if self.openai_api_key.startswith("sk-paste"):
            return False
        return True

    def using_real_embeddings(self) -> bool:
        """True when we will make real network calls to OpenAI for embeddings."""
        if self.embedding_provider != "openai":
            return False
        if self.openai_api_key == "":
            return False
        if self.openai_api_key.startswith("sk-paste"):
            return False
        return True

    def describe(self) -> dict:
        """A safe summary for the /health endpoint. Never includes secrets."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_is_live": self.using_real_llm(),
            "embedding_provider": self.embedding_provider,
            "embedding_is_live": self.using_real_embeddings(),
            "embedding_dimensions": self.effective_embedding_dimensions(),
            "storage_backend": self.storage_backend,
            "cache_backend": self.cache_backend,
            "rerank_enabled": self.enable_rerank,
            "query_rewrite_enabled": self.enable_query_rewrite,
        }

    def effective_embedding_dimensions(self) -> int:
        """
        The offline embedder uses a small fixed size so tests stay fast.
        The OpenAI embedder uses whatever EMBEDDING_DIMENSIONS says.
        """
        if self.using_real_embeddings():
            return self.embedding_dimensions
        return 512


# One shared instance that the whole application imports.
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
