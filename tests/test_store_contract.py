"""One contract, both backends.

Every semantic the scenarios depend on is asserted here against
InMemoryStateStore and, when a Firestore emulator is reachable, against
FirestoreStateStore. The point is that swapping the backend cannot quietly
change what "atomic claim" or "commit only if verified" means.

To include the Firestore backend:

    firebase emulators:start --only firestore     # or gcloud emulators firestore
    export FIRESTORE_EMULATOR_HOST=localhost:8080
    export GOOGLE_CLOUD_PROJECT=harbor-storm-test
    .venv/bin/python -m pytest -q tests/test_store_contract.py
"""
from __future__ import annotations
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.models import CandidatePlan, WorkItem
from app.core.store import ClaimContentionError
from app.core.store import UNFENCED
from app.core.store import (EventAlreadyAppliedError, InMemoryStateStore,
                            SupersededWorkerError)
from app.scenarios import stormslot

EMULATOR = os.environ.get("FIRESTORE_EMULATOR_HOST")


def _memory():
    return InMemoryStateStore(stormslot.build_state())


def _firestore():
    from app.core.firestore_store import FirestoreStateStore
    return FirestoreStateStore(f"test-{uuid.uuid4().hex[:12]}",
                               state=stormslot.build_state())


BACKENDS = [pytest.param(_memory, id="memory")]
if EMULATOR:
    BACKENDS.append(pytest.param(_firestore, id="firestore"))
else:
    BACKENDS.append(pytest.param(
        _firestore, id="firestore",
        marks=pytest.mark.skip(reason="set FIRESTORE_EMULATOR_HOST to run")))


@pytest.fixture(params=BACKENDS)
def store(request):
    return request.param()


@pytest.fixture
def reopen():
    """Reopen the same underlying run through a second store instance.

    For Firestore that is a genuine rehydration from the database. For the
    in-memory backend it is a second store over the same state object, which is
    the closest equivalent that substrate has -- it shares persisted state and
    nothing else.
    """
    def _reopen(store):
        if type(store).__name__ == "InMemoryStateStore":
            fresh = InMemoryStateStore(store.state)
            # Carry the event log across. Dropping it would make any trace
            # assertion on a reopened store pass vacuously, and the trace is
            # the record this substrate is judged on.
            fresh.events = list(store.events)
            return fresh
        from app.core.firestore_store import FirestoreStateStore
        return FirestoreStateStore(store.run_id)
    return _reopen


def kinds(store):
    return [e["kind"] for e in store.trace()]


def test_event_sequence_is_dense_and_ordered(store):
    for i in range(5):
        store.emit("PING", "tester", {"i": i})
    seqs = [e["seq"] for e in store.trace()]
    assert seqs == list(range(1, len(seqs) + 1))


def test_create_work_is_idempotent(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    assert sum(1 for k in kinds(store) if k == "WORK_CREATED") == 1


def test_claim_is_exclusive(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    assert store.claim("route", "transport-agent", fence=UNFENCED) is True
    assert store.claim("route", "rival-agent", fence=UNFENCED) is False
    assert store.state.work["route"].claimed_by == "transport-agent"
    assert "CLAIM_REFUSED" in kinds(store)


def test_claim_is_atomic_under_concurrency(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    with ThreadPoolExecutor(max_workers=8) as pool:
        won = list(pool.map(lambda i: store.claim("route", f"agent-{i}", fence=UNFENCED), range(8)))
    assert sum(won) == 1


def test_contention_records_every_refusal(store):
    """Contention must not be relieved by losing evidence.

    Sixteen agents race for one item. Exactly one wins, and the other fifteen
    refusals are all in the trace -- a refusal record is what proves the
    membrane rejected something, so a fix that dropped them would be a
    regression dressed as a speedup.
    """
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)

    def race(i):
        # Under real contention a loser can spend its whole retry budget before
        # the winner commits. Nothing is decided for that actor, so claim()
        # raises rather than inventing a refusal that names no claimant (#12).
        try:
            return store.claim("route", f"agent-{i}", fence=UNFENCED)
        except ClaimContentionError:
            return None

    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(race, range(16)))

    assert sum(1 for o in outcomes if o is True) == 1
    trace = store.trace()
    assert sum(1 for e in trace if e["kind"] == "CLAIMED") == 1

    refused = [e for e in trace if e["kind"] == "CLAIM_REFUSED"]
    contended = [e for e in trace if e["kind"] == "CLAIM_CONTENDED"]

    # Every loser is accounted for, as a refusal or as unresolved contention.
    # The count is what matters, not the split: the split is timing.
    assert len(refused) + len(contended) == 15, (
        f"{15 - len(refused) - len(contended)} losing actor(s) left no evidence "
        f"at all: {len(refused)} refused, {len(contended)} contended"
    )
    assert sum(1 for o in outcomes if o is False) == len(refused)
    assert sum(1 for o in outcomes if o is None) == len(contended)

    # And no refusal may be fabricated: a CLAIM_REFUSED has to name the actor
    # that actually holds the item.
    winner = next(e for e in trace if e["kind"] == "CLAIMED")["actor"]
    for e in refused:
        assert e["payload"]["current_claimant"] == winner, (
            f"refusal named {e['payload']['current_claimant']!r} but the item "
            f"is held by {winner!r}"
        )
    # the winner's identity is consistent between the trace and the work item
    claimed = next(e for e in trace if e["kind"] == "CLAIMED")
    assert store.refresh().work["route"].claimed_by == claimed["actor"] \
        if hasattr(store, "refresh") else True


def test_concurrent_events_keep_a_total_order(store):
    """Every event gets a distinct position; none collide or vanish."""
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda i: store.emit("PING", f"agent-{i}", {"i": i}), range(24)))
    trace = store.trace()
    pings = [e for e in trace if e["kind"] == "PING"]
    assert len(pings) == 24
    assert {e["payload"]["i"] for e in pings} == set(range(24))
    seqs = [e["seq"] for e in trace]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_holder_renews_its_own_claim(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    assert store.claim("route", "a", fence=UNFENCED) is True
    assert store.claim("route", "a", fence=UNFENCED) is True
    assert store.claim("route", "b", fence=UNFENCED) is False


def test_release_reopens_the_item(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    store.claim("route", "a", fence=UNFENCED)
    store.release("route", "a", "handing off", fence=UNFENCED)
    assert store.state.work["route"].status == "OPEN"
    assert store.claim("route", "b", fence=UNFENCED) is True


def test_unverified_plan_cannot_commit(store):
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    assert store.commit_plan("p1", "agent", fence=UNFENCED) is False
    assert store.state.committed_plan_id is None
    assert "COMMIT_REFUSED" in kinds(store)


def test_verified_plan_commits(store):
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is True
    assert store.refresh().committed_plan_id == "p1" if hasattr(store, "refresh") \
        else store.state.committed_plan_id == "p1"


def test_rejection_records_its_reason(store):
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    store.reject_plan("p1", "verifier", "transit crosses severe weather", fence=UNFENCED)
    plan = store.get_plan("p1")
    assert plan.verified is False
    assert plan.rejection_reason == "transit crosses severe weather"


def test_revoke_requires_being_the_committed_plan(store):
    store.add_plan(CandidatePlan(id="p1", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    store.add_plan(CandidatePlan(id="p2", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={}), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    assert store.revoke_commit("p2", "verifier", "wrong plan", fence=UNFENCED) is False
    assert store.revoke_commit("p1", "verifier", "forecast changed", fence=UNFENCED) is True
    assert store.get_plan("p1").verified is False


def test_missing_plan_raises(store):
    with pytest.raises(KeyError):
        store.get_plan("nope")


def test_snapshot_round_trips_the_world(store):
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    store.claim("route", "transport-agent", fence=UNFENCED)
    snap = store.snapshot()
    assert snap["scenario"] == "stormslot"
    assert snap["work"]["route"]["claimed_by"] == "transport-agent"
    assert snap["committed_plan_id"] is None

# --- stale-plan protection ------------------------------------------
#
# A plan is bound to the revision it was computed against. If external truth
# arrives between planning and commit, the plan describes a world that no
# longer exists and must be refused -- by both backends, identically.


def payload_of(store, kind):
    """The payload of the first event of `kind`, or None."""
    for e in store.trace():
        if e["kind"] == kind:
            return e.get("payload") or {}
    return None


def _plan_at(revision, plan_id="p1"):
    return CandidatePlan(id=plan_id, scenario="stormslot", created_by="agent",
                         actions=[], metrics={}, basis_revision=revision)


def test_revision_starts_at_zero_and_advances_observably(store):
    assert store.state.revision == 0
    assert store.advance_revision("weather-agent", "forecast updated").revision == 1
    assert store.state.revision == 1
    body = payload_of(store, "STATE_REVISION_ADVANCED")
    assert body["revision"] == 1
    assert body["reason"] == "forecast updated"


def test_stale_verified_plan_cannot_commit(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    assert store.mark_verified("p1", "verifier", fence=UNFENCED) is True
    store.advance_revision("weather-agent", "storm arrived")

    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is False
    assert store.state.committed_plan_id is None

    body = payload_of(store, "COMMIT_REFUSED")
    assert body["reason"] == "stale"
    assert body["plan_revision"] == 0
    assert body["state_revision"] == 1


def test_stale_plan_cannot_even_verify(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert store.mark_verified("p1", "verifier", fence=UNFENCED) is False
    assert store.get_plan("p1").verified is False
    assert "stale" in (store.get_plan("p1").rejection_reason or "")

    body = payload_of(store, "VERIFY_REFUSED_STALE")
    assert body["plan_revision"] == 0 and body["state_revision"] == 1


def test_plan_bound_to_the_current_revision_still_commits(store):
    """The guard must refuse stale plans without refusing fresh ones."""
    store.advance_revision("weather-agent", "forecast updated")
    store.add_plan(_plan_at(1), fence=UNFENCED)
    assert store.mark_verified("p1", "verifier", fence=UNFENCED) is True
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is True
    assert store.state.committed_plan_id == "p1"


def test_rebind_lets_a_survivor_commit_and_records_both_revisions(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is False

    assert store.rebind_plan("p1", "verifier", expected_revision=1, fence=UNFENCED) == 1
    body = payload_of(store, "PLAN_REBOUND")
    assert body["from_revision"] == 0 and body["to_revision"] == 1

    assert store.mark_verified("p1", "verifier", fence=UNFENCED) is True
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is True


def test_each_advance_invalidates_again(store):
    """Binding tracks the world continuously, not just the first disruption."""
    store.advance_revision("weather-agent", "first")
    store.add_plan(_plan_at(1), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "second")
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is False
    assert store.state.revision == 2


def test_rebind_refuses_when_the_world_moved_past_the_verified_revision(store):
    """Compare-and-swap, not blind stamp.

    A caller re-binds on the strength of a verdict reached at some revision. If
    truth landed while that verdict was being reached, stamping whatever is
    current would bind the plan to a world no verifier ever checked it against.
    """
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.advance_revision("weather-agent", "first")

    # Caller verified against revision 1, but a second disruption landed since.
    store.advance_revision("weather-agent", "second")

    assert store.rebind_plan("p1", "verifier", expected_revision=1, fence=UNFENCED) is None
    assert store.get_plan("p1").basis_revision == 0, "must not be stamped"

    body = payload_of(store, "REBIND_REFUSED_STALE")
    assert body["verified_against_revision"] == 1
    assert body["state_revision"] == 2


def test_revoke_if_stale_withdraws_a_genuinely_stale_commitment(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert store.revoke_if_stale("p1", "verifier", "world moved", fence=UNFENCED) is True
    assert store.state.committed_plan_id is None
    assert store.get_plan("p1").verified is False


def test_revoke_if_stale_leaves_a_repaired_commitment_alone(store):
    """A commitment bound to the current revision has been validated against
    the world as it stands. A worker acting on an older verdict must not undo it."""
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")
    store.rebind_plan("p1", "worker-b", expected_revision=1, fence=UNFENCED)   # repaired

    assert store.revoke_if_stale("p1", "verifier", "world moved", fence=UNFENCED) is False
    assert store.state.committed_plan_id == "p1"
    assert store.get_plan("p1").verified is True
    assert payload_of(store, "REVOKE_SKIPPED_REPAIRED")["state_revision"] == 1


def test_revoke_if_stale_refuses_a_plan_that_is_not_the_commitment(store):
    store.add_plan(_plan_at(0, "p1"), fence=UNFENCED)
    store.add_plan(_plan_at(0, "p2"), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert store.revoke_if_stale("p2", "verifier", "wrong plan", fence=UNFENCED) is False
    assert store.state.committed_plan_id == "p1"


def test_revoke_if_revision_current_revokes_while_the_verdict_is_current(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "storm arrived")

    assert store.revoke_if_revision_current("p1", "verifier", "unsafe", 1, fence=UNFENCED) is True
    assert store.state.committed_plan_id is None


def test_revoke_if_revision_current_skips_an_obsolete_verdict(store):
    store.add_plan(_plan_at(0), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    store.advance_revision("weather-agent", "first")
    store.advance_revision("weather-agent", "second")

    # A verdict reached at revision 1, acted on at revision 2.
    assert store.revoke_if_revision_current("p1", "verifier", "unsafe", 1, fence=UNFENCED) is False
    assert store.state.committed_plan_id == "p1"
    body = payload_of(store, "REVOKE_SKIPPED_OBSOLETE_VERDICT")
    assert body["verdict_revision"] == 1 and body["state_revision"] == 2


def test_revoke_if_revision_current_still_revokes_a_freshly_bound_plan(store):
    """The distinction from revoke_if_stale, asserted directly.

    A plan bound to the current revision can still be genuinely refuted.
    revoke_if_stale would decline here; this primitive must not, or a
    just-rejected commitment would be stranded."""
    store.advance_revision("weather-agent", "storm arrived")
    store.add_plan(_plan_at(1), fence=UNFENCED)
    store.mark_verified("p1", "verifier", fence=UNFENCED)
    store.commit_plan("p1", "verifier", fence=UNFENCED)
    assert store.get_plan("p1").basis_revision == store.state.revision

    assert store.revoke_if_stale("p1", "verifier", "unsafe", fence=UNFENCED) is False
    assert store.revoke_if_revision_current("p1", "verifier", "unsafe", 1, fence=UNFENCED) is True
    assert store.state.committed_plan_id is None


# --- external-event idempotency --------------------------------------
#
# Pub/Sub delivers at least once. With revision binding in place, a redelivery
# that advanced the revision twice would invalidate every plan computed against
# the revision in between -- correct work refused because of a change that
# never happened. Deduplication is therefore part of the revision contract,
# not a performance concern.


def advances(store):
    return [e for e in store.trace() if e["kind"] == "STATE_REVISION_ADVANCED"]


def test_a_redelivered_event_does_not_advance_the_revision(store):
    assert store.advance_revision("weather-agent", "forecast", event_id="m-1").revision == 1
    store.complete_event("m-1", "weather-agent", fence=UNFENCED)
    assert store.advance_revision("weather-agent", "forecast", event_id="m-1") is None

    assert store.state.revision == 1
    assert len(advances(store)) == 1
    body = payload_of(store, "DUPLICATE_EVENT_IGNORED")
    assert body["event_id"] == "m-1"
    assert body["revision"] == 1
    assert body["first_seen_revision"] == 1
    # A revision, never a timestamp: gate 6 compares trace payloads across replays.
    assert not any(isinstance(v, str) and "T" in v and ":" in v
                   for v in body.values()), f"wall clock leaked into payload: {body}"


def test_distinct_events_each_advance(store):
    assert store.advance_revision("weather-agent", "first", event_id="m-1").revision == 1
    assert store.advance_revision("weather-agent", "second", event_id="m-2").revision == 2
    assert store.state.revision == 2
    assert len(advances(store)) == 2


def test_events_without_an_id_are_not_deduplicated(store):
    """No delivery identity means no basis to collapse on.

    A synthesised constant would make every local trigger look like a
    redelivery of the same message and silently drop all but the first.
    """
    assert store.advance_revision("weather-agent", "local trigger").revision == 1
    assert store.advance_revision("weather-agent", "local trigger").revision == 2
    assert store.state.revision == 2
    assert "DUPLICATE_EVENT_IGNORED" not in [e["kind"] for e in store.trace()]


def test_concurrent_delivery_of_one_message_advances_exactly_once(store):
    """The race, not the sequence.

    At-least-once delivery means two subscribers can be handed the same message
    at the same moment. A check that is not inside the transaction that advances
    the revision will let both through.
    """
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)   # warm the run doc
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(
            lambda _: store.advance_revision("weather-agent", "forecast",
                                             event_id="m-race"),
            range(6)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"expected exactly one advance, got {winners}"
    assert winners[0].revision == 1
    assert len(advances(store)) == 1, "the revision advanced more than once"
    # The five losers acknowledge without repeating the work: the winner still
    # holds a live lease, so this is contention, not an abandoned application.
    assert sum(1 for e in store.trace()
               if e["kind"] == "DUPLICATE_EVENT_IN_FLIGHT") == 5


def test_deduplication_survives_reopening_the_store(store, reopen):
    """Restart, or a second Cloud Run instance, must still refuse the duplicate."""
    store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.complete_event("m-1", "weather-agent", fence=UNFENCED)
    assert store.has_processed_event("m-1") is True

    fresh = reopen(store)
    assert fresh.has_processed_event("m-1") is True
    assert fresh.advance_revision("weather-agent", "forecast", event_id="m-1") is None
    assert fresh.state.revision == 1


def test_an_unfinished_application_survives_reopening(store, reopen):
    """The half the two-phase scheme exists for.

    A store opened after another worker advanced but never finished must see
    the unfinished application -- otherwise it advances the revision a second
    time for the same message, which is the whole failure being prevented.
    """
    assert store.advance_revision("weather-agent", "forecast", event_id="m-1").revision == 1
    # no complete_event: the worker died here

    fresh = reopen(store)
    assert fresh.has_processed_event("m-1") is False
    fresh.event_lease_seconds = 0          # the original worker is presumed gone
    assert fresh.advance_revision("weather-agent", "forecast", event_id="m-1").revision == 1, \
        "a reopened store advanced the revision again for the same message"
    assert fresh.state.revision == 1
    assert "EVENT_APPLICATION_RESUMED" in [e["kind"] for e in fresh.trace()]


def test_an_abandoned_application_is_resumed_immediately(store):
    """Abandonment does not wait for the lease.

    A redelivery after a NACK arrives within seconds, far inside any sane
    lease. If only the lease could license repair, that redelivery -- the one
    the transport sent precisely because the work failed -- would be dismissed
    as contention with a worker that is already dead.
    """
    assert store.advance_revision("weather-agent", "forecast", event_id="m-1").revision == 1
    store.abandon_event("m-1", "weather-agent", "provider timed out", fence=UNFENCED)

    # Lease untouched and still live; repair must happen anyway.
    assert store.advance_revision("weather-agent", "forecast", event_id="m-1").revision == 1
    assert store.state.revision == 1
    kinds_seen = [e["kind"] for e in store.trace()]
    assert "EVENT_APPLICATION_ABANDONED" in kinds_seen
    assert "EVENT_APPLICATION_RESUMED" in kinds_seen
    assert "DUPLICATE_EVENT_IN_FLIGHT" not in kinds_seen


def test_abandoning_a_completed_event_does_nothing(store):
    store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.complete_event("m-1", "weather-agent", fence=UNFENCED)
    store.abandon_event("m-1", "weather-agent", "late failure report", fence=UNFENCED)
    assert store.has_processed_event("m-1") is True
    assert "EVENT_APPLICATION_ABANDONED" not in [e["kind"] for e in store.trace()]


# --- worker fencing ---------------------------------------------------
#
# Revision numbers cannot separate a superseded worker from its replacement:
# a resumed application deliberately does not advance the revision, so both
# legitimately operate at the same world revision. The attempt number closes
# that dimension.
#
#     event E, application attempt A
#             -> all effects produced for E carry A
#             -> resume creates A+1
#             -> anything from A is thereafter incapable of
#                changing authoritative operational state


def superseded_pair(store):
    """Return (stale_fence, live_fence) for one event, A superseded by A+1."""
    first = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.event_lease_seconds = 0            # A goes silent
    second = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    assert first.fence.attempt == 1 and second.fence.attempt == 2
    return first.fence, second.fence


def test_a_resume_mints_the_next_attempt(store):
    stale, live = superseded_pair(store)
    assert stale.event_id == live.event_id == "m-1"
    assert (stale.attempt, live.attempt) == (1, 2)
    body = payload_of(store, "EVENT_APPLICATION_RESUMED")
    assert body["attempt"] == 2


def test_a_superseded_attempt_cannot_change_authoritative_state(store):
    stale, live = superseded_pair(store)
    store.add_plan(_plan_at(1), live)         # the live attempt may work

    for call in (
        lambda: store.add_plan(_plan_at(1, "p2"), stale),
        lambda: store.mark_verified("p1", "agent", stale),
        lambda: store.reject_plan("p1", "agent", "no", stale),
        lambda: store.commit_plan("p1", "agent", stale),
        lambda: store.complete_event("m-1", "agent", stale),
    ):
        with pytest.raises(SupersededWorkerError):
            call()

    assert "p2" not in store.state.plans, "a superseded attempt proposed a plan"
    assert store.get_plan("p1").verified is False
    assert store.state.committed_plan_id is None
    assert store.has_processed_event("m-1") is False, \
        "a superseded attempt declared the event applied"


def test_a_superseded_attempt_cannot_revoke_or_rebind(store):
    stale, live = superseded_pair(store)
    store.add_plan(_plan_at(1), live)
    store.mark_verified("p1", "verifier", live)
    store.commit_plan("p1", "verifier", live)

    with pytest.raises(SupersededWorkerError):
        store.rebind_plan("p1", "agent", 1, stale)
    with pytest.raises(SupersededWorkerError):
        store.revoke_commit("p1", "agent", "stale worker", stale)
    with pytest.raises(SupersededWorkerError):
        store.revoke_if_stale("p1", "agent", "stale worker", stale)
    with pytest.raises(SupersededWorkerError):
        store.revoke_if_revision_current("p1", "agent", "stale worker", 1, stale)

    assert store.state.committed_plan_id == "p1", \
        "a superseded attempt withdrew its replacement's commitment"


def test_the_live_attempt_can_do_everything_the_stale_one_cannot(store):
    """The fence must refuse the superseded worker without hobbling the live one."""
    stale, live = superseded_pair(store)
    store.add_plan(_plan_at(1), live)
    assert store.mark_verified("p1", "verifier", live) is True
    assert store.commit_plan("p1", "verifier", live) is True
    store.complete_event("m-1", "weather-agent", live)
    assert store.has_processed_event("m-1") is True


def test_a_completed_event_fences_out_even_its_own_attempt(store):
    """Once applied, the attempt that applied it is spent.

    Otherwise a worker could keep writing effects for an event the trace has
    already recorded as finished, which is exactly the untruthful terminator
    the fence exists to prevent. The refusal names the real reason: finished,
    not superseded -- nobody replaced this worker, its work is simply over.
    """
    lease = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.add_plan(_plan_at(1), lease.fence)
    store.complete_event("m-1", "weather-agent", lease.fence)

    with pytest.raises(EventAlreadyAppliedError):
        store.mark_verified("p1", "agent", lease.fence)
    assert store.get_plan("p1").verified is False
    assert "EFFECT_REFUSED_SUPERSEDED" not in [e["kind"] for e in store.trace()], \
        "a finished event was reported as a supersession"


def test_completing_an_applied_event_twice_is_a_no_op(store):
    """complete_event is idempotent, and the fence must not take that away."""
    lease = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.complete_event("m-1", "weather-agent", lease.fence)
    before = len(store.trace())
    store.complete_event("m-1", "weather-agent", lease.fence)   # must not raise
    assert len(store.trace()) == before


def test_a_superseded_attempt_cannot_abandon_the_live_one(store):
    """The primitive that decides who owns the event must itself be fenced.

    `state` is what advance_revision reads to choose a worker. A superseded
    attempt writing it would invite a third worker into an application its
    replacement is still performing -- the loser of a race ejecting the winner.
    """
    stale, live = superseded_pair(store)
    with pytest.raises(SupersededWorkerError):
        store.abandon_event("m-1", "agent", "stale worker failed", stale)

    marker = store.state.pending_events["m-1"]
    assert marker["state"] == "in_progress", "a superseded attempt abandoned the live one"
    assert marker["attempt"] == live.attempt

    # The live attempt may still finish normally.
    store.complete_event("m-1", "weather-agent", live)
    assert store.has_processed_event("m-1") is True


def test_unfenced_work_is_unaffected(store):
    """Work with no delivery identity carries no fence and nothing can supersede it."""
    store.advance_revision("weather-agent", "local trigger")
    store.add_plan(_plan_at(1), fence=UNFENCED)
    assert store.mark_verified("p1", "verifier", fence=UNFENCED) is True
    assert store.commit_plan("p1", "verifier", fence=UNFENCED) is True


def test_concurrent_refusals_do_not_overwrite_each_others_evidence(store):
    """Refusal evidence must be per-refusal, not parked on the store.

    Firestore cannot write a refusal inside the transaction that raises -- the
    write rolls back with it -- so it is recorded on the way out. Holding that
    record on the store instead of on the exception makes it shared mutable
    state: workers refused at the same moment overwrite one another, and the
    trace attributes a refusal to the wrong attempt or loses it.
    """
    import threading

    # Three attempts, all superseded by a fourth.
    fences = []
    for _ in range(3):
        lease = store.advance_revision("weather-agent", "delivery", event_id="m-1")
        fences.append(lease.fence)
        store.event_lease_seconds = 0
    store.advance_revision("weather-agent", "delivery", event_id="m-1")

    ready, errors = threading.Barrier(len(fences)), []

    def refuse(fence):
        ready.wait(timeout=5)
        try:
            store.add_plan(_plan_at(1, f"p{fence.attempt}"), fence)
        except SupersededWorkerError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=refuse, args=(f,)) for f in fences]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert len(errors) == len(fences), "a superseded write was not refused"
    # Each exception carries its own refusal, naming its own attempt.
    assert sorted(e.refusal["attempt"] for e in errors) == \
        sorted(f.attempt for f in fences)

    refusals = [e["payload"] for e in store.trace()
                if e["kind"] == "EFFECT_REFUSED_SUPERSEDED"]
    assert len(refusals) == len(fences), \
        f"expected {len(fences)} refusal records, trace has {len(refusals)}"
    assert sorted(r["attempt"] for r in refusals) == sorted(f.attempt for f in fences), \
        "refusal records were cross-wired between concurrent workers"
    assert not store.state.plans, "a superseded attempt proposed a plan"


def test_a_superseded_attempt_cannot_touch_work_items(store):
    """Work items are authoritative operational state: claimed_by and status.

    Fenced in this change, and asserted here on both backends -- the contract
    file exists so a backend swap cannot quietly change what any of this means.
    """
    stale, live = superseded_pair(store)
    store.create_work(WorkItem("route", "find departure"), fence=live)

    with pytest.raises(SupersededWorkerError):
        store.claim("route", "transport-agent", stale)
    assert store.state.work["route"].claimed_by is None

    with pytest.raises(SupersededWorkerError):
        store.create_work(WorkItem("late", "work from a dead worker"), fence=stale)
    assert "late" not in store.state.work

    assert store.claim("route", "transport-agent", live) is True
    with pytest.raises(SupersededWorkerError):
        store.release("route", "transport-agent", "stale release", stale)
    assert store.state.work["route"].claimed_by == "transport-agent", \
        "a superseded attempt released its replacement's claim"


def test_an_already_applied_refusal_reaches_the_trace(store):
    """Both kinds of refusal are recorded, not just supersession.

    A refused effect that exists only as a stack trace is the failure the
    event log exists to prevent, and that standard does not apply to one
    refusal kind and not the other.
    """
    lease = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.complete_event("m-1", "weather-agent", lease.fence)

    with pytest.raises(EventAlreadyAppliedError):
        store.add_plan(_plan_at(1), lease.fence)

    body = payload_of(store, "EFFECT_REFUSED_ALREADY_APPLIED")
    assert body is not None, "an already-applied refusal left no record"
    assert body["event_id"] == "m-1" and body["attempt"] == lease.fence.attempt
    assert body["effect"] == "propose a plan"
    assert "EFFECT_REFUSED_SUPERSEDED" not in [e["kind"] for e in store.trace()]


def test_abandoning_an_applied_event_is_a_no_op_on_both_backends(store):
    """A worker reporting a genuine fault must not have that fault replaced.

    abandon_event is called from inside a scenario's own error handler. If it
    raised when the event was already applied, the reported failure would
    become "already applied" and the real cause would survive only in
    __context__ -- and it would do so on the deployed backend only.
    """
    lease = store.advance_revision("weather-agent", "forecast", event_id="m-1")
    store.complete_event("m-1", "weather-agent", lease.fence)

    store.abandon_event("m-1", "weather-agent", "TimeoutError: provider down",
                        lease.fence)          # must not raise
    assert store.has_processed_event("m-1") is True
    assert "EVENT_APPLICATION_ABANDONED" not in [e["kind"] for e in store.trace()]
