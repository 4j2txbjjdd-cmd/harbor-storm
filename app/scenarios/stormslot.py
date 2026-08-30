from __future__ import annotations
import math
from typing import List, Optional, Tuple

from app.core.models import OperationalState, WorkItem, CandidatePlan
from app.core.store import Fence, Store, SupersededWorkerError, FenceArg, UNFENCED
from app.core.verify import verify_and_commit, reverify_committed
from app.providers.weather import WeatherProvider, WeatherPoint
from app.providers.routes import RouteProvider


def build_state() -> OperationalState:
    return OperationalState(
        scenario="stormslot",
        facts={
            "port": "PORT_A",
            "warehouse": "WH_A",
            "container_ready_hour": 13,
            # What is currently on the books. The agents must justify any change.
            "booked_pickup_hour": 15,
            "pickup_deadline_hour": 17,
            "truck_available_hour": 13,
            # port-agent scope: hours the terminal can physically hand over
            "port_handover_hours": [13, 14, 15, 16, 17],
            # warehouse-agent scope: receiving dock availability
            "warehouse_open_hour": 10,
            "warehouse_close_hour": 19,
            "warehouse_shift_change_hour": 12,
            "storm_threshold_rain_mm": 25.0,
        },
        target={"container_delivered_before_close": True},
    )


def severe_hours(series: List[WeatherPoint], threshold_mm: float) -> List[int]:
    return [p.hour for p in series if p.rain_mm >= threshold_mm]


def transit_hours(depart_hour: float, minutes: int) -> List[int]:
    """Every clock hour the truck is exposed on the road, departure included."""
    arrival = depart_hour + minutes / 60.0
    return list(range(int(math.floor(depart_hour)), int(math.ceil(arrival))))


def _departure_of(plan: CandidatePlan) -> Optional[int]:
    for a in plan.actions:
        if a.get("type") == "dispatch_truck":
            return int(a["hour"])
    return None


def make_verifier(store: Store, series: List[WeatherPoint], routes: RouteProvider):
    """Deterministic feasibility check.

    Reads only the plan's proposed *actions* and recomputes everything else
    from authoritative facts and providers. Agent-supplied metrics are never
    trusted, so a confident but wrong proposal is still rejected.
    """
    f = store.state.facts
    bad = set(severe_hours(series, f["storm_threshold_rain_mm"]))

    def verifier(plan: CandidatePlan) -> Tuple[bool, str]:
        depart = _departure_of(plan)
        if depart is None:
            return False, "plan proposes no truck dispatch"
        if depart < f["container_ready_hour"]:
            return False, f"container not ready until {f['container_ready_hour']}:00"
        if depart > f["pickup_deadline_hour"]:
            return False, "pickup deadline missed"
        if depart not in f["port_handover_hours"]:
            return False, f"port cannot hand over at {depart}:00"

        route = routes.estimate(f["port"], f["warehouse"], depart)
        if not route.feasible:
            return False, route.reason or "route infeasible"

        exposed = sorted(set(transit_hours(depart, route.minutes)) & bad)
        if exposed:
            return False, f"transit crosses severe weather at {exposed}"

        arrival = depart + route.minutes / 60.0
        if arrival > f["warehouse_close_hour"]:
            return False, "warehouse closed before arrival"
        if arrival < f["warehouse_open_hour"]:
            return False, "warehouse not yet open on arrival"
        if int(math.floor(arrival)) == f["warehouse_shift_change_hour"]:
            return False, "arrival lands in warehouse shift change"
        return True, "feasible"

    return verifier


def _plan(plan_id: str, depart: int, minutes: int, cargo_note: str,
          basis_revision: int) -> CandidatePlan:
    arrival = depart + minutes / 60.0
    return CandidatePlan(
        id=plan_id,
        scenario="stormslot",
        created_by="transport-agent",
        actions=[
            {"type": "reserve_pickup", "hour": depart},
            {"type": "dispatch_truck", "hour": depart},
            {"type": "reserve_receiving", "arrival_hour": arrival},
        ],
        metrics={
            "departure_hour": float(depart),
            "arrival_hour": arrival,
            "route_minutes": float(minutes),
        },
        basis_revision=basis_revision,
    )


def run(store: Store, weather: WeatherProvider, routes: RouteProvider,
        fence: FenceArg = UNFENCED) -> dict:
    f = store.state.facts
    # Every plan below is computed against this revision and binds to it.
    revision = store.state.revision
    series = weather.hourly(f["port"])
    bad = severe_hours(series, f["storm_threshold_rain_mm"])
    store.emit("WEATHER_MEASURED", "weather-agent", {
        "location": f["port"],
        "severe_hours": bad,
        "threshold_rain_mm": f["storm_threshold_rain_mm"],
    })

    store.create_work(WorkItem("route", "find feasible truck departure"), fence=fence)
    store.create_work(WorkItem("slot", "protect port pickup window"), fence=fence)
    store.create_work(WorkItem("receive", "protect warehouse receiving window"), fence=fence)

    for work_id, actor in (("route", "transport-agent"),
                           ("slot", "port-agent"),
                           ("receive", "warehouse-agent")):
        if not store.claim(work_id, actor, fence):
            store.emit("ABORT", "coordinator", {"reason": f"could not claim {work_id}"})
            return store.snapshot()

    # Each bounded actor reports the constraint only it can see.
    store.emit("CONSTRAINT_REPORTED", "port-agent",
               {"handover_hours": f["port_handover_hours"],
                "booked_pickup_hour": f["booked_pickup_hour"]})
    store.emit("CONSTRAINT_REPORTED", "warehouse-agent",
               {"open": f["warehouse_open_hour"], "close": f["warehouse_close_hour"],
                "shift_change": f["warehouse_shift_change_hour"]})

    verifier = make_verifier(store, series, routes)

    # 1. Business as usual: honour the booked slot.
    booked = f["booked_pickup_hour"]
    booked_route = routes.estimate(f["port"], f["warehouse"], booked)
    store.add_plan(_plan("stormslot-plan-1", booked, booked_route.minutes, "booked slot",
                        revision), fence)
    if verify_and_commit(store, "stormslot-plan-1", verifier, fence):
        return store.snapshot()

    # 2. Rejected. Replan: least deviation from the booking that survives verification.
    store.emit("REPLAN_STARTED", "transport-agent",
               {"reason": store.get_plan("stormslot-plan-1").rejection_reason})
    candidates = sorted(
        (h for h in f["port_handover_hours"]
         if f["container_ready_hour"] <= h <= f["pickup_deadline_hour"]
         and h >= f["truck_available_hour"] and h != booked),
        key=lambda h: (abs(h - booked), h),
    )
    for n, hour in enumerate(candidates, start=2):
        est = routes.estimate(f["port"], f["warehouse"], hour)
        pid = f"stormslot-plan-{n}"
        store.add_plan(_plan(pid, hour, est.minutes, "storm-avoiding slot", revision),
                       fence)
        if verify_and_commit(store, pid, verifier, fence):
            store.emit("SLOT_REBOOKED", "port-agent",
                       {"from_hour": booked, "to_hour": hour, "cause": "severe weather"})
            return store.snapshot()

    store.emit("NO_FEASIBLE_PLAN", "coordinator",
               {"searched_hours": candidates, "severe_hours": bad})
    return store.snapshot()


def disrupt(store: Store, weather: WeatherProvider, routes: RouteProvider,
            event_id: Optional[str] = None) -> dict:
    """New forecast arrives. Re-verify the commitment; replan if it no longer holds."""
    f = store.state.facts
    series = weather.hourly(f["port"])
    bad = severe_hours(series, f["storm_threshold_rain_mm"])

    # New truth has landed: everything computed before this point is stale. This
    # call is also the deduplication point, so nothing above it writes to the
    # trace -- a redelivered message must leave no mark at all, not a weather
    # event followed by a refusal.
    lease = store.advance_revision(
        "weather-agent", "forecast updated", {"location": f["port"]},
        event_id=event_id)
    if lease is None:
        return store.snapshot()
    fence = lease.fence

    try:
        store.emit("WEATHER_UPDATED", "weather-agent",
                   {"location": f["port"], "severe_hours": bad})
        verifier = make_verifier(store, series, routes)
        if not reverify_committed(store, verifier, fence):
            run(store, weather, routes, fence)
    except SupersededWorkerError:
        # Another attempt owns this event now. Reporting failure would abandon
        # an application that is not ours and invite a third worker in.
        raise
    except Exception as exc:
        # The revision has already advanced; this application did not finish.
        # Say so, so the redelivery that follows repairs the run immediately
        # instead of being dismissed as contention with a dead worker.
        store.abandon_event(event_id, "weather-agent",
                            f"{type(exc).__name__}: {exc}", fence)
        raise
    store.complete_event(event_id, "weather-agent", fence)
    return store.snapshot()
