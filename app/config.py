"""Runtime wiring, resolved from the environment and validated at startup.

Every unset-but-required value raises here rather than downstream. A missing
weather key that surfaces as an empty forecast reads as "no severe weather"
to the verifier, which would approve exactly the plan this system exists to
reject -- so absence must fail at the boundary, loudly.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Optional

from app.core.models import OperationalState
from app.providers.weather import (GoogleWeatherProvider, MockWeatherProvider,
                                   WeatherProvider, WeatherProviderError)
from app.providers.routes import RouteProvider
from app.scenarios import stormslot, harborwindow

SCENARIOS = ("stormslot", "harborwindow")


class ConfigError(RuntimeError):
    """The process is not configured well enough to be trusted with a decision."""


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip().lower()


@dataclass(frozen=True)
class Settings:
    state_backend: str          # memory | firestore
    weather_provider: str       # mock | google
    gcp_project: Optional[str]
    firestore_database: Optional[str]
    pubsub_topic: Optional[str]
    forecast_hours: int

    @classmethod
    def from_env(cls) -> "Settings":
        state_backend = _env("STATE_BACKEND", "memory")
        weather_provider = _env("WEATHER_PROVIDER", "mock")
        if state_backend not in ("memory", "firestore"):
            raise ConfigError(
                f"STATE_BACKEND={state_backend!r} is not one of memory|firestore")
        if weather_provider not in ("mock", "google"):
            raise ConfigError(
                f"WEATHER_PROVIDER={weather_provider!r} is not one of mock|google")

        project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT")
        if state_backend == "firestore" and not project:
            raise ConfigError(
                "STATE_BACKEND=firestore requires GOOGLE_CLOUD_PROJECT. Refusing "
                "to start with a Firestore backend and no project."
            )
        if weather_provider == "google" and not (
            os.environ.get("GOOGLE_WEATHER_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
        ):
            raise ConfigError(
                "WEATHER_PROVIDER=google requires GOOGLE_WEATHER_API_KEY. Set it, "
                "or run with WEATHER_PROVIDER=mock. Refusing to start with a live "
                "weather provider and no key."
            )
        try:
            hours = int(os.environ.get("FORECAST_HOURS", "24"))
        except ValueError as exc:
            raise ConfigError(f"FORECAST_HOURS is not an integer: {exc}") from exc

        return cls(
            state_backend=state_backend,
            weather_provider=weather_provider,
            gcp_project=project,
            firestore_database=os.environ.get("FIRESTORE_DATABASE"),
            pubsub_topic=os.environ.get("PUBSUB_TOPIC"),
            forecast_hours=hours,
        )

    @property
    def is_live(self) -> bool:
        return self.weather_provider == "google"

    def describe(self) -> dict:
        return {
            "state_backend": self.state_backend,
            "weather_provider": self.weather_provider,
            "gcp_project": self.gcp_project,
            "pubsub_topic": self.pubsub_topic,
            "forecast_hours": self.forecast_hours,
            "deterministic_replay": not self.is_live,
        }


def build_state(scenario: str) -> OperationalState:
    if scenario == "stormslot":
        return stormslot.build_state()
    if scenario == "harborwindow":
        return harborwindow.build_state()
    raise ConfigError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")


def make_weather(settings: Settings, seeded: str = "baseline") -> WeatherProvider:
    """Live provider when configured, otherwise the seeded deterministic one."""
    if settings.is_live:
        return GoogleWeatherProvider(hours=settings.forecast_hours)
    from app.demo import weather_fixture, disrupted_weather_fixture
    if seeded == "disrupted":
        return disrupted_weather_fixture()
    if seeded == "baseline":
        return weather_fixture()
    raise ConfigError(f"unknown seeded forecast {seeded!r}; expected baseline|disrupted")


def make_routes(settings: Settings) -> RouteProvider:
    """StormSlot road routing.

    HarborWindow is the submission flagship. StormSlot remains transfer evidence,
    so its live Google Routes adapter is intentionally not part of the submitted
    runtime and this path remains on the seeded provider.
    """
    from app.demo import route_fixture
    return route_fixture()


def make_store(settings: Settings, run_id: str, scenario: str):
    state = build_state(scenario)
    if settings.state_backend == "firestore":
        from app.core.firestore_store import FirestoreStateStore
        return FirestoreStateStore(run_id, state=state, project=settings.gcp_project,
                                   database=settings.firestore_database)
    from app.core.store import InMemoryStateStore
    return InMemoryStateStore(state)
