"""
LAYER 3 - STORAGE: THE FACTORY
==============================
The single place that decides which backend to use. Everything else in the
codebase asks for `get_store()` and does not care about the answer.
"""

from rag_assistant.layer1_config.settings import settings
from rag_assistant.layer3_storage.base import DocumentStore

# Built once and reused, because opening a database connection is not free.
_store: DocumentStore | None = None


def build_store() -> DocumentStore:
    """Create a fresh store from configuration."""
    if settings.storage_backend == "postgres":
        from rag_assistant.layer3_storage.postgres_store import PostgresDocumentStore

        store: DocumentStore = PostgresDocumentStore(
            dsn=settings.postgres_dsn,
            embedding_dimensions=settings.effective_embedding_dimensions(),
        )
    else:
        from rag_assistant.layer3_storage.sqlite_store import SqliteDocumentStore

        store = SqliteDocumentStore(database_path=settings.sqlite_file())

    store.initialise()
    return store


def get_store() -> DocumentStore:
    """The shared store instance used by the running application."""
    global _store
    if _store is None:
        _store = build_store()
    return _store


def set_store(store: DocumentStore | None) -> None:
    """Replace the shared store. Used by the tests to inject a temporary one."""
    global _store
    _store = store
