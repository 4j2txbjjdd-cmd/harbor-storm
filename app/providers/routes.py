from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Protocol, Tuple


@dataclass(frozen=True)
class RouteEstimate:
    minutes: int
    distance_km: float
    feasible: bool = True
    reason: str = ""


class RouteProvider(Protocol):
    def estimate(self, origin: str, destination: str, departure_hour: int) -> RouteEstimate:
        ...


class MissingRouteData(KeyError):
    """A route was asked for that the seeded scenario does not define."""


class MockRouteProvider:
    """Deterministic seeded routes.

    Deliberately has no fallback estimate. A silent default would let a
    scenario verify a departure hour nobody ever costed, which is exactly the
    class of quiet wrong answer this substrate exists to prevent.
    """

    def __init__(self, routes: Dict[Tuple[str, str, int], RouteEstimate]):
        self.routes = routes

    def estimate(self, origin: str, destination: str, departure_hour: int) -> RouteEstimate:
        key = (origin, destination, int(departure_hour))
        if key not in self.routes:
            raise MissingRouteData(
                f"no seeded route for {origin}->{destination} departing {departure_hour}:00; "
                f"seeded departures: {sorted(h for o, d, h in self.routes if (o, d) == (origin, destination))}"
            )
        return self.routes[key]


class GoogleRoutesProvider:
    """Production adapter seam for the StormSlot transfer scenario.

    HarborWindow is the submission flagship. StormSlot remains transfer evidence,
    so the Google Routes adapter is intentionally not implemented for submission.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key

    def estimate(self, origin: str, destination: str, departure_hour: int) -> RouteEstimate:
        raise NotImplementedError(
            "Google Routes adapter is not implemented in this submission; "
            "StormSlot uses the seeded route provider."
        )
