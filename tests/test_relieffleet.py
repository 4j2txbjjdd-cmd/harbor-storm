"""ReliefFleet: the multi-mission instantiation, held to the same gates.

The fleet plan is one candidate like any other: the whole assignment is
recomputed from authoritative facts, a hazard or a failed bridge revokes a
committed assignment on a named reason, and two agencies cannot hold the
same vehicle — refused at the claim layer with the holder named, and
refused again at the plan layer if an assignment reuses a slot.
"""
import pytest

from app.core.store import InMemoryStateStore, UNFENCED
from app.core.models import CandidatePlan
from app.fleet_demo import (calm_weather_fixture, disrupted_weather_fixture,
                            weather_fixture)
from app.scenarios import relieffleet

AUTHORITATIVE = ("PLAN_VERIFIED", "PLAN_COMMITTED", "PLAN_REJECTED",
                 "COMMIT_REVOKED")


def kinds(store):
    return [e.kind for e in store.events]


def shape(store):
    return [(e.kind, e.actor, e.payload) for e in store.events]


def fresh_store():
    return InMemoryStateStore(relieffleet.build_state())


def assignments(store, plan_id):
    plan = store.snapshot()["plans"][plan_id]
    return {a["mission"]: (a["vehicle"], a["window"])
            for a in plan["actions"] if a["type"] == "assign"}


# --- hazard is load-bearing ------------------------------------------


def test_calm_forecast_keeps_the_first_light_dispatch():
    store = fresh_store()
    snap = relieffleet.run(store, calm_weather_fixture())
    assert snap["committed_plan_id"] == "fleet-r0-p1"
    assert "PLAN_REJECTED" not in kinds(store)
    # Everything departs at first light on the naive board.
    assert all(w == 6 for _, w in assignments(store, "fleet-r0-p1").values())


def test_hazard_moves_the_fleet_on_a_named_reason():
    store = fresh_store()
    snap = relieffleet.run(store, weather_fixture())
    assert snap["committed_plan_id"] == "fleet-r0-p2"

    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert rejected[0].payload["plan_id"] == "fleet-r0-p1"
    assert "over truck limit at hour 6" in rejected[0].payload["reason"]

    got = assignments(store, "fleet-r0-p2")
    assert got == {"M1": ("TRUCK_1", 9), "M2": ("HELI_1", 9),
                   "M3": ("TRUCK_2", 9)}


# --- the verifier rejects structural double-allocation ----------------


def test_reused_vehicle_window_slot_is_refused():
    store = fresh_store()
    corridor = calm_weather_fixture().hourly("CORRIDOR_A")
    verifier = relieffleet.make_verifier(store, corridor)
    plan = CandidatePlan(
        id="bad", scenario="relieffleet", created_by="fleet-planner",
        actions=[
            {"type": "assign", "mission": "M1", "vehicle": "TRUCK_1", "window": 9},
            {"type": "assign", "mission": "M2", "vehicle": "TRUCK_1", "window": 9},
            {"type": "assign", "mission": "M3", "vehicle": "TRUCK_2", "window": 9},
        ],
        metrics={}, basis_revision=0)
    ok, reason = verifier(plan)
    assert not ok
    assert "TRUCK_1 assigned twice in the 9:00 window" in reason


def test_unassigned_mission_is_named():
    store = fresh_store()
    corridor = calm_weather_fixture().hourly("CORRIDOR_A")
    verifier = relieffleet.make_verifier(store, corridor)
    plan = CandidatePlan(
        id="partial", scenario="relieffleet", created_by="fleet-planner",
        actions=[
            {"type": "assign", "mission": "M1", "vehicle": "TRUCK_1", "window": 9},
            {"type": "assign", "mission": "M2", "vehicle": "HELI_1", "window": 9},
        ],
        metrics={}, basis_revision=0)
    ok, reason = verifier(plan)
    assert not ok
    assert "missions left unassigned: ['M3']" in reason


def test_beds_pool_is_a_fleet_wide_constraint():
    store = fresh_store()
    store.state.facts["hospital_beds_free"] = 10  # 8+3+2 = 13 > 10
    snap = relieffleet.run(store, calm_weather_fixture())
    assert snap["committed_plan_id"] is None
    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert "13 casualties exceed 10 free beds" in rejected[-1].payload["reason"]


# --- claims: two agencies, one helicopter ----------------------------


def test_second_agency_cannot_take_a_held_vehicle():
    store = fresh_store()
    relieffleet.run(store, weather_fixture())
    took = store.claim("vehicle-HELI_1", "agency-bravo", UNFENCED)
    assert took is False
    refused = [e for e in store.events if e.kind == "CLAIM_REFUSED"]
    assert refused[-1].payload["work_id"] == "vehicle-HELI_1"
    assert refused[-1].payload["current_claimant"] == "air-team"


# --- the second surge: revoke and reallocate --------------------------


def test_bridge_failure_and_pulse_revoke_and_reallocate():
    store = fresh_store()
    snap = relieffleet.run(store, weather_fixture())
    before = assignments(store, snap["committed_plan_id"])
    assert before["M2"] == ("HELI_1", 9)

    snap = relieffleet.disrupt(store, disrupted_weather_fixture(),
                               failed_edges=["BR1"])

    revoked = [e for e in store.events if e.kind == "COMMIT_REVOKED"]
    assert revoked[0].payload["plan_id"] == "fleet-r0-p2"
    assert "over truck limit at hour 9" in revoked[0].payload["reason"]
    assert "EDGE_FAILED" in kinds(store)

    after = assignments(store, snap["committed_plan_id"])
    # The bridge village flies in the same window, the heaviest mission waits
    # out the surge until noon, and the helicopter flies a second sortie at
    # noon rather than waking a third crew.
    assert after == {"M2": ("HELI_1", 9), "M1": ("TRUCK_1", 12),
                     "M3": ("HELI_1", 12)}


def test_authority_stays_with_the_verifier():
    store = fresh_store()
    relieffleet.run(store, weather_fixture())
    relieffleet.disrupt(store, disrupted_weather_fixture(),
                        failed_edges=["BR1"])
    trace = store.trace()
    for e in trace:
        if e["kind"] in AUTHORITATIVE:
            assert e["actor"] == "verifier"
    for i, e in enumerate(trace):
        if e["kind"] == "PLAN_COMMITTED":
            assert any(p["kind"] == "PLAN_VERIFIED"
                       and p["payload"]["plan_id"] == e["payload"]["plan_id"]
                       for p in trace[:i])


# --- deterministic replay --------------------------------------------


def test_seeded_lane_replays_identically_including_the_surge():
    def one_run():
        store = fresh_store()
        relieffleet.run(store, weather_fixture())
        relieffleet.disrupt(store, disrupted_weather_fixture(),
                            failed_edges=["BR1"])
        return shape(store), store.snapshot()["committed_plan_id"]

    a, b = one_run(), one_run()
    assert a == b
    assert a[1] == "fleet-r1-p3"


def test_redelivered_surge_applies_at_most_once():
    store = fresh_store()
    relieffleet.run(store, weather_fixture())
    relieffleet.disrupt(store, disrupted_weather_fixture(),
                        failed_edges=["BR1"], event_id="surge-1")
    after = len(store.trace())
    snap = relieffleet.disrupt(store, disrupted_weather_fixture(),
                               failed_edges=["BR1"], event_id="surge-1")
    assert [e["kind"] for e in store.trace()[after:]] == ["DUPLICATE_EVENT_IGNORED"]
    assert snap["committed_plan_id"] == "fleet-r1-p3"


# --- edge state is derived from the record, never held in mutable state


def test_edge_failures_live_on_the_trace_not_in_facts():
    """The auditor's finding, kept as a contract: facts are never mutated,
    so a backend that re-reads state from storage loses nothing — the
    failure is reconstructed from the durable trace on any backend."""
    store = fresh_store()
    relieffleet.run(store, weather_fixture())
    relieffleet.disrupt(store, disrupted_weather_fixture(),
                        failed_edges=["BR1"])
    assert store.state.facts["edges"]["BR1"] == "intact"   # untouched baseline
    assert relieffleet.current_edges(store)["BR1"] == "failed"
    rebuilt = dict(relieffleet.build_state().facts["edges"])
    for e in store.trace():
        if e["kind"] == "EDGE_FAILED":
            rebuilt[e["payload"]["edge"]] = "failed"
    assert rebuilt == relieffleet.current_edges(store)
