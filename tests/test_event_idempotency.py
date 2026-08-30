"""Duplicate external events must leave the world exactly as they found it.

Pub/Sub delivers at least once. Once candidate plans bind to a state revision,
a redelivery that advanced the revision a second time does more than waste
work: every plan computed against the revision in between is invalidated and
refused as stale, on the strength of a change that never happened. So a
duplicate must not advance the revision, must not re-run the disruption, and
must not leave a mark on the trace beyond the record of its own refusal.
"""
import base64
import json

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app import runner
from app.core.models import CandidatePlan
from app.core.store import UNFENCED
from app.core.store import InMemoryStateStore
from app.demo import weather_fixture, disrupted_weather_fixture, route_fixture
from app.scenarios import stormslot, harborwindow


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    for var in ("STATE_BACKEND", "WEATHER_PROVIDER", "GOOGLE_WEATHER_API_KEY",
                "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    runner.reset()
    yield
    runner.reset()


def kinds(store):
    return [e.kind for e in store.events]


# --- scenario level ---------------------------------------------------

@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_a_redelivered_disruption_changes_nothing(scenario):
    if scenario == "stormslot":
        store = InMemoryStateStore(stormslot.build_state())
        stormslot.run(store, weather_fixture(), route_fixture())
        apply = lambda: stormslot.disrupt(store, disrupted_weather_fixture(),
                                          route_fixture(), event_id="msg-1")
    else:
        store = InMemoryStateStore(harborwindow.build_state())
        harborwindow.run(store, weather_fixture())
        apply = lambda: harborwindow.disrupt(store, disrupted_weather_fixture(),
                                             event_id="msg-1")

    apply()
    after_first = {"revision": store.state.revision,
                   "committed": store.state.committed_plan_id,
                   "events": len(store.events),
                   "plans": len(store.state.plans)}

    apply()   # redelivery

    assert store.state.revision == after_first["revision"], "revision advanced twice"
    assert store.state.committed_plan_id == after_first["committed"]
    assert len(store.state.plans) == after_first["plans"], "replanned on a duplicate"
    # The only new event is the record of the refusal itself.
    assert len(store.events) == after_first["events"] + 1
    assert kinds(store)[-1] == "DUPLICATE_EVENT_IGNORED"


@pytest.mark.parametrize("scenario", ["stormslot", "harborwindow"])
def test_a_duplicate_emits_no_weather_event(scenario):
    """The dedup check sits before anything writes to the trace."""
    if scenario == "stormslot":
        store = InMemoryStateStore(stormslot.build_state())
        stormslot.run(store, weather_fixture(), route_fixture())
        apply = lambda: stormslot.disrupt(store, disrupted_weather_fixture(),
                                          route_fixture(), event_id="msg-1")
        weather_kind = "WEATHER_UPDATED"
    else:
        store = InMemoryStateStore(harborwindow.build_state())
        harborwindow.run(store, weather_fixture())
        apply = lambda: harborwindow.disrupt(store, disrupted_weather_fixture(),
                                             event_id="msg-1")
        weather_kind = "MARINE_WEATHER_UPDATED"

    apply()
    before = kinds(store).count(weather_kind)
    apply()
    assert kinds(store).count(weather_kind) == before, \
        "a duplicate announced weather that had already been absorbed"


def test_distinct_deliveries_still_apply():
    """Deduplication must not swallow genuinely new evidence."""
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-2")
    assert store.state.revision == 2


# --- HTTP surface -----------------------------------------------------

def push(run_id, message_id=None, profile="disrupted"):
    body = json.dumps({"run_id": run_id, "profile": profile}).encode()
    message = {"data": base64.b64encode(body).decode(), "attributes": {}}
    if message_id is not None:
        message["messageId"] = message_id
    return {"message": message, "subscription": "s"}


@pytest.fixture
def client():
    return TestClient(app)


def started_run(client):
    return client.post("/runs", json={"scenario": "stormslot",
                                      "profile": "baseline"}).json()["run_id"]


def test_duplicate_push_is_acknowledged_not_rejected(client):
    """A duplicate must ack. An error status makes Pub/Sub redeliver it."""
    run_id = started_run(client)

    first = client.post("/pubsub/push", json=push(run_id, "msg-1"))
    assert first.status_code == 200
    assert first.json()["duplicate"] is False

    second = client.post("/pubsub/push", json=push(run_id, "msg-1"))
    assert second.status_code == 200, "a duplicate must not return an error"
    assert second.json()["duplicate"] is True


def test_duplicate_push_does_not_advance_the_revision(client):
    run_id = started_run(client)
    client.post("/pubsub/push", json=push(run_id, "msg-1"))
    after_first = client.get(f"/runs/{run_id}").json()["trace"]

    client.post("/pubsub/push", json=push(run_id, "msg-1"))
    after_second = client.get(f"/runs/{run_id}").json()["trace"]

    advances = lambda t: [e for e in t if e["kind"] == "STATE_REVISION_ADVANCED"]
    assert len(advances(after_second)) == len(advances(after_first)) == 1
    assert after_second[-1]["kind"] == "DUPLICATE_EVENT_IGNORED"


def test_a_push_without_a_message_id_still_applies(client):
    """No delivery identity, no deduplication -- and no silent collapse."""
    run_id = started_run(client)
    for _ in range(2):
        assert client.post("/pubsub/push", json=push(run_id)).status_code == 200
    trace = client.get(f"/runs/{run_id}").json()["trace"]
    assert len([e for e in trace if e["kind"] == "STATE_REVISION_ADVANCED"]) == 2
    assert not [e for e in trace if e["kind"] == "DUPLICATE_EVENT_IGNORED"]


def test_encode_push_does_not_synthesise_a_shared_id():
    """A constant id would make every local envelope a duplicate of the last."""
    from app.events import DisruptionEvent, encode_push, parse_push
    a = encode_push(DisruptionEvent(run_id="r1"))
    b = encode_push(DisruptionEvent(run_id="r1"))
    assert "messageId" not in a["message"]
    assert parse_push(a).message_id is None and parse_push(b).message_id is None

    carried = encode_push(DisruptionEvent(run_id="r1", message_id="real-1"))
    assert parse_push(carried).message_id == "real-1"


# --- repair after a half-applied disruption ---------------------------
#
# The revision advance and the work it licenses cannot share a transaction:
# re-verification, revocation and replanning all happen after the advance
# commits. A process that dies in between has advanced the world without
# reacting to it. Redelivery is the only repair an at-least-once transport
# offers, so absorption is marked complete only once the work is done.


def half_applied_stormslot():
    """A run whose disruption advanced the revision and then died."""
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    committed_before = store.state.committed_plan_id
    # disrupt() got as far as the advance and the process died here.
    store.advance_revision("weather-agent", "forecast updated",
                           {"location": store.state.facts["port"]},
                           event_id="msg-1")
    return store, committed_before


def test_a_half_applied_disruption_is_repaired_by_redelivery():
    store, committed_before = half_applied_stormslot()
    assert store.state.revision == 1
    assert store.state.committed_plan_id == committed_before, \
        "precondition: the run is still committed to the pre-disruption plan"
    assert not store.has_processed_event("msg-1"), \
        "an event that never finished must not count as processed"

    store.event_lease_seconds = 0        # the worker that started it is gone
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    assert store.state.revision == 1, "repair must not advance the revision again"
    assert "EVENT_APPLICATION_RESUMED" in kinds(store)
    assert store.has_processed_event("msg-1"), "repair must close the marker"
    # The commitment has now actually been reconsidered against the new truth.
    assert "MARINE_WEATHER_UPDATED" not in kinds(store)
    assert "WEATHER_UPDATED" in kinds(store)


def test_the_repaired_run_is_no_longer_committed_to_a_refuted_plan():
    """The concrete harm: without repair the run stays committed to a
    departure the new forecast has already refuted."""
    store, committed_before = half_applied_stormslot()
    store.event_lease_seconds = 0
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    final = store.state.committed_plan_id
    if final is not None:
        assert store.get_plan(final).verified is True
        assert store.get_plan(final).basis_revision == store.state.revision
    assert final != committed_before or "COMMIT_REAFFIRMED" in kinds(store), \
        "the pre-disruption commitment survived without being re-verified"


def test_an_in_flight_duplicate_does_no_work_and_is_not_acknowledged():
    """Concurrent delivery is not an abandoned application.

    While the first delivery still holds a live lease, a second must do no
    work -- otherwise two workers replan the same disruption. It must also not
    be acknowledged: acking ends redelivery, and if the first worker then fails
    there would be nothing left to repair the run. See the 409 test below.
    """
    store, _ = half_applied_stormslot()
    before = len(store.events)

    # Default lease is live: the first delivery is presumed still working.
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    assert store.state.revision == 1
    assert kinds(store)[-1] == "DUPLICATE_EVENT_IN_FLIGHT"
    assert len(store.events) == before + 1, "an in-flight duplicate did work"
    assert "EVENT_APPLICATION_RESUMED" not in kinds(store)


def test_a_completed_event_is_never_resumed_even_after_the_lease_expires():
    """Expiry licenses repair, not reapplication."""
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")
    assert store.has_processed_event("msg-1")

    store.event_lease_seconds = 0
    revision_before, events_before = store.state.revision, len(store.events)
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    assert store.state.revision == revision_before
    assert len(store.events) == events_before + 1
    assert kinds(store)[-1] == "DUPLICATE_EVENT_IGNORED"


def test_no_event_payload_carries_a_wall_clock():
    """Gate 6 compares trace payloads byte for byte across two seeded replays.

    Event.ts is excluded from that comparison; payloads are not. A timestamp in
    a payload makes replay non-identical, and intermittently so -- two runs in
    the same millisecond would compare equal.
    """
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    for e in store.events:
        for key, value in e.payload.items():
            assert not (isinstance(value, str) and value.count(":") >= 2
                        and "T" in value), \
                f"{e.kind}.{key} carries a wall clock: {value!r}"


def test_the_api_reports_the_outcome_it_was_given_not_the_trace_tail(client):
    """The verdict is carried from the store, not recovered by scanning.

    A trace-tail scan is wrong under concurrency: any event appended to the
    same run between the transaction and the read flips the answer, and on
    Firestore the tail is ordered by a per-writer clock.
    """
    run_id = started_run(client)
    first = client.post("/pubsub/push", json=push(run_id, "msg-1")).json()
    assert first["outcome"] == "applied" and first["duplicate"] is False

    second = client.post("/pubsub/push", json=push(run_id, "msg-1")).json()
    assert second["outcome"] == "duplicate" and second["duplicate"] is True

    # An unrelated event lands after the duplicate; the verdict must not move.
    third = client.post("/pubsub/push", json=push(run_id, "msg-2")).json()
    assert third["outcome"] == "applied"
    fourth = client.post("/pubsub/push", json=push(run_id, "msg-1")).json()
    assert fourth["outcome"] == "duplicate", \
        "the verdict changed because an unrelated event moved the trace tail"


def test_replay_with_event_ids_is_byte_identical():
    """Gate 6's comparison, run over the path the gate does not yet exercise.

    gate.py compares json.dumps(payload, sort_keys=True) for every event across
    two seeded runs; Event.ts is excluded, payloads are not. The gate itself
    passes no event_id today, so this replays the way /pubsub/push actually
    drives a run -- with ids, and with a redelivery.
    """
    import json

    def replay():
        store = InMemoryStateStore(stormslot.build_state())
        stormslot.run(store, weather_fixture(), route_fixture())
        stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                          event_id="msg-1")
        stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                          event_id="msg-1")     # redelivery
        return [(e.kind, json.dumps(e.payload, sort_keys=True))
                for e in store.events]

    first, second = replay(), replay()
    assert len(first) == len(second)
    for i, (a, b) in enumerate(zip(first, second)):
        assert a == b, f"replay diverged at event {i}: {a} != {b}"


# --- failure reports beat lease expiry ---------------------------------

def failing_disrupt(store, event_id="msg-1"):
    """Drive a disruption whose work raises after the revision advanced."""
    class Exploding:
        def hourly(self, *_a, **_k):
            raise RuntimeError("weather provider is down")
    real = disrupted_weather_fixture()

    class OnceThenBoom:
        """Serves the forecast to the advance, then fails the work."""
        def __init__(self):
            self.calls = 0
        def hourly(self, *a, **k):
            self.calls += 1
            return real.hourly(*a, **k)

    class BadRoutes:
        def estimate(self, *_a, **_k):
            raise RuntimeError("route provider is down")

    with pytest.raises(RuntimeError):
        stormslot.disrupt(store, OnceThenBoom(), BadRoutes(), event_id=event_id)


def test_a_failed_application_is_marked_abandoned_not_left_in_flight():
    """The failure the lease alone gets wrong.

    The work raised, so the endpoint NACKs and Pub/Sub redelivers within
    seconds -- far inside the 60s lease. If that redelivery were treated as
    contention, the repair would be acknowledged and dropped, and the run would
    stay committed to a plan the delivered forecast refutes. Permanently.
    """
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    failing_disrupt(store)

    assert store.state.revision == 1, "the revision advanced before the failure"
    assert not store.has_processed_event("msg-1")
    assert "EVENT_APPLICATION_ABANDONED" in kinds(store)

    # The redelivery lands immediately, with the lease still live.
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    assert "EVENT_APPLICATION_RESUMED" in kinds(store)
    assert "DUPLICATE_EVENT_IN_FLIGHT" not in kinds(store), \
        "the repair delivery was dismissed as contention with a dead worker"
    assert store.state.revision == 1, "repair must not advance the revision again"
    assert store.has_processed_event("msg-1")


def test_the_repaired_run_reconsiders_the_commitment():
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    committed_before = store.state.committed_plan_id
    failing_disrupt(store)

    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")
    final = store.state.committed_plan_id
    assert final != committed_before or "COMMIT_REAFFIRMED" in kinds(store)
    if final is not None:
        assert store.get_plan(final).basis_revision == store.state.revision


def test_an_in_flight_duplicate_is_not_acknowledged_over_http(client):
    """409, not 200: the message must come back rather than be dropped."""
    run_id = started_run(client)
    # Advance without finishing, exactly as a killed worker would leave it.
    from app.config import Settings
    live = runner.get_store(run_id, Settings.from_env())
    live.advance_revision("weather-agent", "forecast updated",
                          {"location": live.state.facts["port"]},
                          event_id="msg-1")

    resp = client.post("/pubsub/push", json=push(run_id, "msg-1"))
    assert resp.status_code == 409, \
        "an in-flight duplicate was acknowledged; redelivery would stop here"


def test_completion_is_marked_when_the_commitment_survives(client):
    """The reaffirm path closes the marker too, not only the replan path."""
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    # Same forecast: the commitment still holds, so reverify reaffirms.
    stormslot.disrupt(store, weather_fixture(), route_fixture(), event_id="calm-1")

    assert "COMMIT_REAFFIRMED" in kinds(store), "precondition: the plan survived"
    assert store.has_processed_event("calm-1"), \
        "the reaffirm path advanced the revision without closing the marker"
    assert kinds(store)[-1] == "EVENT_APPLIED"


# --- worker fencing, end to end ---------------------------------------

def test_a_superseded_worker_cannot_finish_its_disruption():
    """The auditor's scenario, closed.

    Worker A stalls past its lease. B resumes, replans and closes the event.
    A then wakes and keeps working -- and every effect it attempts is refused,
    because its attempt has been superseded.
    """
    from app.core.store import SupersededWorkerError

    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())

    a = store.advance_revision("weather-agent", "forecast updated",
                               {"location": store.state.facts["port"]},
                               event_id="msg-1")
    assert a.fence.attempt == 1

    store.event_lease_seconds = 0            # A goes silent
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")      # B resumes and finishes
    assert store.has_processed_event("msg-1")
    committed_by_b = store.state.committed_plan_id

    # A wakes up holding attempt 1 and tries to carry on.
    from app.core.store import EventAlreadyAppliedError
    from app.core.verify import verify_and_commit, reverify_committed
    refused = (SupersededWorkerError, EventAlreadyAppliedError)
    with pytest.raises(refused):
        verify_and_commit(store, committed_by_b, lambda _p: (True, ""), a.fence)
    with pytest.raises(refused):
        reverify_committed(store, lambda _p: (False, "A disagrees"), a.fence)
    # Completing an already-applied event stays a harmless no-op -- the fence
    # must not turn idempotency into an error.
    before = len(store.events)
    store.complete_event("msg-1", "weather-agent", a.fence)
    assert len(store.events) == before

    assert store.state.committed_plan_id == committed_by_b, \
        "the superseded worker moved authoritative state after being replaced"


def test_every_effect_for_an_event_carries_its_attempt():
    """The invariant, stated as a trace property.

    Each resume mints a new attempt, and the trace records which attempt each
    application belonged to -- so 'who did this' is answerable after the fact.
    """
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    store.advance_revision("weather-agent", "forecast updated",
                           {"location": store.state.facts["port"]},
                           event_id="msg-1")
    store.event_lease_seconds = 0
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    resumed = [e for e in store.events if e.kind == "EVENT_APPLICATION_RESUMED"]
    applied = [e for e in store.events if e.kind == "EVENT_APPLIED"]
    assert resumed and resumed[0].payload["attempt"] == 2
    assert applied and applied[0].payload["event_id"] == "msg-1"


def test_a_superseded_worker_does_not_abandon_its_replacements_event():
    """Losing the event is not a failure to report.

    If a superseded worker reported failure it would abandon an application it
    no longer owns, inviting a third worker into work that is already done.
    """
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    store.advance_revision("weather-agent", "forecast updated",
                           {"location": store.state.facts["port"]},
                           event_id="msg-1")
    store.event_lease_seconds = 0
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")

    before = kinds(store).count("EVENT_APPLICATION_ABANDONED")
    # A third delivery finds the event complete and is simply ignored.
    stormslot.disrupt(store, disrupted_weather_fixture(), route_fixture(),
                      event_id="msg-1")
    assert kinds(store).count("EVENT_APPLICATION_ABANDONED") == before
    assert kinds(store)[-1] == "DUPLICATE_EVENT_IGNORED"


# --- the fence must be threaded, not merely implemented ----------------

class SupersedeOnFirstCall:
    """A provider that lets another delivery take the event over mid-flight."""

    def __init__(self, real, store, event_id="msg-1", after_calls=0):
        self.real, self.store, self.event_id = real, store, event_id
        self.after_calls, self.calls, self.fired = after_calls, 0, False

    def _supersede(self):
        self.calls += 1
        if self.fired or self.calls <= self.after_calls:
            return
        self.fired = True
        self.store.event_lease_seconds = 0
        self.store.advance_revision("weather-agent", "concurrent redelivery",
                                    event_id=self.event_id)

    def estimate(self, *a, **k):
        self._supersede()
        return self.real.estimate(*a, **k)

    def hourly(self, *a, **k):
        return self.real.hourly(*a, **k)


def superseded_midflight_store():
    store = InMemoryStateStore(stormslot.build_state())
    stormslot.run(store, weather_fixture(), route_fixture())
    return store


def test_a_worker_superseded_mid_replan_cannot_propose():
    """Catches a scenario that forgets to pass the fence into run().

    The store-level fence is useless if disrupt() drops it on the way to
    add_plan, and no store-level test can see that.
    """
    from app.core.store import SupersededWorkerError
    store = superseded_midflight_store()
    plans_before = set(store.state.plans)

    with pytest.raises(SupersededWorkerError):
        stormslot.disrupt(store, disrupted_weather_fixture(),
                          SupersedeOnFirstCall(route_fixture(), store),
                          event_id="msg-1")

    assert set(store.state.plans) == plans_before, \
        "a superseded worker proposed a plan through the scenario path"
    assert "EFFECT_REFUSED_SUPERSEDED" in kinds(store)


def test_a_worker_superseded_mid_replan_does_not_abandon_the_live_attempt():
    """The blocking hole: abandon_event must be fenced too.

    `state` is what advance_revision reads to decide who may work. A superseded
    attempt marking it abandoned invites a third worker into an application its
    replacement is still performing, and evicts that replacement.
    """
    from app.core.store import SupersededWorkerError
    store = superseded_midflight_store()

    with pytest.raises(SupersededWorkerError):
        stormslot.disrupt(store, disrupted_weather_fixture(),
                          SupersedeOnFirstCall(route_fixture(), store),
                          event_id="msg-1")

    assert "EVENT_APPLICATION_ABANDONED" not in kinds(store), \
        "a superseded worker abandoned an event it no longer owned"
    marker = store.state.pending_events["msg-1"]
    assert marker["state"] == "in_progress"
    assert marker["attempt"] == 2, "the live attempt was evicted"


def test_a_worker_superseded_before_verification_cannot_verify():
    """Catches the membrane dropping the fence between add_plan and verify."""
    from app.core.store import SupersededWorkerError
    from app.core.verify import verify_and_commit

    store = superseded_midflight_store()
    lease = store.advance_revision("weather-agent", "forecast updated",
                                   {"location": store.state.facts["port"]},
                                   event_id="msg-1")
    store.add_plan(CandidatePlan(id="mid", scenario="stormslot",
                                 created_by="agent", actions=[], metrics={},
                                 basis_revision=store.state.revision), lease.fence)

    def superseding_verifier(_plan):
        store.event_lease_seconds = 0
        store.advance_revision("weather-agent", "redelivery", event_id="msg-1")
        return True, ""

    with pytest.raises(SupersededWorkerError):
        verify_and_commit(store, "mid", superseding_verifier, lease.fence)
    assert store.get_plan("mid").verified is False
    assert store.state.committed_plan_id != "mid"


def test_a_superseded_delivery_is_not_acknowledged_over_http(client):
    """409, not 500: being superseded is a designed outcome, not a fault."""
    run_id = started_run(client)
    from app.config import Settings
    live = runner.get_store(run_id, Settings.from_env())
    live.advance_revision("weather-agent", "forecast updated",
                          {"location": live.state.facts["port"]},
                          event_id="msg-1")
    live.event_lease_seconds = 0
    live.advance_revision("weather-agent", "redelivery", event_id="msg-1")

    # A third delivery finds attempt 2 in progress with an expired lease and
    # resumes as attempt 3; attempt 2 would be refused if it kept working.
    resp = client.post("/pubsub/push", json=push(run_id, "msg-1"))
    assert resp.status_code in (200, 409), f"unexpected {resp.status_code}"
    assert resp.status_code != 500, "a designed outcome surfaced as a fault"


def test_a_worker_superseded_after_reverification_cannot_replan():
    """Catches disrupt() dropping the fence specifically on the way into run().

    Supersession fires late -- after re-verification has already failed and
    replanning has begun -- so the only guard left is the fence run() carries.
    """
    from app.core.store import SupersededWorkerError
    store = superseded_midflight_store()
    plans_before = set(store.state.plans)

    # The verifier consumes route estimates during re-verification; fire after
    # those, i.e. once run() is the caller.
    routes = SupersedeOnFirstCall(route_fixture(), store, after_calls=3)

    with pytest.raises(SupersededWorkerError):
        stormslot.disrupt(store, disrupted_weather_fixture(), routes,
                          event_id="msg-1")

    assert set(store.state.plans) == plans_before, \
        "a superseded worker replanned through run()"


def test_a_superseded_worker_cannot_claim_work():
    """Work items are authoritative operational state too."""
    from app.core.store import SupersededWorkerError
    from app.core.models import WorkItem

    store = InMemoryStateStore(stormslot.build_state())
    first = store.advance_revision("weather-agent", "forecast", event_id="msg-1")
    store.create_work(WorkItem("route", "find departure"), fence=first.fence)
    store.event_lease_seconds = 0
    store.advance_revision("weather-agent", "redelivery", event_id="msg-1")

    with pytest.raises(SupersededWorkerError):
        store.claim("route", "transport-agent", first.fence)
    assert store.state.work["route"].claimed_by is None, \
        "a superseded worker claimed work"
    with pytest.raises(SupersededWorkerError):
        store.create_work(WorkItem("extra", "late work"), fence=first.fence)
    assert "extra" not in store.state.work


def test_a_superseded_actor_toolkit_cannot_propose():
    """An actor proposing during an event application is an effect like any
    other, and must name the attempt it belongs to."""
    from app.core.store import SupersededWorkerError
    from app.agents.tools import ActorToolkit

    store = InMemoryStateStore(stormslot.build_state())
    first = store.advance_revision("weather-agent", "forecast", event_id="msg-1")
    kit = ActorToolkit(store, "transport-agent", ["route"],
                       ["port", "warehouse", "truck_available_hour"], "fenced",
                       fence=first.fence)
    kit.read_facts()

    store.event_lease_seconds = 0
    store.advance_revision("weather-agent", "redelivery", event_id="msg-1")

    with pytest.raises(SupersededWorkerError):
        kit.propose_plan(actions=[{"type": "dispatch_truck", "hour": 15}])
    assert store.state.plans == {}, "a superseded actor's proposal was recorded"


def test_a_worker_superseded_inside_the_verifier_cannot_commit():
    """The narrow window between proposing and verifying.

    add_plan guards a worker superseded before an iteration begins. It cannot
    guard one superseded *during* verification -- only the fence the scenario
    hands to verify_and_commit does. Supersession is timed to land on the
    verifier's own route lookup, after the last plan has been proposed.
    """
    from app.core.store import SupersededWorkerError
    store = superseded_midflight_store()
    committed_before = store.state.committed_plan_id

    # Measured: five add_plan calls consume ten route estimates; the eleventh
    # happens inside the verifier, between the final add_plan and mark_verified.
    routes = SupersedeOnFirstCall(route_fixture(), store, after_calls=10)

    with pytest.raises(SupersededWorkerError):
        stormslot.disrupt(store, disrupted_weather_fixture(), routes,
                          event_id="msg-1")

    # The prior commitment was already revoked by re-verification, before the
    # supersession -- that part was legitimate. What must not happen is the
    # superseded worker installing a replacement for it.
    assert store.state.committed_plan_id is None, \
        "a worker superseded mid-verification still committed a plan"
    assert not any(p.verified for p in store.state.plans.values()), \
        "a superseded worker's plan was marked verified"


def test_a_superseded_actor_toolkit_cannot_claim_work():
    """claim_work writes claimed_by and status -- authoritative, like proposing."""
    from app.core.store import SupersededWorkerError
    from app.core.models import WorkItem
    from app.agents.tools import ActorToolkit

    store = InMemoryStateStore(stormslot.build_state())
    first = store.advance_revision("weather-agent", "forecast", event_id="msg-1")
    store.create_work(WorkItem("route", "find departure"), fence=first.fence)
    kit = ActorToolkit(store, "transport-agent", ["route"],
                       ["port", "warehouse", "truck_available_hour"], "fenced",
                       fence=first.fence)

    store.event_lease_seconds = 0
    store.advance_revision("weather-agent", "redelivery", event_id="msg-1")

    with pytest.raises(SupersededWorkerError):
        kit.claim_work("route")
    assert store.state.work["route"].claimed_by is None, \
        "a superseded actor claimed work"
