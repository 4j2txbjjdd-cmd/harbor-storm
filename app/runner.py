"""Run lifecycle: seed a scenario, drive it, apply disruptions.

The only place that knows how to turn a scenario name plus configuration into
a live run. Both the CLI demo and the HTTP API go through here, so they cannot
drift apart.
"""
from __future__ import annotations
import uuid
from typing import Any, Dict, Optional

from app.config import (SCENARIOS, ConfigError, Settings, make_routes,
                        make_store, make_weather)
from app.scenarios import stormslot, harborwindow


class UnknownRun(KeyError):
    """A run id that this process has never seen and cannot rehydrate."""


# Process-local registry for the in-memory backend. With STATE_BACKEND=firestore
# runs are rehydrated from Firestore instead, so they survive a restart and are
# visible to every Cloud Run instance.
_RUNS: Dict[str, Any] = {}


def new_run_id(scenario: str) -> str:
    return f"{scenario}-{uuid.uuid4().hex[:8]}"


def _dispatch_run(scenario: str, store, settings: Settings, profile: str):
    weather = make_weather(settings, seeded=profile)
    if scenario == "stormslot":
        return stormslot.run(store, weather, make_routes(settings))
    return harborwindow.run(store, weather)


def _dispatch_disrupt(scenario: str, store, settings: Settings, profile: str,
                      event_id: Optional[str] = None):
    weather = make_weather(settings, seeded=profile)
    if scenario == "stormslot":
        return stormslot.disrupt(store, weather, make_routes(settings), event_id)
    return harborwindow.disrupt(store, weather, event_id)


def start_run(scenario: str, settings: Optional[Settings] = None,
              run_id: Optional[str] = None, profile: str = "baseline") -> Dict[str, Any]:
    if scenario not in SCENARIOS:
        raise ConfigError(f"unknown scenario {scenario!r}; expected one of {SCENARIOS}")
    settings = settings or Settings.from_env()
    run_id = run_id or new_run_id(scenario)
    store = make_store(settings, run_id, scenario)
    _RUNS[run_id] = store
    _dispatch_run(scenario, store, settings, profile)
    return describe(run_id, store)


def get_store(run_id: str, settings: Optional[Settings] = None):
    if run_id in _RUNS:
        return _RUNS[run_id]
    settings = settings or Settings.from_env()
    if settings.state_backend != "firestore":
        raise UnknownRun(
            f"run {run_id!r} is not in this process and STATE_BACKEND=memory, so "
            f"it cannot be rehydrated. Runs do not survive a restart on the "
            f"memory backend."
        )
    from app.core.firestore_store import FirestoreStateStore
    store = FirestoreStateStore(run_id, project=settings.gcp_project,
                                database=settings.firestore_database)
    _RUNS[run_id] = store
    return store


def apply_disruption(run_id: str, settings: Optional[Settings] = None,
                     profile: str = "disrupted",
                     event_id: Optional[str] = None) -> Dict[str, Any]:
    """Apply new external truth to a run.

    `event_id` is the delivery identity -- Pub/Sub's messageId. Passing it makes
    the application idempotent: a redelivery of the same message leaves the run
    exactly as it was. Passing None means this is not a redeliverable event (the
    CLI demo, a direct trigger) and it always applies.
    """
    settings = settings or Settings.from_env()
    store = get_store(run_id, settings)

    # The outcome is read from authoritative store state, before and after --
    # never reconstructed by scanning the trace. Any concurrent write to the
    # same run would move the trace tail, and on Firestore the tail is ordered
    # by a per-writer clock, so a second instance running marginally ahead
    # would reorder it and invert the answer.
    already = store.has_processed_event(event_id) if event_id is not None else False
    # Authoritative, not the read-model cache: with containerConcurrency > 1
    # two threads delivering the same message share one store instance, and
    # either could refresh that cache between this read and the check below.
    revision_before = (store.refresh().revision if hasattr(store, "refresh")
                       else store.state.revision)

    _dispatch_disrupt(store.state.scenario, store, settings, profile, event_id)
    result = describe(run_id, store)

    if event_id is None:
        result["outcome"] = "applied"
    elif already:
        result["outcome"] = "duplicate"
    elif ((store.refresh().revision if hasattr(store, "refresh")
           else store.state.revision) == revision_before
          and not store.has_processed_event(event_id)):
        # Advanced by nobody and closed by nobody: another delivery of this
        # message holds the lease and is applying it now.
        result["outcome"] = "in_flight"
    else:
        result["outcome"] = "applied"
    return result


def describe(run_id: str, store) -> Dict[str, Any]:
    snap = store.snapshot()
    committed = snap["committed_plan_id"]
    plans = snap["plans"]
    return {
        "run_id": run_id,
        "scenario": snap["scenario"],
        "committed_plan_id": committed,
        "committed_plan": plans.get(committed) if committed else None,
        "rejected_plans": [p for p in plans.values()
                           if not p["verified"] and p["rejection_reason"]],
        "plans": plans,
        "work": snap["work"],
        "facts": snap["facts"],
        "target": snap["target"],
        "trace": store.trace(),
    }


def list_runs() -> Dict[str, Any]:
    return {rid: {"scenario": s.state.scenario,
                  "committed_plan_id": s.state.committed_plan_id}
            for rid, s in _RUNS.items()}


def reset() -> None:
    """Drop the process-local registry. Tests only."""
    _RUNS.clear()
