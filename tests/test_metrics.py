"""Metrics are a fold over the trace, and the fold must not editorialize."""
from app.fleet_demo import run_one as fleet_run
from app.metrics import fold

import contextlib
import io


def _fleet_trace(disrupt):
    with contextlib.redirect_stdout(io.StringIO()):
        return fleet_run(disrupt=disrupt)["event_trace"]


def test_fleet_disruption_metrics():
    m = fold(_fleet_trace(disrupt=True))
    assert m["commits"] == 2
    assert m["revocations"] == 1
    assert m["unrecovered_revocations"] == 0
    assert "truck limit" in m["revocation_reasons"][0]
    assert m["rejections_by_class"].get("hazard/weather", 0) >= 1
    assert len(m["reallocation_span_events"]) == 1
    assert m["reallocation_span_events"][0] > 0


def test_calm_run_has_no_revocations():
    m = fold(_fleet_trace(disrupt=False))
    assert m["commits"] == 1
    assert m["revocations"] == 0
    assert m["reallocation_span_events"] == []


def test_claim_refusal_is_counted_as_double_allocation_attempt():
    from app.core.store import InMemoryStateStore, UNFENCED
    from app.scenarios import relieffleet
    from app.fleet_demo import weather_fixture

    store = InMemoryStateStore(relieffleet.build_state())
    relieffleet.run(store, weather_fixture())
    store.claim("vehicle-HELI_1", "agency-bravo", UNFENCED)
    m = fold(store.trace())
    assert m["double_allocation_attempts_refused"] == 1
