"""The live 'what's next' surface: ReliefRun on real weather, plus the
classic dashboard.

Additive module. The frozen API app is mounted unchanged at the root; the
`/relief/*` routes add the third instantiation on top of it. On the live
lane (`WEATHER_PROVIDER=google`) the corridor hazard is a real forecast for
real coordinates supplied via `SITES_JSON`, and the run is durable in
Firestore, so an external clock (Cloud Scheduler hitting `/observe`) gives
genuine multi-day operation: most observations find the commitment still
holds; a moved forecast revokes it through the same verifier as everywhere
else. On the seeded lane everything works offline and deterministically,
which remains the reference path.

Observations are content-addressed exactly as in the sentinel: the event id
is derived from the forecast, so a repeated or crash-replayed observation
applies at most once, on any backend.
"""
from __future__ import annotations
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from fastapi import FastAPI, Body, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.api import app as classic
from app.config import ConfigError, Settings
from app.core.store import InMemoryStateStore
from app.relief_demo import (disrupted_weather_fixture, weather_fixture)
from app.scenarios import reliefrun
from app.sentinel import observation_fingerprint

STATIC = Path(__file__).parent / "static"

portal = FastAPI(title="Harbor — ReliefRun live lane")

# Seeded-lane runs live in this process; durable runs live in Firestore and
# are re-attached per request, so a restart or a second scheduler delivery
# finds the same authoritative state.
_memory_runs: Dict[str, InMemoryStateStore] = {}


@portal.exception_handler(ConfigError)
async def _config_error(request, exc: ConfigError):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def _settings() -> Settings:
    return Settings.from_env()


def _weather(settings: Settings, profile: str = "baseline"):
    if settings.is_live:
        from app.providers.weather import GoogleWeatherProvider
        return GoogleWeatherProvider(hours=settings.forecast_hours)
    if profile == "disrupted":
        return disrupted_weather_fixture()
    return weather_fixture()


def _store(settings: Settings, run_id: str, create: bool):
    if settings.state_backend == "firestore":
        from app.core.firestore_store import FirestoreStateStore
        state = reliefrun.build_state() if create else None
        try:
            return FirestoreStateStore(run_id, state=state,
                                       project=settings.gcp_project,
                                       database=settings.firestore_database)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if create:
        _memory_runs[run_id] = InMemoryStateStore(reliefrun.build_state())
    if run_id not in _memory_runs:
        raise HTTPException(status_code=404,
                            detail=f"unknown relief run {run_id!r}")
    return _memory_runs[run_id]


class StartRelief(BaseModel):
    run_id: Optional[str] = None


class Observe(BaseModel):
    # Seeded lane only: which fixture the observation reads. Ignored with a
    # 400 on the live lane, where the real forecast is the only truth.
    profile: str = "baseline"


def _summary(store, extra: dict = {}) -> dict:
    snap = store.snapshot()
    return {
        "scenario": "reliefrun",
        "committed_plan_id": snap["committed_plan_id"],
        "revision": snap["revision"],
        "plan": (snap["plans"] or {}).get(snap["committed_plan_id"]),
        **extra,
    }


@portal.get("/relief", response_class=HTMLResponse)
def relief_page() -> str:
    page = STATIC / "relief.html"
    if not page.exists():
        raise HTTPException(status_code=500,
                            detail=f"relief page missing at {page}")
    return page.read_text(encoding="utf-8")


@portal.get("/relief/config")
def relief_config() -> dict:
    settings = _settings()
    desc = settings.describe()
    if settings.is_live:
        from app.providers.weather import load_sites
        sites = load_sites()
        desc["sites"] = {k: {"lat": v[0], "lng": v[1]}
                         for k, v in sites.items()
                         if k in ("CORRIDOR_A", "VILLAGE_X")}
    return desc


@portal.post("/relief/runs")
def create_relief_run(body: StartRelief = Body(default=StartRelief())) -> dict:
    settings = _settings()
    run_id = body.run_id or f"relief-{int(time.time())}"
    weather = _weather(settings)
    store = _store(settings, run_id, create=True)
    reliefrun.run(store, weather)
    return _summary(store, {"run_id": run_id})


@portal.post("/relief/runs/{run_id}/observe")
def observe(run_id: str, body: Observe = Body(default=Observe())) -> dict:
    """One re-observation: fetch the forecast, and if it differs from what
    the store has already applied, hand it to the disruption path. This is
    the endpoint an external clock calls; redeliveries are deduplicated by
    the content-addressed event id, so calling it too often is safe and
    calling it twice for one forecast changes nothing."""
    settings = _settings()
    if settings.is_live and body.profile != "baseline":
        raise HTTPException(
            status_code=400,
            detail="profile is a seeded-lane control; on the live lane the "
                   "real forecast is the only observation")
    weather = _weather(settings, profile=body.profile)
    store = _store(settings, run_id, create=False)

    before_revision = store.state.revision
    before_events = len(store.trace())
    fingerprint = observation_fingerprint("reliefrun", store.state.facts, weather)
    reliefrun.disrupt(store, weather, event_id=f"forecast-{fingerprint}")

    snap = store.snapshot()
    return _summary(store, {
        "run_id": run_id,
        "fingerprint": fingerprint,
        "revision_advanced": snap["revision"] != before_revision,
        "new_events": store.trace()[before_events:],
    })


@portal.get("/relief/runs/{run_id}")
def read_relief_run(run_id: str) -> dict:
    store = _store(_settings(), run_id, create=False)
    return _summary(store, {"run_id": run_id})


@portal.get("/relief/runs/{run_id}/trace")
def read_relief_trace(run_id: str) -> dict:
    store = _store(_settings(), run_id, create=False)
    return {"run_id": run_id, "trace": store.trace()}


# The frozen app, unchanged, serves everything else -- the classic dashboard
# at /, the two frozen scenarios' API, healthz, config, pubsub push.
portal.mount("/", classic)
