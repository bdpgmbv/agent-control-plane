"""
LAYER 3 - STORAGE: THE INDEX GUARD
==================================
A trap that bites everyone exactly once, usually in production.

Vectors are only comparable to other vectors from the SAME embedding model. The
offline embedder produces 512 numbers; text-embedding-3-small produces 1536.
Even at equal sizes, two different models put "refund" in completely different
places.

So when you paste an API key into .env and restart, every vector already in the
database becomes meaningless. Nothing crashes. Search still returns five
passages. They are simply the wrong five, and every answer quietly gets worse.

This file makes that impossible: the index records which embedder built it, and
a mismatch is refused with an instruction instead of being ignored.
"""

from rag_assistant.layer0_shared.logging_setup import get_logger, log_event

log = get_logger(__name__)


class IndexMismatchError(Exception):
    """Raised when the stored vectors were built by a different embedder."""


def fingerprint_of(embedder) -> str:
    """A short identity for an embedder, e.g. 'text-embedding-3-small:1536'."""
    name = getattr(embedder, "name", "unknown")
    dimensions = getattr(embedder, "dimensions", 0)
    return "%s:%s" % (name, dimensions)


def check_index_matches_embedder(store, embedder) -> None:
    """
    Compare the embedder now in use against the one that built the index.

    An empty index adopts the current embedder. A matching one is fine. A
    mismatch raises, with the exact command needed to fix it.
    """
    current = fingerprint_of(embedder)
    stored = store.read_index_fingerprint()

    if stored == "":
        store.write_index_fingerprint(current)
        log_event(log, "index.fingerprint_recorded", embedder=current)
        return

    if stored == current:
        return

    if store.count_chunks() == 0:
        # Nothing indexed, so nothing to invalidate. Adopt the new embedder.
        store.write_index_fingerprint(current)
        log_event(log, "index.fingerprint_replaced", was=stored, now=current)
        return

    raise IndexMismatchError(
        "The documents in this knowledge base were indexed with '%s', but the "
        "application is now configured to use '%s'.\n\n"
        "Vectors from two different embedding models cannot be compared. Search "
        "would still return results, but they would be the wrong ones, and every "
        "answer would quietly get worse.\n\n"
        "Re-index with the new embedder:\n"
        "    python scripts/reindex.py\n" % (stored, current)
    )
