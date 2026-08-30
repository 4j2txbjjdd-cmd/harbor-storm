from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

from app.core.models import OperationalState, WorkItem, CandidatePlan
from app.core.store import Fence, Store, SupersededWorkerError, FenceArg, UNFENCED
from app.core.verify import verify_and_commit, reverify_committed
from app.providers.weather import WeatherProvider, WeatherPoint


def build_state() -> OperationalState:
    return OperationalState(
        scenario="harborwindow",
        facts={
            "harbor": "HARBOR_A",
            "island": "ISLAND_B",
            "cargo_ready_hour": 12,
            # The sailing currently on the schedule.
            "booked_departure_hour": 12,
            "latest_departure_hour": 17,
            "crossing_hours": 1.5,
            # harbormaster-agent scope: the boat only sails on these slots
            "sailing_slots": [12, 14, 16],
            "island_landing_cutoff_hour": 18,
            # cargo-agent scope
            "boat_capacity_kg": 500,
            "cargo_kg": 320,
            # window-agent scope
            "max_wind_kph": 35.0,
            "max_rain_mm": 20.0,
        },
        target={"cargo_moved_in_safe_window": True},
    )


def crossing_hours(depart_hour: float, duration_hours: float) -> List[int]:
    """Every clock hour the boat is at sea, departure included."""
    return list(range(int(math.floor(depart_hour)),
                      int(math.ceil(depart_hour + duration_hours))))


def _departure_of(plan: CandidatePlan) -> Optional[int]:
    for a in plan.actions:
        if a.get("type") == "depart":
            return int(a["hour"])
    return None


def _unsafe_reason(hour: int, by_h: Dict[int, WeatherPoint],
                   by_i: Dict[int, WeatherPoint], f: dict) -> Optional[str]:
    for label, table in (("harbor", by_h), ("island", by_i)):
        p = table.get(hour)
        if p is None:
            return f"no {label} forecast for hour {hour}"
        if p.wind_kph > f["max_wind_kph"]:
            return f"{label} wind {p.wind_kph} kph over limit at hour {hour}"
        if p.rain_mm > f["max_rain_mm"]:
            return f"{label} rain {p.rain_mm} mm over limit at hour {hour}"
    return None


def make_verifier(store: Store, harbor: List[WeatherPoint], island: List[WeatherPoint]):
    """Deterministic feasibility check for a proposed sailing.

    Recomputes the whole crossing window from authoritative facts. The plan's
    own metrics are ignored, so an over-confident agent proposal still fails.
    """
    f = store.state.facts
    by_h = {p.hour: p for p in harbor}
    by_i = {p.hour: p for p in island}

    def verifier(plan: CandidatePlan) -> Tuple[bool, str]:
        depart = _departure_of(plan)
        if depart is None:
            return False, "plan proposes no departure"
        if f["cargo_kg"] > f["boat_capacity_kg"]:
            return False, (f"cargo {f['cargo_kg']} kg exceeds boat capacity "
                           f"{f['boat_capacity_kg']} kg")
        if depart < f["cargo_ready_hour"]:
            return False, f"cargo not ready until {f['cargo_ready_hour']}:00"
        if depart > f["latest_departure_hour"]:
            return False, "past latest departure hour"
        if depart not in f["sailing_slots"]:
            return False, f"no sailing slot at {depart}:00"

        arrival = depart + f["crossing_hours"]
        if arrival > f["island_landing_cutoff_hour"]:
            return False, "island landing cutoff passed"

        for hour in crossing_hours(depart, f["crossing_hours"]):
            reason = _unsafe_reason(hour, by_h, by_i, f)
            if reason:
                return False, reason
        return True, "feasible"

    return verifier


def _plan(plan_id: str, depart: int, f: dict, basis_revision: int) -> CandidatePlan:
    return CandidatePlan(
        id=plan_id,
        scenario="harborwindow",
        created_by="window-agent",
        actions=[
            {"type": "reserve_boat", "departure_hour": depart},
            {"type": "load_cargo", "kg": f["cargo_kg"]},
            {"type": "depart", "hour": depart},
        ],
        metrics={
            "departure_hour": float(depart),
            "arrival_hour": depart + f["crossing_hours"],
            "cargo_kg": float(f["cargo_kg"]),
        },
        basis_revision=basis_revision,
    )


# Which actor is answerable for which work item. A caller that drives one of
# these actors itself -- an LLM proposer executing through its own toolkit --
# passes a subset, so the work item it owns is left OPEN for it to claim through
# `claim_work` rather than being claimed on its behalf. Claiming for an actor
# that is about to run would hide the one tool call that proves it is bounded.
ACTOR_WORK = (("window", "window-agent"),
              ("load", "cargo-agent"),
              ("slot", "harbormaster-agent"))


def seed(store: Store, weather: WeatherProvider, fence: FenceArg = UNFENCED,
         claim_for=ACTOR_WORK):
    """Measure the marine window, open the work items, publish the constraints.

    This is the shared preamble: everything that must exist before anybody can
    plan, and nothing that decides anything. Returns the (harbor, island)
    forecasts the verifier will recompute against, or None if a work item could
    not be claimed -- in which case an ABORT is already on the trace.

    The two constraint reports are how a bounded actor learns what it may not
    see directly. `window-agent` cannot read `sailing_slots`; it reads the trace
    where `harbormaster-agent` published them. That indirection is the point,
    and it is the only channel between scopes.
    """
    f = store.state.facts
    harbor = weather.hourly(f["harbor"])
    island = weather.hourly(f["island"])
    by_h = {p.hour: p for p in harbor}
    by_i = {p.hour: p for p in island}
    covered = sorted(set(by_h) & set(by_i))
    unsafe = [h for h in covered if _unsafe_reason(h, by_h, by_i, f)]
    store.emit("MARINE_WEATHER_MEASURED", "weather-agent", {
        "harbor": f["harbor"], "island": f["island"],
        "max_wind_kph": f["max_wind_kph"], "max_rain_mm": f["max_rain_mm"],
        "harbor_hours": covered,
        "severe_hours": unsafe,
    })

    store.create_work(WorkItem("window", "find safe departure window"), fence=fence)
    store.create_work(WorkItem("load", "check boat/cargo feasibility"), fence=fence)
    store.create_work(WorkItem("slot", "confirm sailing slot"), fence=fence)

    for work_id, actor in claim_for:
        if not store.claim(work_id, actor, fence):
            store.emit("ABORT", "coordinator", {"reason": f"could not claim {work_id}"})
            return None

    store.emit("CONSTRAINT_REPORTED", "cargo-agent",
               {"cargo_kg": f["cargo_kg"], "boat_capacity_kg": f["boat_capacity_kg"]})
    store.emit("CONSTRAINT_REPORTED", "harbormaster-agent",
               {"sailing_slots": f["sailing_slots"],
                "booked_departure_hour": f["booked_departure_hour"],
                "island_landing_cutoff_hour": f["island_landing_cutoff_hour"]})
    return harbor, island


def run(store: Store, weather: WeatherProvider,
        fence: FenceArg = UNFENCED) -> dict:
    f = store.state.facts
    # Every plan below is computed against this revision and binds to it.
    revision = store.state.revision
    seeded = seed(store, weather, fence)
    if seeded is None:
        return store.snapshot()
    harbor, island = seeded

    verifier = make_verifier(store, harbor, island)

    # 1. Business as usual: sail on the booked slot.
    booked = f["booked_departure_hour"]
    store.add_plan(_plan("harbor-plan-1", booked, f, revision), fence)
    if verify_and_commit(store, "harbor-plan-1", verifier, fence):
        return store.snapshot()

    # 2. Rejected. Replan: least deviation from the booked sailing that verifies.
    store.emit("REPLAN_STARTED", "window-agent",
               {"reason": store.get_plan("harbor-plan-1").rejection_reason})
    candidates = sorted(
        (h for h in f["sailing_slots"]
         if f["cargo_ready_hour"] <= h <= f["latest_departure_hour"] and h != booked),
        key=lambda h: (abs(h - booked), h),
    )
    for n, hour in enumerate(candidates, start=2):
        pid = f"harbor-plan-{n}"
        store.add_plan(_plan(pid, hour, f, revision), fence)
        if verify_and_commit(store, pid, verifier, fence):
            store.emit("SAILING_RESCHEDULED", "harbormaster-agent",
                       {"from_hour": booked, "to_hour": hour, "cause": "marine weather"})
            return store.snapshot()

    store.emit("NO_FEASIBLE_PLAN", "coordinator", {"searched_hours": candidates})
    return store.snapshot()


def disrupt(store: Store, weather: WeatherProvider,
            event_id: Optional[str] = None) -> dict:
    """New marine forecast arrives. Re-verify the commitment; replan if broken."""
    f = store.state.facts
    harbor = weather.hourly(f["harbor"])
    island = weather.hourly(f["island"])
    by_h = {p.hour: p for p in harbor}
    by_i = {p.hour: p for p in island}
    unsafe = [h for h in sorted(set(by_h) & set(by_i))
              if _unsafe_reason(h, by_h, by_i, f)]

    # New truth has landed: everything computed before this point is stale. This
    # call is also the deduplication point, so nothing above it writes to the
    # trace -- a redelivered message must leave no mark at all.
    lease = store.advance_revision(
        "weather-agent", "marine forecast updated",
        {"harbor": f["harbor"], "island": f["island"]}, event_id=event_id)
    if lease is None:
        return store.snapshot()
    fence = lease.fence

    try:
        store.emit("MARINE_WEATHER_UPDATED", "weather-agent",
                   {"harbor": f["harbor"], "island": f["island"],
                    "severe_hours": unsafe})
        verifier = make_verifier(store, harbor, island)
        if not reverify_committed(store, verifier, fence):
            run(store, weather, fence)
    except SupersededWorkerError:
        # Another attempt owns this event now.
        raise
    except Exception as exc:
        # The revision has already advanced; this application did not finish.
        store.abandon_event(event_id, "weather-agent",
                            f"{type(exc).__name__}: {exc}", fence)
        raise
    store.complete_event(event_id, "weather-agent", fence)
    return store.snapshot()
