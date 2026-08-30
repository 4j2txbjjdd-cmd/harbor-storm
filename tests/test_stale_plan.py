"""Stale-plan protection, end to end.

A candidate is computed against a world. If that world moves before the
candidate commits, the candidate is describing something that no longer
exists. Recomputing it is not enough on its own -- under asynchronous
execution an actor can read facts, sleep, and return after the world has
changed, and a verifier run at that moment would pass a plan built for the
old world. These tests hold the line at the membrane, at the scenarios, and
at the agent tool surface.
"""
import pytest

from app.core.models import CandidatePlan
from app.core.store import UNFENCED
from app.core.store import InMemoryStateStore
from app.core.verify import verify_and_commit, reverify_committed
from app.agents.tools import ActorToolkit
from app.demo import weather_fixture, disrupted_weather_fixture, route_fixture
from app.scenarios import stormslot, harborwindow


def kinds(store):
    return [e.kind for e in store.events]


def payloads(store, kind):
    return [e.payload for e in store.events if e.kind == kind]


def _always_ok(_plan):
    return True, ""


# --- the membrane ----------------------------------------------------

def test_verify_and_commit_refuses_a_stale_plan_even_when_the_verifier_passes():
    """The verifier is not the only gate. A passing verdict on a stale plan
    must still fail, or asynchronous execution reopens the hole."""
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert verify_and_commit(store, "p1", _always_ok, fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    reason = payloads(store, "PLAN_REJECTED")[0]["reason"]
    assert "stale" in reason and "revision 0" in reason


def test_verify_and_commit_still_works_when_nothing_moved():
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    assert verify_and_commit(store, "p1", _always_ok, fence=UNFENCED) is True
    assert store.state.committed_plan_id == "p1"


def test_reverify_rebinds_a_plan_that_survives_new_facts():
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "forecast updated")

    assert reverify_committed(store, _always_ok, fence=UNFENCED) is True
    assert store.get_plan("p1").basis_revision == 1
    assert payloads(store, "PLAN_REBOUND")[0]["from_revision"] == 0
    # Re-binding is explicit and logged, never a silent exemption.
    assert kinds(store).index("PLAN_REBOUND") < kinds(store).index("COMMIT_REAFFIRMED")


# --- the asynchronous case -------------------------------------------

def test_an_actor_that_read_facts_before_the_disruption_is_refused():
    """The failure this whole mechanism exists for.

    The actor reads the world at revision 0, the world moves to revision 1,
    and only then does the actor propose. Binding at propose time would stamp
    stale reasoning as current; binding at read time refuses it.
    """
    store = InMemoryStateStore(stormslot.build_state())
    tools = ActorToolkit(store, "transport-agent", ["route"],
                         ["port", "warehouse", "truck_available_hour"], "async", fence=UNFENCED)

    tools.read_facts()                      # observed revision 0
    store.advance_revision("weather-agent", "storm arrived")
    result = tools.propose_plan(actions=[{"type": "dispatch_truck", "hour": 15}])

    plan = store.get_plan(result["plan_id"])
    assert plan.basis_revision == 0, "proposal must bind to what the actor read"
    assert verify_and_commit(store, plan.id, _always_ok, fence=UNFENCED) is False
    assert store.state.committed_plan_id is None


def test_an_actor_that_re_reads_after_the_disruption_is_accepted():
    """The same actor, having actually looked again, may proceed."""
    store = InMemoryStateStore(stormslot.build_state())
    tools = ActorToolkit(store, "transport-agent", ["route"],
                         ["port", "warehouse", "truck_available_hour"], "async", fence=UNFENCED)

    tools.read_facts()
    store.advance_revision("weather-agent", "storm arrived")
    tools.read_facts()                      # looked again: now at revision 1
    result = tools.propose_plan(actions=[{"type": "dispatch_truck", "hour": 18}])

    assert store.get_plan(result["plan_id"]).basis_revision == 1
    assert verify_and_commit(store, result["plan_id"], _always_ok, fence=UNFENCED) is True


# --- both scenarios --------------------------------------------------

def test_stormslot_disruption_advances_the_revision_and_rebinds():
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    committed = store.state.committed_plan_id
    assert store.state.revision == 0
    assert store.get_plan(committed).basis_revision == 0

    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture())
    assert store.state.revision == 1
    assert "STATE_REVISION_ADVANCED" in kinds(store)

    final = store.state.committed_plan_id
    if final is not None:
        assert store.get_plan(final).basis_revision == 1, \
            "whatever holds after a disruption must be bound to the new world"


def test_harborwindow_disruption_advances_the_revision_and_rebinds():
    store = InMemoryStateStore(harborwindow.build_state())
    harborwindow.run(store, weather_fixture())
    assert store.state.revision == 0

    harborwindow.disrupt(store, disrupted_weather_fixture())
    assert store.state.revision == 1

    final = store.state.committed_plan_id
    if final is not None:
        assert store.get_plan(final).basis_revision == 1


def test_the_revision_advances_before_anything_is_re_verified():
    """Ordering is the whole guarantee: advancing after re-verification would
    let the old world sign off on the new one."""
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    before = len(store.events)

    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture())
    after = kinds(store)[before:]

    advanced = after.index("STATE_REVISION_ADVANCED")
    for later in ("PLAN_REBOUND", "COMMIT_REAFFIRMED", "PLAN_VERIFIED",
                  "COMMIT_REVOKED", "VERIFY_REFUSED_STALE"):
        if later in after:
            assert advanced < after.index(later), \
                f"{later} must come after the revision advances"


# --- interleaving: truth arriving during verification ----------------

def test_reverify_refuses_to_rebind_when_truth_lands_during_verification():
    """The TOCTOU window between verdict and re-bind.

    Verification is not instantaneous. If a disruption lands while the verifier
    is running, the verdict already describes a world that is gone. Re-binding
    on that verdict would stamp the plan onto a revision no verifier ever
    checked it against -- the precise gap the binding exists to close.
    """
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "first storm")

    def verifier_interrupted_by_new_truth(_plan):
        # A second disruption lands while this verdict is being reached.
        store.advance_revision("weather-agent", "second storm mid-verification")
        return True, ""

    assert reverify_committed(store, verifier_interrupted_by_new_truth, fence=UNFENCED) is False
    assert store.get_plan("p1").basis_revision == 0, \
        "plan must not be stamped onto a revision nothing verified it against"
    assert "REBIND_REFUSED_STALE" in kinds(store)
    # The commitment is withdrawn, not left standing on a dead verdict.
    assert store.state.committed_plan_id is None
    assert "COMMIT_REVOKED" in kinds(store)


def test_reverify_rebind_is_atomic_under_real_threads():
    """The same race, driven by an actual concurrent disruption.

    The verifier blocks until another thread has advanced the revision, so the
    re-bind is guaranteed to be attempted against a moved world.
    """
    import threading

    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "first storm")

    verifier_entered = threading.Event()
    disruption_landed = threading.Event()

    def slow_verifier(_plan):
        verifier_entered.set()
        assert disruption_landed.wait(timeout=5), "disruption thread never ran"
        return True, ""

    def land_a_disruption():
        assert verifier_entered.wait(timeout=5), "verifier never started"
        store.advance_revision("weather-agent", "concurrent storm")
        disruption_landed.set()

    other = threading.Thread(target=land_a_disruption)
    other.start()
    result = reverify_committed(store, slow_verifier, fence=UNFENCED)
    other.join(timeout=5)

    assert result is False
    assert store.state.revision == 2
    assert store.get_plan("p1").basis_revision == 0
    assert "REBIND_REFUSED_STALE" in kinds(store)


# --- fail closed on an unbound proposal ------------------------------

def test_proposing_without_reading_facts_is_refused():
    """No observed world means no basis to bind to.

    Substituting the current revision would manufacture a binding the actor
    never earned, which is indistinguishable from the stale case at commit time.
    """
    from app.agents.tools import UnboundProposalError

    store = InMemoryStateStore(stormslot.build_state())
    tools = ActorToolkit(store, "transport-agent", ["route"],
                         ["port", "warehouse", "truck_available_hour"], "unbound", fence=UNFENCED)

    with pytest.raises(UnboundProposalError, match="without calling read_facts"):
        tools.propose_plan(actions=[{"type": "dispatch_truck", "hour": 15}])

    assert store.state.plans == {}, "a refused proposal must not be recorded"


def test_reading_facts_first_makes_the_same_proposal_valid():
    store = InMemoryStateStore(stormslot.build_state())
    tools = ActorToolkit(store, "transport-agent", ["route"],
                         ["port", "warehouse", "truck_available_hour"], "bound", fence=UNFENCED)
    tools.read_facts()
    result = tools.propose_plan(actions=[{"type": "dispatch_truck", "hour": 15}])
    assert store.get_plan(result["plan_id"]).basis_revision == 0


def test_a_lost_rebind_race_does_not_revoke_the_winners_repair():
    """The loser of a re-bind race must not undo the winner.

    A verifies against R1. While A is reaching that verdict, B advances the
    world to R2, re-verifies the same commitment, and repairs it. A's re-bind
    then fails -- correctly, its verdict is stale. But the commitment no longer
    needs withdrawing: B has already validated it against the world as it now
    stands. Revoking unconditionally would destroy valid newer work.
    """
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "first storm")      # -> R1

    def worker_a_verifier(_plan):
        # Worker B does its whole cycle inside A's verification window.
        store.advance_revision("weather-agent", "second storm")  # -> R2
        assert store.rebind_plan("p1", "worker-b", expected_revision=2, fence=UNFENCED) == 2
        return True, ""

    assert reverify_committed(store, worker_a_verifier, fence=UNFENCED) is False

    assert store.state.committed_plan_id == "p1", \
        "the loser of the race revoked the winner's valid commitment"
    assert store.get_plan("p1").basis_revision == 2
    assert store.get_plan("p1").verified is True
    assert "REVOKE_SKIPPED_REPAIRED" in kinds(store)
    assert "COMMIT_REVOKED" not in kinds(store)


def test_an_unrepaired_stale_commitment_is_still_revoked():
    """The guard must not become a blanket exemption.

    Same lost race, but nobody repaired the commitment. It is genuinely stale
    and must still be withdrawn.
    """
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "first storm")

    def verifier_interrupted(_plan):
        store.advance_revision("weather-agent", "second storm")   # no repair
        return True, ""

    assert reverify_committed(store, verifier_interrupted, fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    assert "COMMIT_REVOKED" in kinds(store)
    assert "REVOKE_SKIPPED_REPAIRED" not in kinds(store)


# --- the negative arm of the same invariant --------------------------

def test_a_stale_negative_verdict_does_not_revoke_a_repaired_commitment():
    """Mirror of the positive arm. A stale FAIL must not undo a fresh PASS.

    A starts failing verification at R1. Inside A's verification window, B
    advances the world to R2, re-verifies the same commitment and repairs it.
    When A resumes, its verdict describes a world that is gone -- it must not
    revoke B's R2 commitment.
    """
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "first storm")      # -> R1

    def worker_a_failing_verifier(_plan):
        store.advance_revision("weather-agent", "second storm")  # B -> R2
        assert store.rebind_plan("p1", "worker-b", expected_revision=2, fence=UNFENCED) == 2
        return False, "A judged this unsafe under R1"

    assert reverify_committed(store, worker_a_failing_verifier, fence=UNFENCED) is False

    assert store.state.committed_plan_id == "p1", \
        "a stale negative verdict revoked a freshly repaired commitment"
    assert store.get_plan("p1").verified is True
    assert store.get_plan("p1").basis_revision == 2
    assert "REVOKE_SKIPPED_OBSOLETE_VERDICT" in kinds(store)
    assert "COMMIT_REVOKED" not in kinds(store)


def test_a_current_negative_verdict_still_revokes():
    """The ordinary case must be untouched: nothing moved, the plan is refuted."""
    store = InMemoryStateStore(stormslot.build_state())
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot", created_by="agent",
                                 actions=[], metrics={}, basis_revision=0), fence=UNFENCED)
    verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert reverify_committed(store, lambda _p: (False, "no longer safe"), fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    assert store.get_plan("p1").rejection_reason == "no longer safe"
    assert "COMMIT_REVOKED" in kinds(store)
    assert "REVOKE_SKIPPED_OBSOLETE_VERDICT" not in kinds(store)


def test_both_arms_are_gated_on_the_same_observed_revision():
    """The invariant stated once: a verdict may move commitment state only
    while the revision it evaluated is still current -- pass or fail."""
    for verdict, expected_event in ((True, "REVOKE_SKIPPED_REPAIRED"),
                                    (False, "REVOKE_SKIPPED_OBSOLETE_VERDICT")):
        store = InMemoryStateStore(stormslot.build_state())
        store.add_plan(CandidatePlan(id="p1", scenario="stormslot",
                                     created_by="agent", actions=[], metrics={},
                                     basis_revision=0), fence=UNFENCED)
        verify_and_commit(store, "p1", _always_ok, fence=UNFENCED)
        store.advance_revision("weather-agent", "first")

        def racing_verifier(_plan, _v=verdict):
            store.advance_revision("weather-agent", "second")
            store.rebind_plan("p1", "worker-b", expected_revision=2, fence=UNFENCED)
            return _v, "" if _v else "stale failure"

        assert reverify_committed(store, racing_verifier, fence=UNFENCED) is False
        assert store.state.committed_plan_id == "p1", \
            f"verdict={verdict} moved commitment state on an obsolete revision"
        assert expected_event in kinds(store)
