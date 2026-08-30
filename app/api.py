"""HTTP surface for the substrate.

Endpoints exist to seed a scenario, trigger a disruption, and read the event
trace -- the three things the four-minute demo needs to show. Nothing here
decides anything: every state change still goes through the verifier.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import SCENARIOS, ConfigError, Settings
from app.core.store import (ClaimContentionError, EventAlreadyAppliedError,
                            SupersededWorkerError)
from app.events import MalformedEvent, parse_push
from app.runner import UnknownRun, apply_disruption, describe, get_store, list_runs, start_run

log = logging.getLogger("harbor")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format='{"severity":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
)

STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="HarborWindow / StormSlot",
    description="Shared autonomous-operations substrate. Verification precedes commitment.",
    version="0.2.0",
)


class StartRun(BaseModel):
    scenario: str = Field(..., description="stormslot | harborwindow")
    profile: str = Field("baseline", description="seeded forecast: baseline | disrupted")


class Disrupt(BaseModel):
    profile: str = Field("disrupted", description="seeded forecast to apply")


def settings() -> Settings:
    try:
        return Settings.from_env()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.exception_handler(ConfigError)
async def _config_error(request: Request, exc: ConfigError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


@app.exception_handler(UnknownRun)
async def _unknown_run(request: Request, exc: UnknownRun):
    return JSONResponse(status_code=404, content={"error": str(exc)})


@app.get("/healthz")
def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/config")
def config() -> Dict[str, Any]:
    """What this process is actually wired to. Read this before trusting a demo."""
    return settings().describe()


@app.get("/scenarios")
def scenarios() -> Dict[str, Any]:
    return {"scenarios": list(SCENARIOS), "selected": "harborwindow",
            "note": "HarborWindow is the submission flagship; StormSlot remains "
                    "available as transfer evidence."}


@app.post("/runs")
def create_run(body: StartRun) -> Dict[str, Any]:
    if body.scenario not in SCENARIOS:
        raise HTTPException(status_code=400,
                            detail=f"unknown scenario {body.scenario!r}; expected {list(SCENARIOS)}")
    try:
        result = start_run(body.scenario, settings(), profile=body.profile)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("run %s committed %s", result["run_id"], result["committed_plan_id"])
    return result


@app.get("/runs")
def runs() -> Dict[str, Any]:
    return {"runs": list_runs()}


@app.get("/runs/{run_id}")
def read_run(run_id: str) -> Dict[str, Any]:
    try:
        return describe(run_id, get_store(run_id, settings()))
    except UnknownRun as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/runs/{run_id}/trace")
def read_trace(run_id: str) -> Dict[str, Any]:
    try:
        store = get_store(run_id, settings())
    except UnknownRun as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"run_id": run_id, "trace": store.trace()}


@app.post("/runs/{run_id}/disrupt")
def disrupt_run(run_id: str, body: Disrupt = Body(default=Disrupt())) -> Dict[str, Any]:
    try:
        result = apply_disruption(run_id, settings(), profile=body.profile)
    except UnknownRun as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("run %s after disruption committed %s", run_id, result["committed_plan_id"])
    return result


@app.post("/pubsub/push")
def pubsub_push(envelope: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Pub/Sub push subscription endpoint.

    A malformed message returns 400 rather than a silent ack, so it retries and
    then lands in the dead-letter topic. Configure one: without a DLQ a poison
    message retries forever.
    """
    try:
        event = parse_push(envelope)
    except MalformedEvent as exc:
        log.error("rejected malformed disruption: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if event.message_id is None:
        # Real Pub/Sub always sets messageId. Without one there is no delivery
        # identity to deduplicate on, so this message will apply every time it
        # arrives. Say so rather than letting it look deduplicated.
        log.warning("disruption for run %s has no messageId; applying without "
                    "deduplication", event.run_id)
    try:
        result = apply_disruption(event.run_id, settings(), profile=event.profile,
                                  event_id=event.message_id)
    except UnknownRun as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except EventAlreadyAppliedError as exc:
        # The event genuinely is applied -- a replacement finished it while
        # this attempt was still working. That is a duplicate, not a fault, so
        # it acknowledges. Letting it 500 would NACK a message whose work is
        # complete and burn a delivery against the dead-letter budget.
        log.info("delivery arrived after the event was applied: %s", exc)
        return {"run_id": event.run_id, "kind": event.kind,
                "outcome": "duplicate", "duplicate": True,
                "committed_plan_id": describe(
                    event.run_id, get_store(event.run_id, settings())
                )["committed_plan_id"]}
    except ClaimContentionError as exc:
        # Contention resolved nothing: the retry budget was spent and a fresh
        # read still could not say who owns the work. Nothing was decided, so
        # redelivering is safe and is the only way the work gets done. 409 for
        # the same reason as the two cases below -- a 500 would burn a delivery
        # against the dead-letter budget and log a stack trace for a condition
        # the system is designed to encounter under load.
        log.info("delivery hit unresolved claim contention: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SupersededWorkerError as exc:
        # Being superseded is a designed outcome, not a fault. Another attempt
        # took the event over mid-flight. 409 puts the message back the same
        # way an in-flight duplicate does, rather than burning a delivery
        # against the dead-letter budget and logging a stack trace for
        # something the system did on purpose.
        log.info("delivery superseded: %s", exc)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if result["outcome"] == "in_flight":
        # Another delivery of this message is applying it right now. This one
        # must NOT be acknowledged: acknowledging ends redelivery, and if that
        # worker then fails there is nothing left to repair the run. 409 puts
        # the message back so it returns once the other attempt has resolved.
        raise HTTPException(
            status_code=409,
            detail=f"event {event.message_id} is already being applied to run "
                   f"{event.run_id}; redeliver")
    # A completed duplicate IS acknowledged. Returning an error there would make
    # Pub/Sub redeliver it for as long as the retry policy allows.
    return {"run_id": event.run_id, "kind": event.kind,
            "outcome": result["outcome"],
            "duplicate": result["outcome"] != "applied",
            "committed_plan_id": result["committed_plan_id"]}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    page = STATIC / "dashboard.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail=f"dashboard template missing at {page}")
    return page.read_text(encoding="utf-8")
