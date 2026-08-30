"""The membrane: authoritative state moves only on independent verification."""
import pytest

from app.core.models import CandidatePlan, WorkItem
from app.core.store import UNFENCED
from app.core.store import InMemoryStateStore
from app.core.verify import verify_and_commit
from app.demo import weather_fixture, route_fixture
from app.providers.routes import MissingRouteData
from app.providers.weather import MissingWeatherData
from app.scenarios import stormslot


def _store():
    return InMemoryStateStore(stormslot.build_state())


def test_confident_but_infeasible_proposal_is_rejected():
    """An agent asserting success does not make it so.

    The plan carries metrics claiming a clean run, but proposes a departure
    that drives through the storm. The verifier ignores the metrics and
    recomputes, so the lie changes nothing.
    """
    store = _store()
    series = weather_fixture().hourly("PORT_A")
    verifier = stormslot.make_verifier(store, series, route_fixture())

    liar = CandidatePlan(
        id="liar", scenario="stormslot", created_by="overconfident-agent",
        actions=[{"type": "dispatch_truck", "hour": 16},
                 {"type": "reserve_pickup", "hour": 16}],
        metrics={"weather_conflict": 0.0, "arrival_hour": 1.0, "confidence": 0.99},
    )
    store.add_plan(liar, fence=UNFENCED)
    assert verify_and_commit(store, "liar", verifier, fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    assert store.get_plan("liar").verified is False
    assert "severe weather" in store.get_plan("liar").rejection_reason


def test_prose_success_claim_cannot_commit():
    """A plan with no dispatch action at all is rejected, not charitably read."""
    store = _store()
    verifier = stormslot.make_verifier(store, weather_fixture().hourly("PORT_A"),
                                       route_fixture())
    store.add_plan(CandidatePlan(
        id="vibes", scenario="stormslot", created_by="chatty-agent",
        actions=[{"type": "note", "text": "handled it, container is fine"}],
        metrics={},
    ), fence=UNFENCED)
    assert verify_and_commit(store, "vibes", verifier, fence=UNFENCED) is False
    assert store.state.committed_plan_id is None


def test_unverified_plan_cannot_be_committed_directly():
    """Bypassing the verifier and calling commit_plan must fail."""
    store = _store()
    store.add_plan(CandidatePlan(id="sneaky", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    assert store.commit_plan("sneaky", "agent", fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    assert any(e.kind == "COMMIT_REFUSED" for e in store.events)


def test_claim_is_atomic_under_contention():
    """Exactly one of many racing actors may hold a work item."""
    from concurrent.futures import ThreadPoolExecutor
    store = _store()
    store.create_work(WorkItem("route", "find feasible truck departure"), fence=UNFENCED)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda i: store.claim("route", f"agent-{i}", fence=UNFENCED), range(16)))
    assert sum(results) == 1
    assert store.state.work["route"].status == "CLAIMED"
    assert sum(1 for e in store.events if e.kind == "CLAIM_REFUSED") == 15


def test_holder_may_renew_its_own_claim():
    store = _store()
    store.create_work(WorkItem("route", "x"), fence=UNFENCED)
    assert store.claim("route", "transport-agent", fence=UNFENCED) is True
    assert store.claim("route", "transport-agent", fence=UNFENCED) is True
    assert store.claim("route", "other-agent", fence=UNFENCED) is False


def test_revoke_only_applies_to_the_committed_plan():
    store = _store()
    stormslot.run(store, weather_fixture(), route_fixture())
    committed = store.state.committed_plan_id
    assert store.revoke_commit("stormslot-plan-1", "verifier", "wrong plan", fence=UNFENCED) is False
    assert store.state.committed_plan_id == committed
    assert store.revoke_commit(committed, "verifier", "forecast changed", fence=UNFENCED) is True
    assert store.state.committed_plan_id is None


def test_missing_provider_data_raises_instead_of_defaulting():
    """Absence is an error. A silent default would approve an uncosted plan."""
    with pytest.raises(MissingRouteData):
        route_fixture().estimate("PORT_A", "WH_A", 21)
    with pytest.raises(MissingWeatherData):
        weather_fixture().hourly("NOWHERE")
