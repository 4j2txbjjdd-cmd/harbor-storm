"""The sentinel: wall-clock re-observation over the membrane.

The long-horizon claim is that commitment does not end scrutiny -- a plan
that has committed stays subject to the world. The sentinel is the harness
that carries that claim across real time: it observes, and when the
observation changes it hands the new truth to the same disruption path the
synchronous demos use. These tests hold the sentinel to its own contract:
an unchanged forecast moves nothing, a changed forecast is applied exactly
once however many times it is observed, and every authoritative consequence
is the verifier's, never the sentinel's.
"""
import pytest

from app.config import ConfigError
from app.core.store import InMemoryStateStore
from app.demo import weather_fixture, disrupted_weather_fixture, route_fixture
from app.scenarios import stormslot, harborwindow
from app.sentinel import Sentinel, main, observation_fingerprint

AUTHORITATIVE = ("PLAN_VERIFIED", "PLAN_COMMITTED", "PLAN_REJECTED",
                 "COMMIT_REVOKED")


class SwappableWeather:
    """A provider whose forecast can change between ticks, the way the real
    world's does."""

    def __init__(self, current):
        self.current = current

    def hourly(self, location):
        return self.current.hourly(location)


def fresh_run(scenario):
    """Seed, plan and commit a scenario, then hand back a sentinel whose
    baseline is the observation the plan was computed from."""
    weather = SwappableWeather(weather_fixture())
    if scenario == "stormslot":
        store = InMemoryStateStore(stormslot.build_state())
        routes = route_fixture()
        stormslot.run(store, weather, routes)
    else:
        store = InMemoryStateStore(harborwindow.build_state())
        routes = None
        harborwindow.run(store, weather)
    baseline = observation_fingerprint(scenario, store.state.facts, weather)
    sentinel = Sentinel(store, weather, scenario, routes,
                        baseline_fingerprint=baseline)
    return store, weather, sentinel


@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_unchanged_forecast_is_a_pure_noop(scenario):
    store, _weather, sentinel = fresh_run(scenario)
    before_trace = len(store.trace())
    before_revision = store.state.revision
    before_committed = store.snapshot()["committed_plan_id"]

    for _ in range(3):
        result = sentinel.tick()
        assert result["changed"] is False
        assert result["revision_advanced"] is False
        assert result["new_events"] == []

    assert len(store.trace()) == before_trace
    assert store.state.revision == before_revision
    assert store.snapshot()["committed_plan_id"] == before_committed


@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_changed_forecast_revokes_and_replans_through_the_verifier(scenario):
    store, weather, sentinel = fresh_run(scenario)
    committed_before = store.snapshot()["committed_plan_id"]

    weather.current = disrupted_weather_fixture()
    result = sentinel.tick()

    assert result["changed"] is True
    assert result["revision_advanced"] is True
    kinds = [e["kind"] for e in result["new_events"]]
    assert "COMMIT_REVOKED" in kinds
    assert result["committed_plan_id"] is not None
    assert result["committed_plan_id"] != committed_before

    # The plan that lost its authority is the one that held it.
    revoked = [e for e in result["new_events"] if e["kind"] == "COMMIT_REVOKED"]
    assert revoked[0]["payload"]["plan_id"] == committed_before


@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_observation_applies_at_most_once_across_restarts(scenario):
    """Content-addressed event ids: a sentinel that crashes and comes back --
    modelled as a new instance with no baseline attaching to the same store --
    re-observes the same forecast and must leave no mark."""
    store, weather, sentinel = fresh_run(scenario)
    weather.current = disrupted_weather_fixture()
    sentinel.tick()

    after_trace = len(store.trace())
    after_revision = store.state.revision
    after_committed = store.snapshot()["committed_plan_id"]

    restarted = Sentinel(store, weather, scenario,
                         route_fixture() if scenario == "stormslot" else None,
                         baseline_fingerprint=None)
    result = restarted.tick()

    assert result["changed"] is True          # it had nothing to compare against
    assert result["revision_advanced"] is False  # but the world did not move twice
    # The refusal itself is on the record; nothing else is.
    assert [e["kind"] for e in result["new_events"]] == ["DUPLICATE_EVENT_IGNORED"]
    assert len(store.trace()) == after_trace + 1
    assert store.state.revision == after_revision
    assert store.snapshot()["committed_plan_id"] == after_committed


@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_sentinel_holds_no_authority(scenario):
    """Every authoritative transition in a sentinel-driven run is the
    verifier's, and the sentinel itself never appears on the trace."""
    store, weather, sentinel = fresh_run(scenario)
    weather.current = disrupted_weather_fixture()
    sentinel.tick()

    trace = store.trace()
    assert all(e["actor"] != "sentinel" for e in trace)
    for e in trace:
        if e["kind"] in AUTHORITATIVE:
            assert e["actor"] == "verifier"


def test_fingerprint_is_deterministic_and_sensitive():
    facts = harborwindow.build_state().facts
    a = observation_fingerprint("harborwindow", facts, weather_fixture())
    b = observation_fingerprint("harborwindow", facts, weather_fixture())
    c = observation_fingerprint("harborwindow", facts,
                                disrupted_weather_fixture())
    assert a == b
    assert a != c


def test_stormslot_requires_routes():
    store = InMemoryStateStore(stormslot.build_state())
    with pytest.raises(ValueError):
        Sentinel(store, weather_fixture(), "stormslot")


# --- the CLI harness --------------------------------------------------

def test_cli_staged_disruption_revokes_on_the_seeded_lane(monkeypatch, capsys):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "mock")
    main(["harborwindow", "--ticks", "3", "--interval", "0",
          "--disrupt-at-tick", "2"])
    out = capsys.readouterr().out
    assert "COMMIT_REVOKED" in out
    assert "CHANGED -> applied" in out
    # tick 3 sees the disrupted forecast again and moves nothing
    assert "unchanged" in out


def test_cli_refuses_staged_disruption_on_the_live_lane(monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "irrelevant")
    with pytest.raises(ConfigError):
        main(["harborwindow", "--ticks", "1", "--disrupt-at-tick", "1"])


def test_cli_refuses_attach_without_a_durable_backend(monkeypatch):
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "mock")
    with pytest.raises(ConfigError):
        main(["harborwindow", "--ticks", "1", "--run-id", "some-run"])
