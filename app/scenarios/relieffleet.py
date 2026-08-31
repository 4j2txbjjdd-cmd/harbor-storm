"""ReliefFleet: many missions, few vehicles, one changing mountain.

The multi-mission extension of ReliefRun, on the same membrane. Three relief
missions compete for two trucks and one helicopter across shared convoy
windows, a shared hospital-bed pool, and a road network whose edges can fail.
The candidate plan is the *fleet assignment* — every mission mapped to a
vehicle and a departure window — so one committed plan is the current
operational truth for the whole fleet, revision-fenced like any other plan.

What the world can do to a committed assignment:
- a hazard pulse can close windows for trucks (water hazard) or the
  helicopter (wind aloft) on different limits;
- a bridge can fail, cutting every road route that crosses it.

Either arrives through the same disruption path as everywhere else: advance
the revision, re-verify the committed assignment, revoke it on a named
physical reason, replan, commit. Two agencies cannot allocate the same
vehicle twice: vehicles are claimable work items (a losing claim is refused
on the record, naming the holder), and the verifier independently rejects
any assignment that reuses a (vehicle, window) pair.

Data is seeded and fictional; the mechanism is what transfers.
"""
from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple

from app.core.models import OperationalState, WorkItem, CandidatePlan
from app.core.store import Store, SupersededWorkerError, FenceArg, UNFENCED
from app.core.verify import verify_and_commit, reverify_committed
from app.providers.weather import WeatherProvider, WeatherPoint


def build_state() -> OperationalState:
    return OperationalState(
        scenario="relieffleet",
        facts={
            "corridor": "CORRIDOR_A",
            # Road topology: named edges, and per-village truck routes over
            # them. The helicopter needs no edges. An edge that is not
            # "intact" cuts every truck route that crosses it.
            "edges": {"BR1": "intact", "RD2": "intact", "RD3": "intact",
                      "RD4": "intact"},
            "villages": {
                "VILLAGE_X": {"truck_route": ["RD2", "RD3"]},
                "VILLAGE_Y": {"truck_route": ["BR1"]},
                "VILLAGE_Z": {"truck_route": ["RD4"]},
            },
            "vehicles": {
                "TRUCK_1": {"kind": "truck", "capacity_kg": 600},
                "TRUCK_2": {"kind": "truck", "capacity_kg": 400},
                "HELI_1": {"kind": "heli", "capacity_kg": 200},
            },
            "missions": [
                {"id": "M1", "village": "VILLAGE_X", "payload_kg": 400,
                 "casualties": 8},
                {"id": "M2", "village": "VILLAGE_Y", "payload_kg": 180,
                 "casualties": 3},
                {"id": "M3", "village": "VILLAGE_Z", "payload_kg": 150,
                 "casualties": 2},
            ],
            "mission_ready_hour": 6,
            "latest_departure_hour": 16,
            "departure_windows": [6, 9, 12, 15],
            "corridor_access_cutoff_hour": 18,
            "transit_hours": {"truck": 2.0, "heli": 1.0},
            "hospital_beds_free": 14,
            # Different physics, different limits: trucks are stopped by
            # water on the road, the helicopter by wind aloft.
            "truck_max_hazard_mm": 15.0,
            "heli_max_wind_kph": 30.0,
        },
        target={"all_missions_delivered_within_access": True},
    )


def transit_span(depart_hour: float, duration_hours: float) -> List[int]:
    return list(range(int(math.floor(depart_hour)),
                      int(math.ceil(depart_hour + duration_hours))))


def plan_id_for(basis_revision: int, n: int) -> str:
    """Plan identity is revision-qualified: the same proposal number at a
    later world revision is a different proposal, and the trace should never
    hold one id naming two objects."""
    return f"fleet-r{basis_revision}-p{n}"


def current_edges(store: Store) -> Dict[str, str]:
    """Edge state, derived from the record rather than held in mutable state.

    The seeded baseline lives in facts; every failure afterwards is an
    EDGE_FAILED event on the trace. Folding the two gives the same answer on
    any backend, because the trace is durable everywhere — facts are never
    mutated, so there is nothing to silently lose on a re-read.
    """
    edges = dict(store.state.facts["edges"])
    for e in store.trace():
        if e["kind"] == "EDGE_FAILED":
            edges[e["payload"]["edge"]] = "failed"
    return edges


def _assignments_of(plan: CandidatePlan) -> List[dict]:
    return [a for a in plan.actions if a.get("type") == "assign"]


def _vehicle_unsafe_reason(kind: str, hour: int,
                           by_c: Dict[int, WeatherPoint], f: dict) -> Optional[str]:
    p = by_c.get(hour)
    if p is None:
        return f"no corridor hazard reading for hour {hour}"
    if kind == "truck" and p.rain_mm > f["truck_max_hazard_mm"]:
        return (f"corridor water hazard {p.rain_mm} mm over truck limit "
                f"at hour {hour}")
    if kind == "heli" and p.wind_kph > f["heli_max_wind_kph"]:
        return f"corridor wind {p.wind_kph} kph over helicopter limit at hour {hour}"
    return None


def _route_blocked_reason(village: str, kind: str, f: dict,
                          edges: Dict[str, str]) -> Optional[str]:
    if kind != "truck":
        return None
    for edge in f["villages"][village]["truck_route"]:
        if edges.get(edge) != "intact":
            return f"route to {village} crosses failed edge {edge}"
    return None


def make_verifier(store: Store, corridor: List[WeatherPoint]):
    """Deterministic feasibility of an entire fleet assignment.

    Recomputes everything from authoritative facts: per-vehicle capacity and
    physics limits, road-edge integrity, window discipline, bed totals, and
    (vehicle, window) uniqueness. The plan's own metrics are ignored.
    """
    f = store.state.facts
    by_c = {p.hour: p for p in corridor}

    def verifier(plan: CandidatePlan) -> Tuple[bool, str]:
        edges = current_edges(store)
        assigns = _assignments_of(plan)
        if not assigns:
            return False, "plan assigns no missions"
        missions = {m["id"]: m for m in f["missions"]}
        seen_missions, used_slots = set(), set()
        total_casualties = 0

        for a in assigns:
            mid, vid, window = a.get("mission"), a.get("vehicle"), a.get("window")
            m = missions.get(mid)
            if m is None:
                return False, f"unknown mission {mid!r}"
            if mid in seen_missions:
                return False, f"mission {mid} assigned twice"
            seen_missions.add(mid)
            v = f["vehicles"].get(vid)
            if v is None:
                return False, f"unknown vehicle {vid!r}"
            if (vid, window) in used_slots:
                return False, (f"vehicle {vid} assigned twice in the "
                               f"{window}:00 window")
            used_slots.add((vid, window))

            if m["payload_kg"] > v["capacity_kg"]:
                return False, (f"mission {mid} payload {m['payload_kg']} kg "
                               f"exceeds {vid} capacity {v['capacity_kg']} kg")
            if window not in f["departure_windows"]:
                return False, f"no convoy window at {window}:00"
            if window < f["mission_ready_hour"]:
                return False, f"missions not ready until {f['mission_ready_hour']}:00"
            if window > f["latest_departure_hour"]:
                return False, "past latest departure hour"

            duration = f["transit_hours"][v["kind"]]
            if window + duration > f["corridor_access_cutoff_hour"]:
                return False, "corridor access cutoff passed"

            blocked = _route_blocked_reason(m["village"], v["kind"], f, edges)
            if blocked:
                return False, blocked
            for hour in transit_span(window, duration):
                reason = _vehicle_unsafe_reason(v["kind"], hour, by_c, f)
                if reason:
                    return False, reason

            total_casualties += m["casualties"]

        missing = sorted(set(missions) - seen_missions)
        if missing:
            return False, f"missions left unassigned: {missing}"
        if total_casualties > f["hospital_beds_free"]:
            return False, (f"{total_casualties} casualties exceed "
                           f"{f['hospital_beds_free']} free beds")
        return True, "feasible"

    return verifier


def propose_assignment(f: dict, corridor: List[WeatherPoint],
                       edges: Dict[str, str], plan_id: str,
                       basis_revision: int) -> CandidatePlan:
    """Greedy deterministic planner: most casualties first, earliest feasible
    (vehicle, window) pair. Confident, not authoritative — the verifier
    recomputes everything it asserts."""
    by_c = {p.hour: p for p in corridor}
    used_slots = set()
    actions = []
    order = sorted(f["missions"],
                   key=lambda m: (-m["casualties"], m["id"]))
    for m in order:
        placed = False
        for window in f["departure_windows"]:
            if placed or window < f["mission_ready_hour"] \
               or window > f["latest_departure_hour"]:
                continue
            for vid in sorted(f["vehicles"]):
                v = f["vehicles"][vid]
                if (vid, window) in used_slots:
                    continue
                if m["payload_kg"] > v["capacity_kg"]:
                    continue
                if _route_blocked_reason(m["village"], v["kind"], f, edges):
                    continue
                duration = f["transit_hours"][v["kind"]]
                if window + duration > f["corridor_access_cutoff_hour"]:
                    continue
                if any(_vehicle_unsafe_reason(v["kind"], h, by_c, f)
                       for h in transit_span(window, duration)):
                    continue
                used_slots.add((vid, window))
                actions.append({"type": "assign", "mission": m["id"],
                                "vehicle": vid, "window": window})
                placed = True
                break
        # An unplaced mission is left out; the verifier names it, and the
        # refusal goes on the record instead of a silent partial plan.
    return CandidatePlan(
        id=plan_id,
        scenario="relieffleet",
        created_by="fleet-planner",
        actions=actions,
        metrics={"missions_assigned": float(len(actions))},
        basis_revision=basis_revision,
    )


def _naive_assignment(f: dict, plan_id: str, basis_revision: int) -> CandidatePlan:
    """The plan on the board before anyone reads the hazard: everything
    departs at first light on the biggest vehicles. Confidently wrong under
    the seeded forecast, which is the point."""
    actions, vehicles = [], sorted(f["vehicles"],
                                   key=lambda v: -f["vehicles"][v]["capacity_kg"])
    for m, vid in zip(sorted(f["missions"], key=lambda m: m["id"]), vehicles):
        actions.append({"type": "assign", "mission": m["id"], "vehicle": vid,
                        "window": f["mission_ready_hour"]})
    return CandidatePlan(id=plan_id, scenario="relieffleet",
                         created_by="fleet-planner", actions=actions,
                         metrics={"missions_assigned": float(len(actions))},
                         basis_revision=basis_revision)


ACTOR_WORK = (("vehicle-TRUCK_1", "ground-team-1"),
              ("vehicle-TRUCK_2", "ground-team-2"),
              ("vehicle-HELI_1", "air-team"))


def seed(store: Store, weather: WeatherProvider, fence: FenceArg = UNFENCED,
         claim_for=ACTOR_WORK):
    """Measure the hazard, open one work item per vehicle, publish constraints.

    Vehicles are claimable resources: whoever holds the claim dispatches.
    A second agency trying to take a held vehicle is refused on the record,
    with the holder named — that is the anti-double-allocation mechanism at
    the claim layer; the verifier enforces it again at the plan layer.
    """
    f = store.state.facts
    corridor = weather.hourly(f["corridor"])
    by_c = {p.hour: p for p in corridor}
    unsafe_truck = [h for h in sorted(by_c)
                    if _vehicle_unsafe_reason("truck", h, by_c, f)]
    unsafe_heli = [h for h in sorted(by_c)
                   if _vehicle_unsafe_reason("heli", h, by_c, f)]
    store.emit("HAZARD_MEASURED", "weather-agent", {
        "corridor": f["corridor"],
        "truck_unsafe_hours": unsafe_truck,
        "heli_unsafe_hours": unsafe_heli,
    })

    for work_id, _actor in claim_for:
        store.create_work(WorkItem(work_id, f"dispatch {work_id}"), fence=fence)
    for work_id, actor in claim_for:
        if not store.claim(work_id, actor, fence):
            store.emit("ABORT", "coordinator",
                       {"reason": f"could not claim {work_id}"})
            return None

    store.emit("CONSTRAINT_REPORTED", "logistics-agent",
               {"missions": f["missions"],
                "vehicles": {k: v["capacity_kg"]
                             for k, v in f["vehicles"].items()}})
    store.emit("CONSTRAINT_REPORTED", "dispatch-agent",
               {"departure_windows": f["departure_windows"],
                "hospital_beds_free": f["hospital_beds_free"],
                "edges": current_edges(store),
                "corridor_access_cutoff_hour": f["corridor_access_cutoff_hour"]})
    return corridor


def run(store: Store, weather: WeatherProvider,
        fence: FenceArg = UNFENCED) -> dict:
    f = store.state.facts
    revision = store.state.revision
    corridor = seed(store, weather, fence)
    if corridor is None:
        return store.snapshot()

    verifier = make_verifier(store, corridor)

    # 1. The board as it stands: everything at first light.
    first = plan_id_for(revision, 1)
    store.add_plan(_naive_assignment(f, first, revision), fence)
    if verify_and_commit(store, first, verifier, fence):
        return store.snapshot()

    # 2. Refused on a named reason. Replan from the hazard.
    store.emit("REPLAN_STARTED", "fleet-planner",
               {"reason": store.get_plan(first).rejection_reason})
    second = plan_id_for(revision, 2)
    store.add_plan(propose_assignment(f, corridor, current_edges(store),
                                      second, revision), fence)
    if verify_and_commit(store, second, verifier, fence):
        assigns = _assignments_of(store.get_plan(second))
        store.emit("FLEET_DISPATCH_SET", "dispatch-agent",
                   {"assignments": assigns})
        return store.snapshot()

    store.emit("NO_FEASIBLE_FLEET_PLAN", "coordinator", {})
    return store.snapshot()


def disrupt(store: Store, weather: WeatherProvider,
            failed_edges: Optional[List[str]] = None,
            event_id: Optional[str] = None) -> dict:
    """New truth: a fresh hazard reading, and optionally failed road edges.

    Same contract as every other disruption path: this call is the
    deduplication point; a redelivered event leaves no mark. Edge failures
    are applied under the same revision advance as the observation, because
    they are one piece of new truth about the same world.
    """
    f = store.state.facts
    corridor = weather.hourly(f["corridor"])

    lease = store.advance_revision(
        "weather-agent", "corridor hazard updated",
        {"corridor": f["corridor"], "failed_edges": failed_edges or []},
        event_id=event_id)
    if lease is None:
        return store.snapshot()
    fence = lease.fence

    try:
        for edge in failed_edges or []:
            store.emit("EDGE_FAILED", "hazard-agent", {"edge": edge})
        by_c = {p.hour: p for p in corridor}
        store.emit("HAZARD_UPDATED", "weather-agent", {
            "corridor": f["corridor"],
            "truck_unsafe_hours": [h for h in sorted(by_c)
                                   if _vehicle_unsafe_reason("truck", h, by_c, f)],
            "heli_unsafe_hours": [h for h in sorted(by_c)
                                  if _vehicle_unsafe_reason("heli", h, by_c, f)],
        })
        verifier = make_verifier(store, corridor)
        if not reverify_committed(store, verifier, fence):
            revision = store.state.revision
            store.emit("REPLAN_STARTED", "fleet-planner",
                       {"reason": "committed assignment lost authority"})
            n = len(store.snapshot()["plans"]) + 1
            pid = plan_id_for(revision, n)
            store.add_plan(propose_assignment(f, corridor, current_edges(store),
                                              pid, revision), fence)
            if verify_and_commit(store, pid, verifier, fence):
                store.emit("FLEET_DISPATCH_SET", "dispatch-agent",
                           {"assignments": _assignments_of(store.get_plan(pid))})
            else:
                store.emit("NO_FEASIBLE_FLEET_PLAN", "coordinator", {})
    except SupersededWorkerError:
        raise
    except Exception as exc:
        store.abandon_event(event_id, "weather-agent",
                            f"{type(exc).__name__}: {exc}", fence)
        raise
    store.complete_event(event_id, "weather-agent", fence)
    return store.snapshot()
