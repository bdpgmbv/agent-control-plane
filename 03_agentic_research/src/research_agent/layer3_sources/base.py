"""
LAYER 3 - SOURCES: THE INTERFACE
================================
Everything a research worker can search is behind this one interface.

WHY IT IS AN INTERFACE AND NOT JUST A FUNCTION
    This project ships with a local corpus, so it runs with no network, no API
    keys and no rate limits, and its results are identical on every machine -
    which is what makes the evaluation suite mean anything.

    A real deployment searches the web, or an internal wiki, or a document store,
    or all three. Those differ in latency, in cost, in how often they fail, and in
    what a "result" even looks like. Putting them behind one interface means the
    planner, the workers and the synthesiser never learn the difference.

TO ADD A REAL SEARCH PROVIDER
    Write a class with a `name` and a `search()` returning SearchHit objects, and
    register it in registry.py. Nothing above layer 3 changes. The things to get
    right are in this file's docstrings: a timeout, a failure that returns empty
    rather than raising, and a credibility rating for whatever comes back.
"""

from abc import ABC, abstractmethod

from research_agent.layer2_models.schemas import SearchHit, SourceDocument


class SourceAdapter(ABC):
    """One place research can be done."""

    name: str = "unnamed"

    @abstractmethod
    def search(self, query: str, top_k: int) -> list[SearchHit]:
        """
        Find documents matching the query, best first.

        Must not raise. A source that is down should return an empty list, so one
        failing provider degrades the report instead of ending the run.
        """

    @abstractmethod
    def get_document(self, document_id: str) -> SourceDocument | None:
        """Fetch one document by id."""

    def describe(self) -> dict:
        return {"name": self.name}
