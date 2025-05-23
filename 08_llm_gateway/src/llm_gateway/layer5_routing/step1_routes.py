"""
LAYER 5, STEP 1 - WHICH MODEL, FOR WHICH WORK
=============================================
A route is a task name and an ordered list of models to try.

Ordered, not one model, because the second entry is the answer to "what happens
when the first one is down". A gateway whose answer to that is "the request
fails" is not buying you anything a direct SDK call does not already do.

The ordering is cheapest-capable-first, which is the only ordering that makes
economic sense: the expensive model is there to catch the case where the cheap
one is unavailable, not to be used by default. Routing that reaches for the
strong model first and falls back to the weak one has the fallback the wrong way
round - it pays the most on the happy path and degrades quality under load,
which is precisely backwards.
"""

from llm_gateway.layer2_models.schemas import Route

DEFAULT_ROUTES = [
    Route(
        name="cheap-first",
        task="general",
        chain=["offline-fast", "offline-strong"],
        reason="ordinary questions: the cheap model, with a better one behind it",
    ),
    Route(
        name="reasoning",
        task="reasoning",
        chain=["offline-strong", "offline-fast"],
        reason="work that needs care: start with the stronger model, degrade rather than fail",
    ),
    Route(
        name="live-cheap-first",
        task="live",
        chain=["gpt-4o-mini", "offline-fast"],
        reason="the real model, with an offline model as the last resort",
    ),
]


class RouteTable:
    def __init__(self, routes: list[Route] | None = None) -> None:
        if routes is None:
            routes = list(DEFAULT_ROUTES)
        self.routes = routes

    def for_task(self, task: str) -> Route:
        for route in self.routes:
            if route.task == task:
                return route
        return self.routes[0]

    def chain_for(self, task: str, explicit_model: str = "") -> tuple[list[str], str]:
        """
        Returns (models to try in order, the route's name).

        An explicit model is honoured and gets NO fallback chain. If a caller
        names a model they have a reason, and quietly answering from a different
        one would make their measurement meaningless - which is the whole reason
        the A/B machinery pins a model per variant.
        """
        if explicit_model != "":
            return ([explicit_model], "explicit")
        route = self.for_task(task)
        return (list(route.chain), route.name)

    def names(self) -> list[str]:
        found = []
        for route in self.routes:
            found.append(route.name)
        return found
