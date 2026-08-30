"""Scenario-level behaviour: weather must change the operational decision."""
from app.core.store import InMemoryStateStore
from app.demo import (weather_fixture, disrupted_weather_fixture, route_fixture,
                      new_store, run_one)
from app.scenarios import stormslot, harborwindow


def kinds(store):
    return [e.kind for e in store.events]


def test_stormslot_rejects_booked_slot_and_replans_around_storm():
    store = InMemoryStateStore(stormslot.build_state())
    snap = stormslot.run(store, weather_fixture(), route_fixture())

    # The booked 15:00 pickup is proposed first and must be refused: the truck
    # would still be on the road during the 16:00 storm hour.
    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert rejected, "booked slot should have been rejected"
    assert rejected[0].payload["plan_id"] == "stormslot-plan-1"
    assert "severe weather" in rejected[0].payload["reason"]

    committed = snap["committed_plan_id"]
    assert committed and committed != "stormslot-plan-1"
    plan = snap["plans"][committed]
    assert plan["verified"] is True
    assert plan["metrics"]["departure_hour"] == 14.0
    assert kinds(store).index("PLAN_VERIFIED") < kinds(store).index("PLAN_COMMITTED")


def test_harborwindow_rejects_booked_sailing_and_replans():
    store = InMemoryStateStore(harborwindow.build_state())
    snap = harborwindow.run(store, weather_fixture())

    rejected = [e for e in store.events if e.kind == "PLAN_REJECTED"]
    assert rejected[0].payload["plan_id"] == "harbor-plan-1"
    assert "wind" in rejected[0].payload["reason"]

    committed = snap["committed_plan_id"]
    plan = snap["plans"][committed]
    assert plan["verified"] is True
    assert plan["metrics"]["departure_hour"] == 14.0


def test_weather_is_load_bearing_not_decorative():
    """With calm weather the booked slot must survive untouched.

    This is the control: if the scenario committed the same plan either way,
    the weather input would be theatre.
    """
    from app.providers.weather import MockWeatherProvider, WeatherPoint
    calm = MockWeatherProvider({
        "PORT_A": [WeatherPoint(h, wind_kph=10, rain_mm=1) for h in range(13, 19)],
    })
    store = InMemoryStateStore(stormslot.build_state())
    snap = stormslot.run(store, calm, route_fixture())
    assert snap["committed_plan_id"] == "stormslot-plan-1"
    assert snap["plans"]["stormslot-plan-1"]["metrics"]["departure_hour"] == 15.0
    assert "PLAN_REJECTED" not in kinds(store)


def test_disruption_revokes_commitment_and_recovers():
    store = new_store("stormslot")
    stormslot.run(store, weather_fixture(), route_fixture())
    first = store.state.committed_plan_id
    snap = stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture())

    assert "COMMIT_REVOKED" in kinds(store)
    assert snap["committed_plan_id"] not in (None, first)
    assert snap["plans"][snap["committed_plan_id"]]["metrics"]["departure_hour"] == 17.0


def test_harborwindow_disruption_recovers():
    store = new_store("harborwindow")
    harborwindow.run(store, weather_fixture())
    snap = harborwindow.disrupt(store, disrupted_weather_fixture())
    assert "COMMIT_REVOKED" in kinds(store)
    assert snap["plans"][snap["committed_plan_id"]]["metrics"]["departure_hour"] == 16.0


def test_runs_are_deterministic_from_the_seed():
    """Hard gate: the demo must replay identically even if a live API is down."""
    for scenario in ("stormslot", "harborwindow"):
        a = run_one(scenario)
        b = run_one(scenario)
        assert a["committed_plan"] == b["committed_plan"]
        assert a["plan"] == b["plan"]
        assert ([e["kind"] for e in a["event_trace"]]
                == [e["kind"] for e in b["event_trace"]])
