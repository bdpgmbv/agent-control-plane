"""
LAYER 3 - SOURCES: THE REGISTRY
===============================
Which sources exist. One place to change when you add a real one.
"""

from research_agent.layer2_models.schemas import SearchHit
from research_agent.layer3_sources.base import SourceAdapter
from research_agent.layer3_sources.local_corpus import LocalCorpusSource

_sources: list[SourceAdapter] | None = None


def get_sources() -> list[SourceAdapter]:
    """The sources available to workers. Built once."""
    global _sources
    if _sources is None:
        _sources = [LocalCorpusSource()]
    return _sources


def set_sources(sources: list[SourceAdapter] | None) -> None:
    """Replace the sources. Used by the tests."""
    global _sources
    _sources = sources


def search_everywhere(query: str, top_k: int) -> list[SearchHit]:
    """
    Search every source and merge the results.

    A source that fails is skipped, not fatal. One provider being down should
    make the report thinner, not end the run - and the gap shows up in the
    report's `gaps` list rather than as a stack trace.
    """
    merged: list[SearchHit] = []

    for source in get_sources():
        try:
            hits = source.search(query, top_k)
        except Exception:
            continue

        for hit in hits:
            merged.append(hit)

    merged.sort(key=lambda hit: hit.relevance, reverse=True)
    return merged[:top_k]
