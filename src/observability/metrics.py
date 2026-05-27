"""Spyglass logging and metrics for trainspotter."""

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec
from typing import TypeVar

from spyglass import MetricsCollector
from spyglass import configure_logging

from ..config import PROJECT_NAME
from ..config import SPYGLASS_HOST

configure_logging(host=SPYGLASS_HOST, project=PROJECT_NAME)
metrics = MetricsCollector(host=SPYGLASS_HOST, project=PROJECT_NAME)

P = ParamSpec("P")
R = TypeVar("R")


def track_request(route: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Increment and time a request handler, tagged by route."""
    tags = {"route": route}

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with metrics.timed("request", tags=tags):
                metrics.increment("request", tags=tags)
                return func(*args, **kwargs)

        return wrapper

    return decorator
