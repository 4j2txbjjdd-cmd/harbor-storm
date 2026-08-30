"""ReliefRun: one relief mission up a mountain corridor after a flood surge.

Third instantiation of the substrate, informed by the dynamics of the
August 2026 Nepal floods: a glacier collapse and landslide sent a surge down
a border valley, took out the road corridor, and left an upstream barrier
lake whose breach risk could invalidate any mission planned after the first
flood. The data here is seeded and fictional -- abstract corridor, village
and hospital -- because what transfers is the mechanism, not the event: a
rescue mission can be correct when planned and lethal at dispatch, and the
system has to refuse it before it becomes action.

Mapping onto the shared substrate:
- sailing slots        -> convoy departure windows on the corridor
- marine weather       -> corridor hazard (wind aloft for air support, and a
                          water-hazard index carried on the provider's rain
                          field: rain-driven slope failure and surge risk)
- cargo vs capacity    -> relief payload vs vehicle capacity, and casualties
                          to extract vs hospital beds
- landing cutoff       -> corridor access cutoff (last light)
- CandidatePlan        -> the mission itself
- atomic claims        -> two teams cannot allocate the same vehicle

Three bounded actors with disjoint information, exactly as in the other two
scenarios: no one of them can clear a mission, because no one of them can
see the whole constraint.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

from app.core.models import OperationalState, WorkItem, CandidatePlan
from app.core.store import Fence, Store, SupersededWorkerError, FenceArg, UNFENCED
from app.core.verify import verify_and_commit, reverify_committed
from app.providers.weather import WeatherProvider, WeatherPoint


def build_state() -> OperationalState:
    return OperationalState(
        scenario="reliefrun",
        facts={
            "corridor": "CORRIDOR_A",
            "village": "VILLAGE_X",
            "mission_ready_hour": 6,
            # The mission currently on the board: depart at first light.
            "booked_departure_hour": 6,
            "latest_departure_hour": 16,
            "transit_hours": 2.0,
            # ops-agent scope: escorted convoy windows on the one open corridor
            "departure_windows": [6, 9, 12, 15],
            "corridor_access_cutoff_hour": 18,
            # logistics-agent scope
            "vehicle_capacity_kg": 600,
            "payload_kg": 400,
            "hospital_beds_free": 12,
            "casualties_to_extract": 8,
            # hazard-agent scope: the limits the hazard series is read against
            "max_wind_kph": 40.0,
            "max_hazard_mm": 15.0,
        },
        target={"relief_delivered_and_casualties_out": True},
    )


def transit_span(depart_hour: float, duration_hours: float) -> List[int]:
    """Every clock hour the mission is on the corridor, departure included."""
    return list(range(int(math.floor(depart_hour)),
                      int(math.ceil(depart_hour + duration_hours))))


def _departure_of(plan: CandidatePlan) -> Optional[int]:
    for a in plan.actions:
        if a.get("type") == "depart":
            return int(a["hour"])
    return None


def _unsafe_reason(hour: int, by_c: Dict[int, WeatherPoint],
                   by_v: Dict[int, WeatherPoint], f: dict) -> Optional[str]:
    for label, table in (("corridor", by_c), ("village", by_v)):
        p = table.get(hour)
        if p is None:
            return f"no {label} hazard reading for hour {hour}"
        if p.wind_kph > f["max_wind_kph"]:
            return f"{label} wind {p.wind_kph} kph over limit at hour {hour}"
        if p.rain_mm > f["max_hazard_mm"]:
            return (f"{label} water hazard {p.rain_mm} mm over limit "
                    f"at hour {hour}")
    return None


def make_verifier(store: Store, corridor: List[WeatherPoint],
                  village: List[WeatherPoint]):
    """Deterministic feasibility check for a proposed mission.

    Recomputes the whole transit from authoritative facts. The plan's own
    metrics are ignored, so an over-confident proposal still fails.
    """
    f = store.state.facts
    by_c = {p.hour: p for p in corridor}
    by_v = {p.hour: p for p in village}

    def verifier(plan: CandidatePlan) -> Tuple[bool, str]:
        depart = _departure_of(plan)
        if depart is None:
            return False, "mission proposes no departure"
        if f["payload_kg"] > f["vehicle_capacity_kg"]:
            return False, (f"payload {f['payload_kg']} kg exceeds vehicle "
                           f"capacity {f['vehicle_capacity_kg']} kg")
        if f["casualties_to_extract"] > f["hospital_beds_free"]:
            return False, (f"{f['casualties_to_extract']} casualties exceed "
                           f"{f['hospital_beds_free']} free beds")
        if depart < f["mission_ready_hour"]:
            return False, f"mission not ready until {f['mission_ready_hour']}:00"
        if depart > f["latest_departure_hour"]:
            return False, "past latest departure hour"
        if depart not in f["departure_windows"]:
            return False, f"no convoy window at {depart}:00"

        arrival = depart + f["transit_hours"]
        if arrival > f["corridor_access_cutoff_hour"]:
            return False, "corridor access cutoff passed"

        for hour in transit_span(depart, f["transit_hours"]):
            reason = _unsafe_reason(hour, by_c, by_v, f)
            if reason:
                return False, reason
        return True, "feasible"

    return verifier


def _plan(plan_id: str, depart: int, f: dict, basis_revision: int) -> CandidatePlan:
    return CandidatePlan(
        id=plan_id,
        scenario="reliefrun",
        created_by="hazard-agent",
        actions=[
            {"type": "reserve_vehicle", "departure_hour": depart},
            {"type": "load_relief", "kg": f["payload_kg"]},
            {"type": "assign_beds", "count": f["casualties_to_extract"]},
            {"type": "depart", "hour": depart},
        ],
        metrics={
            "departure_hour": float(depart),
            "arrival_hour": depart + f["transit_hours"],
            "payload_kg": float(f["payload_kg"]),
            "casualties": float(f["casualties_to_extract"]),
        },
        basis_revision=basis_revision,
    )


# Which actor is answerable for which work item; same contract as the other
# scenarios -- a caller driving one of these actors itself passes a subset so
# the actor claims its own work through the tool surface.
ACTOR_WORK = (("corridor", "hazard-agent"),
              ("supplies", "logistics-agent"),
              ("mission-slot", "ops-agent"))


def seed(store: Store, weather: WeatherProvider, fence: FenceArg = UNFENCED,
         claim_for=ACTOR_WORK):
    """Measure the corridor hazard, open the work items, publish constraints.

    The constraint reports are the only channel between scopes:
    `hazard-agent` cannot read `departure_windows`; it reads the trace where
    `ops-agent` published them. That indirection is the operation.
    """
    f = store.state.facts
    corridor = weather.hourly(f["corridor"])
    village = weather.hourly(f["village"])
    by_c = {p.hour: p for p in corridor}
    by_v = {p.hour: p for p in village}
    covered = sorted(set(by_c) & set(by_v))
    unsafe = [h for h in covered if _unsafe_reason(h, by_c, by_v, f)]
    store.emit("HAZARD_MEASURED", "weather-agent", {
        "corridor": f["corridor"], "village": f["village"],
        "max_wind_kph": f["max_wind_kph"], "max_hazard_mm": f["max_hazard_mm"],
        "corridor_hours": covered,
        "severe_hours": unsafe,
    })

    store.create_work(WorkItem("corridor", "find safe transit window"), fence=fence)
    store.create_work(WorkItem("supplies", "check payload/vehicle feasibility"),
                      fence=fence)
    store.create_work(WorkItem("mission-slot", "confirm convoy window and beds"),
                      fence=fence)

    for work_id, actor in claim_for:
        if not store.claim(work_id, actor, fence):
            store.emit("ABORT", "coordinator", {"reason": f"could not claim {work_id}"})
            return None

    store.emit("CONSTRAINT_REPORTED", "logistics-agent",
               {"payload_kg": f["payload_kg"],
                "vehicle_capacity_kg": f["vehicle_capacity_kg"]})
    store.emit("CONSTRAINT_REPORTED", "ops-agent",
               {"departure_windows": f["departure_windows"],
                "booked_departure_hour": f["booked_departure_hour"],
                "hospital_beds_free": f["hospital_beds_free"],
                "corridor_access_cutoff_hour": f["corridor_access_cutoff_hour"]})
    return corridor, village


def run(store: Store, weather: WeatherProvider,
        fence: FenceArg = UNFENCED) -> dict:
    f = store.state.facts
    # Every mission below is computed against this revision and binds to it.
    revision = store.state.revision
    seeded = seed(store, weather, fence)
    if seeded is None:
        return store.snapshot()
    corridor, village = seeded

    verifier = make_verifier(store, corridor, village)

    # 1. The mission on the board: depart at first light.
    booked = f["booked_departure_hour"]
    store.add_plan(_plan("relief-plan-1", booked, f, revision), fence)
    if verify_and_commit(store, "relief-plan-1", verifier, fence):
        return store.snapshot()

    # 2. Refused. Replan: least deviation from the booked departure that
    # verifies -- every hour of delay is casualties waiting.
    store.emit("REPLAN_STARTED", "hazard-agent",
               {"reason": store.get_plan("relief-plan-1").rejection_reason})
    candidates = sorted(
        (h for h in f["departure_windows"]
         if f["mission_ready_hour"] <= h <= f["latest_departure_hour"]
         and h != booked),
        key=lambda h: (abs(h - booked), h),
    )
    for n, hour in enumerate(candidates, start=2):
        pid = f"relief-plan-{n}"
        store.add_plan(_plan(pid, hour, f, revision), fence)
        if verify_and_commit(store, pid, verifier, fence):
            store.emit("MISSION_RESCHEDULED", "ops-agent",
                       {"from_hour": booked, "to_hour": hour,
                        "cause": "corridor hazard"})
            return store.snapshot()

    store.emit("NO_FEASIBLE_MISSION", "coordinator", {"searched_hours": candidates})
    return store.snapshot()


def disrupt(store: Store, weather: WeatherProvider,
            event_id: Optional[str] = None) -> dict:
    """New hazard truth arrives -- a barrier-lake alert, a fresh surge pulse.

    Re-verify the committed mission; replan if it no longer holds. Same
    dedup contract as the other scenarios: this call is the deduplication
    point, and a redelivered message leaves no mark.
    """
    f = store.state.facts
    corridor = weather.hourly(f["corridor"])
    village = weather.hourly(f["village"])
    by_c = {p.hour: p for p in corridor}
    by_v = {p.hour: p for p in village}
    unsafe = [h for h in sorted(set(by_c) & set(by_v))
              if _unsafe_reason(h, by_c, by_v, f)]

    lease = store.advance_revision(
        "weather-agent", "corridor hazard updated",
        {"corridor": f["corridor"], "village": f["village"]}, event_id=event_id)
    if lease is None:
        return store.snapshot()
    fence = lease.fence

    try:
        store.emit("HAZARD_UPDATED", "weather-agent",
                   {"corridor": f["corridor"], "village": f["village"],
                    "severe_hours": unsafe})
        verifier = make_verifier(store, corridor, village)
        if not reverify_committed(store, verifier, fence):
            run(store, weather, fence)
    except SupersededWorkerError:
        raise
    except Exception as exc:
        store.abandon_event(event_id, "weather-agent",
                            f"{type(exc).__name__}: {exc}", fence)
        raise
    store.complete_event(event_id, "weather-agent", fence)
    return store.snapshot()
