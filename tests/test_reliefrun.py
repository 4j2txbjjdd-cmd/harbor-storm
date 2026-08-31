"""ReliefRun: the disaster-relief instantiation, held to the same gates.

The scenario is additive; the membrane is not new. These tests assert the
same five mechanical properties the frozen scenarios are gated on -- the
hazard changes the decision (with a calm-forecast negative control), an
infeasible mission is deterministically refused on a named reason,
authoritative state moves only through the verifier, the barrier-lake alert
revokes a committed mission and the fleet re-commits, and the seeded lane
replays identically -- plus the sentinel carrying it across wall-clock ticks.
"""
import pytest

from app.core.store import InMemoryStateStore
from app.relief_demo import (calm_weather_fixture, disrupted_weather_fixture,
                             weather_fixture)
from app.scenarios import reliefrun
from app.sentinel import Sentinel, observation_fingerprint

AUTHORITATIVE = ("PLAN_VERIFIED", "PLAN_COMMITTED", "PLAN_REJECTED",
                 "COMMIT_REVOKED")


def kinds(store):
    return [e.kind for e in store.events]


def shape(store):
    """Trace stripped of timestamps, for replay comparison."""
    return [(e.kind, e.actor, e.payload) for e in store.events]


def fresh_store():
    return InMemoryStateStore(reliefrun.build_state())


# --- hazard is load-bearing ------------------------------------------


def test_calm_forecast_keeps_the_booked_departure():
    """Negative control: under a calm forecast the first-light mission must
    survive untouched, or the scenario is reacting to something other than
    the hazard."""
    store = fresh_store()
    snap = reliefrun.run(store, calm_weather_fixture())
    assert snap["committed_plan_id"] == "relief-r0-p1"
    assert "MISSION_RESCHEDULED" not in kinds(store)
    assert "PLAN_REJECTED" not in kinds(store)


def test_hazard_moves_the_mission_on_a_named_reason():
    store = fresh_store()
    snap = reliefrun.run(store, weather_fixture())
    assert snap["committed_plan_id"] == "relief-r0-p2"

    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert rejected[0].payload["plan_id"] == "relief-r0-p1"
    assert "water hazard" in rejected[0].payload["reason"]
    assert "hour 6" in rejected[0].payload["reason"]

    moved = [e for e in store.events if e.kind == "MISSION_RESCHEDULED"]
    assert moved[0].payload == {"from_hour": 6, "to_hour": 9,
                               "cause": "corridor hazard"}


# --- infeasibility is refused deterministically ----------------------


def test_overloaded_vehicle_is_refused():
    store = fresh_store()
    store.state.facts["payload_kg"] = 700
    snap = reliefrun.run(store, calm_weather_fixture())
    assert snap["committed_plan_id"] is None
    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert "payload 700 kg exceeds vehicle capacity 600 kg" \
        in rejected[0].payload["reason"]


def test_more_casualties_than_beds_is_refused():
    store = fresh_store()
    store.state.facts["casualties_to_extract"] = 14
    snap = reliefrun.run(store, calm_weather_fixture())
    assert snap["committed_plan_id"] is None
    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert "14 casualties exceed 12 free beds" in rejected[0].payload["reason"]


# --- the membrane ----------------------------------------------------


def test_every_commit_is_preceded_by_verification_and_owned_by_the_verifier():
    store = fresh_store()
    reliefrun.run(store, weather_fixture())
    reliefrun.disrupt(store, disrupted_weather_fixture())

    trace = store.trace()
    for i, e in enumerate(trace):
        if e["kind"] == "PLAN_COMMITTED":
            prior = [p for p in trace[:i]
                     if p["kind"] == "PLAN_VERIFIED"
                     and p["payload"]["plan_id"] == e["payload"]["plan_id"]]
            assert prior, f"{e['payload']['plan_id']} committed unverified"
    for e in trace:
        if e["kind"] in AUTHORITATIVE:
            assert e["actor"] == "verifier"


def test_barrier_lake_alert_revokes_the_committed_mission_and_recommits():
    store = fresh_store()
    snap = reliefrun.run(store, weather_fixture())
    assert snap["committed_plan_id"] == "relief-r0-p2"

    snap = reliefrun.disrupt(store, disrupted_weather_fixture())

    revoked = [e for e in store.events if e.kind == "COMMIT_REVOKED"]
    assert revoked[0].payload["plan_id"] == "relief-r0-p2"
    assert "water hazard" in revoked[0].payload["reason"]
    # The mission that was correct when planned is refused before dispatch,
    # and the fleet re-commits into the next window that verifies.
    assert snap["committed_plan_id"] == "relief-r1-p3"
    moved = [e for e in store.events if e.kind == "MISSION_RESCHEDULED"]
    assert moved[-1].payload["to_hour"] == 12


# --- deterministic replay --------------------------------------------


def test_seeded_lane_replays_identically_including_the_disruption():
    def one_run():
        store = fresh_store()
        reliefrun.run(store, weather_fixture())
        reliefrun.disrupt(store, disrupted_weather_fixture())
        return shape(store), store.snapshot()["committed_plan_id"]

    (shape_a, committed_a), (shape_b, committed_b) = one_run(), one_run()
    assert shape_a == shape_b
    assert committed_a == committed_b == "relief-r1-p3"


# --- bounded actors --------------------------------------------------


def test_three_disjoint_claimants_and_one_proposer():
    store = fresh_store()
    reliefrun.run(store, weather_fixture())
    claimed = {e.payload["work_id"]: e.actor
               for e in store.events if e.kind == "CLAIMED"}
    assert claimed == {"corridor": "hazard-agent",
                       "supplies": "logistics-agent",
                       "mission-slot": "ops-agent"}
    proposers = {e.actor for e in store.events if e.kind == "PLAN_PROPOSED"}
    assert proposers == {"hazard-agent"}


# --- the sentinel carries it across wall-clock time ------------------


class SwappableWeather:
    def __init__(self, current):
        self.current = current

    def hourly(self, location):
        return self.current.hourly(location)


def test_sentinel_watches_reliefrun():
    weather = SwappableWeather(weather_fixture())
    store = fresh_store()
    reliefrun.run(store, weather)
    baseline = observation_fingerprint("reliefrun", store.state.facts, weather)
    sentinel = Sentinel(store, weather, "reliefrun",
                        baseline_fingerprint=baseline)

    quiet = sentinel.tick()
    assert quiet["changed"] is False and quiet["new_events"] == []

    weather.current = disrupted_weather_fixture()
    result = sentinel.tick()
    assert result["revision_advanced"] is True
    assert "COMMIT_REVOKED" in [e["kind"] for e in result["new_events"]]
    assert result["committed_plan_id"] == "relief-r1-p3"

    restarted = Sentinel(store, weather, "reliefrun", baseline_fingerprint=None)
    replay = restarted.tick()
    assert replay["revision_advanced"] is False
    assert [e["kind"] for e in replay["new_events"]] == ["DUPLICATE_EVENT_IGNORED"]


# --- the clock is part of the truth ----------------------------------

from datetime import datetime
from zoneinfo import ZoneInfo

KTM = ZoneInfo("Asia/Kathmandu")


def at(hour, minute=30, day=31):
    return datetime(2026, 8, day, hour, minute, tzinfo=KTM)


def test_a_departure_whose_hour_has_passed_is_revoked_and_replanned():
    """The semantic hole the clock closes: a commitment can be weather-valid
    and still undeliverable, because its dispatch time is in the past."""
    store = fresh_store()
    reliefrun.run(store, weather_fixture())          # commits 9:00 (r0-p2)

    snap = reliefrun.observe(store, weather_fixture(), now=at(11),
                             event_id="obs-day1-11")
    revoked = [e for e in store.events if e.kind == "COMMIT_REVOKED"]
    assert revoked[0].payload["plan_id"] == "relief-r0-p2"
    assert "departure 9:00 has passed (now 11:00)" in revoked[0].payload["reason"]
    assert snap["committed_plan_id"] == "relief-r1-p3"   # 12:00, still future
    assert snap["plans"]["relief-r1-p3"]["metrics"]["departure_hour"] == 12.0


def test_service_day_rolls_over_when_every_window_has_passed():
    store = fresh_store()
    reliefrun.run(store, weather_fixture())
    reliefrun.observe(store, weather_fixture(), now=at(11),
                      event_id="obs-day1-11")         # standing: 12:00

    snap = reliefrun.observe(store, weather_fixture(), now=at(23),
                             event_id="obs-day1-23")
    assert "SERVICE_DAY_ROLLED" in kinds(store)
    revoked = [e for e in store.events if e.kind == "COMMIT_REVOKED"]
    assert "departure 12:00 has passed (now 23:00)" in revoked[-1].payload["reason"]
    # Tomorrow's plan: first light refused on weather, 9:00 commits.
    assert snap["committed_plan_id"] == "relief-r2-p2"
    assert snap["plans"]["relief-r2-p2"]["metrics"]["departure_hour"] == 9.0


def test_same_hour_redelivery_is_deduplicated():
    store = fresh_store()
    reliefrun.run(store, weather_fixture())
    reliefrun.observe(store, weather_fixture(), now=at(11),
                      event_id="obs-day1-11")
    before = store.state.revision
    reliefrun.observe(store, weather_fixture(), now=at(11, minute=55),
                      event_id="obs-day1-11")         # same clock bucket
    assert store.state.revision == before
    assert kinds(store)[-1] == "DUPLICATE_EVENT_IGNORED"


def test_a_run_created_mid_day_does_not_commit_a_passed_window():
    """The clock applies from the first plan, not only from the first
    observation: creating a mission at 10:30 must not commit 6:00 or 9:00."""
    store = fresh_store()
    now = at(10)
    snap = reliefrun.run(
        store, calm_weather_fixture(),
        now_hour=reliefrun.planning_now_hour(store.state.facts, now))
    assert snap["committed_plan_id"] == "relief-r0-p3"
    assert snap["plans"]["relief-r0-p3"]["metrics"]["departure_hour"] == 12.0
    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert "departure 6:00 has passed (now 10:00)" in rejected[0].payload["reason"]


def test_planning_now_hour_rolls_to_none_after_last_window():
    facts = reliefrun.build_state().facts
    assert reliefrun.planning_now_hour(facts, None) is None
    assert reliefrun.planning_now_hour(facts, at(10)) == 10
    assert reliefrun.planning_now_hour(facts, at(23)) is None


def test_sentinel_clock_makes_an_unchanged_forecast_new_truth():
    """Without a clock the sentinel gates on the forecast alone; with one,
    a later hour is a new observation even if the forecast is identical."""
    weather = SwappableWeather(weather_fixture())
    store = fresh_store()
    reliefrun.run(store, weather)
    times = [at(10), at(11)]
    sentinel = Sentinel(store, weather, "reliefrun",
                        clock=lambda: times.pop(0))

    first = sentinel.tick()      # 9:00 mission has passed -> revoke, commit 12:00
    assert first["revision_advanced"] is True
    assert "COMMIT_REVOKED" in [e["kind"] for e in first["new_events"]]

    second = sentinel.tick()     # same forecast, new hour: 12:00 still holds
    assert second["changed"] is True
    assert second["revision_advanced"] is True
    assert "COMMIT_REAFFIRMED" in [e["kind"] for e in second["new_events"]]
