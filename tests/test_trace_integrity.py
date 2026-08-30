"""The trace is evidence, so its displayed order is checked, not trusted."""
from app.core.verify import check_trace_integrity
from app.demo import route_fixture, weather_fixture, disrupted_weather_fixture
from app.core.store import InMemoryStateStore
from app.scenarios import stormslot


def test_real_runs_are_self_consistent():
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture())
    assert check_trace_integrity(store.trace()) == []


def test_commit_displayed_before_verify_is_caught():
    """The exact inversion cross-instance clock skew could produce."""
    trace = [
        {"seq": 1, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p1"}},
        {"seq": 2, "kind": "PLAN_COMMITTED", "payload": {"plan_id": "p1"}},
        {"seq": 3, "kind": "PLAN_VERIFIED", "payload": {"plan_id": "p1"}},
    ]
    problems = check_trace_integrity(trace)
    assert len(problems) == 1
    assert "PLAN_COMMITTED at 2 displayed before PLAN_VERIFIED at 3" in problems[0]


def test_commit_with_no_verification_at_all_is_caught():
    """Distinguished from an inversion: the verification never happened."""
    trace = [
        {"seq": 1, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p1"}},
        {"seq": 2, "kind": "PLAN_COMMITTED", "payload": {"plan_id": "p1"}},
    ]
    problems = check_trace_integrity(trace)
    assert any("no preceding PLAN_VERIFIED" in p for p in problems)


def test_verify_displayed_before_propose_is_caught():
    trace = [
        {"seq": 1, "kind": "PLAN_VERIFIED", "payload": {"plan_id": "p1"}},
        {"seq": 2, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p1"}},
        {"seq": 3, "kind": "PLAN_COMMITTED", "payload": {"plan_id": "p1"}},
    ]
    assert any("before PLAN_PROPOSED" in p for p in check_trace_integrity(trace))


def test_revoke_without_verification_is_caught():
    trace = [
        {"seq": 1, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p1"}},
        {"seq": 2, "kind": "COMMIT_REVOKED", "payload": {"plan_id": "p1"}},
    ]
    assert check_trace_integrity(trace)


def test_rejected_plans_do_not_trip_the_check():
    trace = [
        {"seq": 1, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p1"}},
        {"seq": 2, "kind": "PLAN_REJECTED", "payload": {"plan_id": "p1",
                                                        "reason": "storm"}},
        {"seq": 3, "kind": "PLAN_PROPOSED", "payload": {"plan_id": "p2"}},
        {"seq": 4, "kind": "PLAN_VERIFIED", "payload": {"plan_id": "p2"}},
        {"seq": 5, "kind": "PLAN_COMMITTED", "payload": {"plan_id": "p2"}},
    ]
    assert check_trace_integrity(trace) == []
