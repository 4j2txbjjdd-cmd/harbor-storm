"""Live-gate structure, exercised offline.

Read the distinction before adding here. These tests prove the gate's *lane
discipline* and that its checks have teeth. None of them proves live weather,
and none of them can: they run against controlled providers precisely so they
never touch the network.

The live proof is:

    WEATHER_PROVIDER=google GOOGLE_WEATHER_API_KEY=... \
      .venv/bin/python -m app.live_gate --provider live

A green suite here plus a blocked live gate means the plumbing is right and
nothing has been proven about Google Weather.
"""
import pytest

from app import live_gate
from app.config import Settings
from app.live_gate import LiveLaneError, evaluate, main
from app.providers.weather import (MockWeatherProvider, WeatherPoint,
                                   WeatherProviderError)

LIVE = live_gate.LIVE_PROVIDER_NAME


def _settings(provider="google") -> Settings:
    return Settings(state_backend="memory", weather_provider=provider,
                    gcp_project=None, firestore_database=None,
                    pubsub_topic=None, forecast_hours=24)


class FakeLive(MockWeatherProvider):
    """Wears the live adapter's name and provenance surface, over seeded data.

    It exists to drive the gate's structural checks without a network. It is
    NOT evidence of anything live -- that is the whole reason the gate reads
    provenance rather than trusting a name, and why every test here that could
    be mistaken for a live proof says so.
    """

    name = LIVE

    def __init__(self, series, fetches=None):
        super().__init__(series)
        self._fetches = fetches
        self.calls = 0

    def hourly(self, location):
        self.calls += 1
        return super().hourly(location)

    def provenance(self):
        if self._fetches is not None:
            return list(self._fetches)
        return [{"site": s, "latitude": 0.0, "longitude": 0.0,
                 "endpoint": "https://weather.googleapis.com/v1/forecast/hours:lookup",
                 "requested_at": "2026-08-27T16:00:00+00:00", "http_status": 200,
                 "hours_requested": 24, "points_returned": len(v),
                 "time_zone": "Europe/Athens", "first_interval_start": None,
                 "last_interval_start": None}
                for s, v in self.series.items()]


def _calm(hours=range(0, 24)):
    return [WeatherPoint(h, wind_kph=8.0, rain_mm=0.0) for h in hours]


def _install(monkeypatch, weather):
    monkeypatch.setattr(live_gate, "make_weather", lambda settings, *a, **k: weather)


# --- lane discipline --------------------------------------------------

def test_asserting_live_against_a_mock_configuration_is_refused():
    """Two independent statements must agree: the flag and WEATHER_PROVIDER."""
    with pytest.raises(LiveLaneError, match="resolves to 'mock'"):
        evaluate(["stormslot"], _settings("mock"), asserted_provider="live")


def test_an_unknown_lane_is_refused():
    with pytest.raises(LiveLaneError, match="unknown provider lane"):
        evaluate(["stormslot"], _settings("google"), asserted_provider="seeded")


def test_a_seeded_provider_reaching_the_gate_is_refused(monkeypatch):
    """Configuration said live and handed back a fixture. Report nothing."""
    _install(monkeypatch, MockWeatherProvider({"PORT_A": _calm()}))
    with pytest.raises(LiveLaneError, match="Refusing to report a seeded run as live"):
        evaluate(["stormslot"], _settings("google"), asserted_provider="live")


# --- no fallback ------------------------------------------------------

def test_a_provider_failure_is_not_absorbed(monkeypatch):
    class Broken(FakeLive):
        def hourly(self, location):
            raise WeatherProviderError("Google Weather API returned 400")

    _install(monkeypatch, Broken({"PORT_A": _calm()}))
    with pytest.raises(WeatherProviderError, match="400"):
        evaluate(["stormslot"], _settings("google"), asserted_provider="live")


def test_a_blocked_gate_prints_nothing_that_reads_as_a_pass(monkeypatch, capsys):
    class Broken(FakeLive):
        def hourly(self, location):
            raise WeatherProviderError("Google Weather API unreachable")

    _install(monkeypatch, Broken({"PORT_A": _calm()}))
    monkeypatch.setattr(live_gate.Settings, "from_env",
                        classmethod(lambda cls: _settings("google")))
    code = main(["--provider", "live", "--scenario", "stormslot"])
    out = capsys.readouterr()
    assert code == 2
    assert out.out == ""
    assert "LIVE PASS" not in out.out and "LIVE PASS" not in out.err
    assert "No seeded result was substituted" in out.err


# --- the checks have teeth --------------------------------------------

def test_the_consumption_check_catches_a_provider_that_changed_its_answer(monkeypatch):
    """The gate recomputes severe hours and compares them with the trace.

    A provider that answers differently the second time would have the scenario
    decide on one world and the gate certify another. `GoogleWeatherProvider`
    caches per site precisely so this cannot happen; this test is what makes
    that cache load-bearing rather than incidental.
    """
    class Drifting(FakeLive):
        def hourly(self, location):
            self.calls += 1
            if self.calls <= 1:
                return _calm()
            return [WeatherPoint(h, wind_kph=8.0, rain_mm=99.0) for h in range(0, 24)]

    _install(monkeypatch, Drifting({"PORT_A": _calm()}))
    report = evaluate(["stormslot"], _settings("google"), asserted_provider="live")
    consumed = [c for c in report["scenarios"][0]["checks"]
                if c["name"] == "scenario consumed the live observations"][0]
    assert consumed["passed"] is False
    assert report["passed"] is False


def test_a_severe_forecast_still_passes_by_moving_the_departure(monkeypatch):
    """Not a vacuous pass: the severe set is non-empty and the committed hour
    avoids it, which is the consistency the check actually asserts."""
    series = [WeatherPoint(h, wind_kph=8.0, rain_mm=99.0 if h in (15, 16) else 0.0)
              for h in range(0, 24)]
    _install(monkeypatch, FakeLive({"PORT_A": series}))
    report = evaluate(["stormslot"], _settings("google"), asserted_provider="live")
    r = report["scenarios"][0]
    assert r["severe_hours_live"] == [15, 16]
    assert r["committed_plan_id"] is not None
    assert r["passed"] is True


def test_an_uncoverable_forecast_reports_a_rejection_not_a_pass_by_silence(monkeypatch):
    """HarborWindow needs both ends of the crossing. A forecast that covers
    neither must produce recorded rejections, not an empty-handed success."""
    _install(monkeypatch, FakeLive({"HARBOR_A": _calm(range(0, 6)),
                                    "ISLAND_B": _calm(range(0, 6))}))
    report = evaluate(["harborwindow"], _settings("google"), asserted_provider="live")
    r = report["scenarios"][0]
    assert r["committed_plan_id"] is None
    assert r["rejections"], "a refusal must leave a reason on the record"
    assert r["passed"] is True


def test_both_scenarios_keep_their_own_weather_rule():
    """StormSlot reads rain at one site; HarborWindow reads wind and rain at
    two. Collapsing them would make the gate agree with itself, not with the
    scenarios."""
    storm = live_gate.LENSES["stormslot"]
    harbor = live_gate.LENSES["harborwindow"]
    facts_s = {"port": "PORT_A", "warehouse": "WH_A"}
    facts_h = {"harbor": "HARBOR_A", "island": "ISLAND_B"}
    assert storm.sites(facts_s) == ["PORT_A"]
    assert harbor.sites(facts_h) == ["HARBOR_A", "ISLAND_B"]
    assert storm.measured_event != harbor.measured_event
    assert storm.weather_rule != harbor.weather_rule


# --- determinism must survive -----------------------------------------

def test_the_deterministic_lane_never_builds_a_live_provider(monkeypatch):
    """The seeded gate and demos must not be able to reach the network.

    Asserted by making the live adapter unconstructable and then running the
    whole deterministic lane. Grepping for the import proves nothing about what
    executes; this fails if any deterministic path ever acquires a live
    provider, including indirectly through config.
    """
    import app.demo as demo
    import app.gate as gate
    from app.providers import weather as weather_mod

    def refuse(*a, **k):
        raise AssertionError(
            "the deterministic lane constructed a live weather provider")

    monkeypatch.setattr(weather_mod.GoogleWeatherProvider, "__init__", refuse)

    report = gate.evaluate_all()
    for scenario in ("stormslot", "harborwindow"):
        assert report[scenario]["survives"] is True
        demo.run_one(scenario, disrupt=True)


def test_the_seeded_lane_stays_byte_identical_across_replays():
    """Live weather changes; the seeded lane may not. Same seed, same trace."""
    import app.demo as demo

    def trace_of():
        store = demo.new_store("harborwindow")
        demo.run_scenario("harborwindow", store, demo.weather_fixture())
        demo.disrupt_scenario("harborwindow", store, demo.disrupted_weather_fixture())
        return [(e["kind"], e["actor"], e["payload"]) for e in store.trace()]

    assert trace_of() == trace_of()
