"""Seismic evidence: a second observation stream through the same membrane.

The provider maps quakes to edge alerts under a declared policy; the
verifier decides what the alerts do to a committed fleet assignment. The
seeded lane never touches the network; the USGS adapter is a live upgrade
exercised only when explicitly asked for.
"""
import pytest

from app.core.store import InMemoryStateStore
from app.fleet_demo import weather_fixture
from app.providers.seismic import (MockSeismicProvider, SeismicEvent,
                                   edge_alerts)
from app.scenarios import relieffleet


def _quake(mag, event_id="us7000test"):
    return SeismicEvent(event_id=event_id, magnitude=mag, latitude=28.1,
                        longitude=85.25, depth_km=10.0,
                        time_iso="2026-08-30T00:00:00Z")


def _facts():
    f = relieffleet.build_state().facts
    f["quake_alert_magnitude"] = 4.5
    f["quake_sensitive_edges"] = ["BR1", "RD3"]
    return f


def test_below_threshold_seismicity_marks_nothing():
    alerts = edge_alerts(MockSeismicProvider([_quake(3.9)])
                         .recent(28.03, 85.2, 150, 3.0), _facts())
    assert alerts == {}


def test_threshold_quake_marks_the_sensitive_edges_with_provenance():
    alerts = edge_alerts(MockSeismicProvider([_quake(5.2)])
                         .recent(28.03, 85.2, 150, 3.0), _facts())
    assert set(alerts) == {"BR1", "RD3"}
    assert "M5.2" in alerts["BR1"]
    assert "us7000test" in alerts["BR1"]


def test_quake_alert_revokes_the_committed_assignment_through_the_verifier():
    """End to end: seismicity -> edge failures -> revision advance ->
    committed fleet plan revoked -> reallocation. The quake never touches
    the store directly; it becomes edge failures under policy, and the
    verifier does the rest."""
    store = InMemoryStateStore(relieffleet.build_state())
    store.state.facts["quake_alert_magnitude"] = 4.5
    # RD4 carries a committed truck mission (M3); its failure must matter.
    store.state.facts["quake_sensitive_edges"] = ["RD4"]
    relieffleet.run(store, weather_fixture())
    assert store.snapshot()["committed_plan_id"] == "fleet-r0-p2"

    events = MockSeismicProvider([_quake(5.6)]).recent(28.03, 85.2, 150, 3.0)
    alerts = edge_alerts(events, store.state.facts)
    snap = relieffleet.disrupt(store, weather_fixture(),
                               failed_edges=sorted(alerts),
                               event_id=f"seismic-{events[0].event_id}")

    kinds = [e["kind"] for e in store.trace()]
    assert "EDGE_FAILED" in kinds
    assert "COMMIT_REVOKED" in kinds
    revoked = [e for e in store.trace() if e["kind"] == "COMMIT_REVOKED"][0]
    assert "failed edge RD4" in revoked["payload"]["reason"]
    # M3's village lost its road; the reallocation puts it on the
    # helicopter, and the same USGS event id can never apply twice.
    plan = snap["plans"][snap["committed_plan_id"]]
    m3 = [a for a in plan["actions"]
          if a.get("type") == "assign" and a["mission"] == "M3"][0]
    assert m3["vehicle"] == "HELI_1"

    again = relieffleet.disrupt(store, weather_fixture(),
                                failed_edges=sorted(alerts),
                                event_id=f"seismic-{events[0].event_id}")
    assert again["committed_plan_id"] == snap["committed_plan_id"]
    assert store.trace()[-1]["kind"] == "DUPLICATE_EVENT_IGNORED"


def test_live_usgs_adapter_is_not_constructible_into_the_seeded_lane(monkeypatch):
    """Mirror of the live-gate discipline: make the live adapter explode and
    run the seeded path through everything above."""
    from app.providers import seismic as s
    monkeypatch.setattr(
        s.USGSSeismicProvider, "__init__",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError(
            "live seismic provider constructed on the seeded lane")))
    test_quake_alert_revokes_the_committed_assignment_through_the_verifier()


def test_usgs_query_honors_the_advertised_lookback():
    """Sol's audit finding, kept as a contract: without an explicit
    starttime USGS applies its own 30-day default, silently widening the
    window this adapter claims. No network involved."""
    from datetime import datetime, timezone
    from app.providers.seismic import USGSSeismicProvider

    p = USGSSeismicProvider.__new__(USGSSeismicProvider)
    p.lookback_hours = 24
    p.timeout = 10.0
    params = p.query_params(28.03, 85.2, 150.0, 4.0)
    assert "starttime" in params
    start = datetime.strptime(params["starttime"], "%Y-%m-%dT%H:%M:%S")
    age_h = (datetime.now(timezone.utc).replace(tzinfo=None) - start).total_seconds() / 3600
    assert 23.9 < age_h < 24.1
