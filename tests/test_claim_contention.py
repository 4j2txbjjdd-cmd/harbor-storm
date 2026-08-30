"""Exhausted retries must produce a truthful outcome or no outcome at all.

Issue #12. `claim()` puts every racing actor on one work document inside one
transaction. Under load the losers can spend their whole retry budget and the
client library raises `ValueError: Failed to commit transaction in N attempts`.
Before this change that escaped `claim()`, which meant two things at once: the
caller got an exception where the contract promises a bool, and the refusal
evidence the membrane depends on was simply missing from the trace.

The fix is a classification, and the classification is the deliverable:

    exhausted retries
      -> authoritative reread
         another actor definitively owns it -> False + a truthful CLAIM_REFUSED
         this actor turns out to own it      -> True  + CLAIM_REAFFIRMED
         nobody owns it / reread failed      -> ClaimContentionError + a
                                                CLAIM_CONTENDED record, and
                                                never a CLAIM_REFUSED

These tests are deterministic and do not use the emulator. Emulator stress is a
secondary measurement: it is timing-dependent, so a green run is luck and
proves nothing about which branch was taken. Forcing each branch directly is
the only way to assert the semantics.

The branch worth the most attention is the third. A fabricated CLAIM_REFUSED
there would be indistinguishable from a real one in the trace -- same kind,
same actor, same work item -- so the tests assert the absence of a *refusal*,
not merely the presence of an exception.

That branch still records CLAIM_CONTENDED. The membrane's rule is that
contention must not be relieved by losing evidence; the fix for a false record
is a true one, not silence.
"""
from __future__ import annotations

import pytest

from app.core.store import ClaimContentionError

firestore_store = pytest.importorskip(
    "app.core.firestore_store", reason="google-cloud-firestore not installed")
FirestoreStateStore = firestore_store.FirestoreStateStore
_is_retry_exhaustion = firestore_store._is_retry_exhaustion


# --- fakes ---------------------------------------------------------------

class _Doc:
    def __init__(self, body, exists=True):
        self._body, self.exists = body, exists

    def to_dict(self):
        return dict(self._body) if self._body is not None else None


class _Ref:
    """Stands in for the work-item document; `get` is the authoritative reread."""

    def __init__(self, doc=None, raises=None):
        self._doc, self._raises = doc, raises
        self.reads = 0

    def get(self, transaction=None):
        self.reads += 1
        if self._raises is not None:
            raise self._raises
        return self._doc


class _Collection:
    def __init__(self, ref):
        self._ref = ref

    def document(self, _work_id):
        return self._ref


class _RunRef:
    def __init__(self, ref):
        self._ref = ref

    def collection(self, _name):
        return _Collection(self._ref)


def _exhaustion() -> ValueError:
    """The exact error the client library raises when the budget is spent."""
    from google.api_core import exceptions as gexc
    from google.cloud.firestore_v1.base_transaction import (
        _EXCEED_ATTEMPTS_TEMPLATE)
    try:
        raise ValueError(_EXCEED_ATTEMPTS_TEMPLATE.format(25)) from gexc.Aborted(
            "Transaction lock timeout.")
    except ValueError as exc:
        return exc


def _store(ref, *, txn_error):
    """A real FirestoreStateStore with only the collaborators claim() touches.

    Built without __init__ so no Firestore client, emulator or network is
    involved: the code under test is the real `claim` and the real
    `_resolve_claim_after_exhaustion`.
    """
    st = object.__new__(FirestoreStateStore)
    st.run_id = "run-1"
    st.run_ref = _RunRef(ref)
    st.emitted = []
    st.emit = lambda kind, actor, payload: st.emitted.append((kind, actor, payload))
    st.refresh = lambda *a, **k: None
    st._run_fenced = lambda _fn: (_ for _ in ()).throw(txn_error)
    st._txn_handle = lambda: None
    # Exercise the classification, not the backoff: retries are covered by
    # their own test below, and sleeping here would only slow the suite.
    st.claim_retries = 0
    st.claim_backoff_seconds = 0.0
    st._contention_backoff = lambda *a, **k: None
    return st


def _refusals(store):
    return [e for e in store.emitted if e[0] == "CLAIM_REFUSED"]


def _contended(store):
    return [e for e in store.emitted if e[0] == "CLAIM_CONTENDED"]


# --- the three branches --------------------------------------------------

def test_definitive_owner_becomes_a_truthful_refusal():
    ref = _Ref(_Doc({"claimed_by": "port-agent", "status": "CLAIMED"}))
    store = _store(ref, txn_error=_exhaustion())

    assert store.claim("route", "transport-agent") is False
    assert ref.reads == 1, "the outcome must come from an authoritative reread"

    (kind, actor, payload), = _refusals(store)
    assert (kind, actor) == ("CLAIM_REFUSED", "transport-agent")
    assert payload["current_claimant"] == "port-agent", (
        "a refusal must name the actor that actually holds the item"
    )
    assert payload["after_contention"] is True


def test_the_actor_may_discover_it_actually_won():
    """A lost response is not a lost claim.

    The commit can land and only its acknowledgement be dropped. The store is
    authoritative about that, so the reread decides, not the exception.
    """
    ref = _Ref(_Doc({"claimed_by": "transport-agent", "status": "CLAIMED"}))
    store = _store(ref, txn_error=_exhaustion())

    assert store.claim("route", "transport-agent") is True
    assert not _refusals(store), "winning must not record a refusal"
    assert [e[0] for e in store.emitted] == ["CLAIM_REAFFIRMED"]
    assert not _contended(store)


def test_unresolved_ownership_raises_without_fabricating_a_refusal():
    """The branch a fabricated refusal would hide in.

    Retries are spent and the item is still unclaimed: this actor did not win,
    but no other actor holds it either. There is no claimant to name, so any
    CLAIM_REFUSED written here would be false -- and false in a way that reads
    exactly like a true one on the trace.
    """
    ref = _Ref(_Doc({"claimed_by": None, "status": "OPEN"}))
    store = _store(ref, txn_error=_exhaustion())

    with pytest.raises(ClaimContentionError) as exc:
        store.claim("route", "transport-agent")

    assert not _refusals(store), (
        f"contention that resolved nothing fabricated a refusal: {store.emitted}"
    )
    # but the attempt is still on the record: contention must not be relieved
    # by losing evidence, only by not inventing it
    (_, actor, payload), = _contended(store)
    assert actor == "transport-agent"
    assert payload["resolved"] is False
    assert payload["reason"] == "ownership-undecided"
    assert "undecided" in str(exc.value)


def test_a_failed_reread_raises_without_fabricating_a_refusal():
    """If the reread itself fails, ownership is unknown -- say so."""
    ref = _Ref(raises=RuntimeError("emulator went away"))
    store = _store(ref, txn_error=_exhaustion())

    with pytest.raises(ClaimContentionError):
        store.claim("route", "transport-agent")
    assert not _refusals(store)
    assert _contended(store)[0][2]["reason"] == "reread-failed"


def test_a_vanished_work_item_raises_without_fabricating_a_refusal():
    ref = _Ref(_Doc(None, exists=False))
    store = _store(ref, txn_error=_exhaustion())

    with pytest.raises(ClaimContentionError):
        store.claim("route", "transport-agent")
    assert not _refusals(store)
    assert _contended(store)[0][2]["reason"] == "work-item-absent"


# --- what must not happen ------------------------------------------------

def test_the_raw_firestore_valueerror_never_reaches_the_caller():
    """`claim` may raise a Harbor outcome; it may not leak the backend's."""
    ref = _Ref(_Doc({"claimed_by": None, "status": "OPEN"}))
    raw = _exhaustion()
    store = _store(ref, txn_error=raw)

    with pytest.raises(ClaimContentionError) as exc:
        store.claim("route", "transport-agent")

    assert not isinstance(exc.value, ValueError)
    assert exc.value.__cause__ is raw, (
        "the backend error must be preserved as the cause for diagnosis, "
        "not discarded and not re-raised as itself"
    )


def test_an_unrelated_valueerror_is_not_swallowed():
    """Only exhaustion is reclassified. A real bug must still surface."""
    ref = _Ref(_Doc({"claimed_by": None, "status": "OPEN"}))
    store = _store(ref, txn_error=ValueError("work_id must be a string"))

    with pytest.raises(ValueError) as exc:
        store.claim("route", "transport-agent")
    assert not isinstance(exc.value, ClaimContentionError)
    assert ref.reads == 0, "a non-contention error must not trigger a reread"


def test_contention_is_not_a_supersession():
    """The two failure modes must stay distinguishable.

    Both mean "your effect did not land", but they call for opposite responses:
    a superseded worker must stop, and a contended one may retry.
    """
    from app.core.store import SupersededWorkerError
    assert not issubclass(ClaimContentionError, SupersededWorkerError)
    assert not issubclass(SupersededWorkerError, ClaimContentionError)


# --- the detector --------------------------------------------------------

def test_exhaustion_is_recognised_by_cause_and_by_message():
    from google.api_core import exceptions as gexc
    from google.cloud.firestore_v1.base_transaction import (
        _EXCEED_ATTEMPTS_TEMPLATE)

    assert _is_retry_exhaustion(_exhaustion())
    assert _is_retry_exhaustion(ValueError(_EXCEED_ATTEMPTS_TEMPLATE.format(5))), (
        "message alone must suffice; the budget in the text is not fixed at 25"
    )
    bare = ValueError("nope")
    bare.__cause__ = gexc.Aborted("lost")
    assert _is_retry_exhaustion(bare), "cause alone must suffice"

    assert not _is_retry_exhaustion(ValueError("bad config"))
    assert not _is_retry_exhaustion(KeyError("route"))


# --- retry before classification -----------------------------------------

def test_a_contended_claim_is_retried_before_it_is_classified():
    """Nobody winning is a worse outcome than somebody losing.

    The client library retries an aborted transaction with no backoff, which is
    right for write-write aborts and wrong for the lock timeouts a crowd on one
    document produces: every attempt retries in lockstep and the measured
    result was that NO actor won. Harbor re-enters the claim with jittered
    backoff so the herd desynchronises, and only classifies once its own budget
    is spent too.
    """
    ref = _Ref(_Doc({"claimed_by": "port-agent", "status": "CLAIMED"}))
    store = _store(ref, txn_error=_exhaustion())
    store.claim_retries = 3
    slept = []
    store._contention_backoff = lambda attempt, w, a: slept.append(attempt)

    assert store.claim("route", "transport-agent") is False
    assert slept == [0, 1, 2], (
        f"expected three backed-off retries before classifying, got {slept}"
    )
    assert ref.reads == 1, "classification must happen once, after the retries"


def test_the_backoff_is_jittered_per_actor():
    """Uniform sleeps would re-synchronise the herd they exist to break up."""
    import time as _time
    store = _store(_Ref(_Doc(None, exists=False)), txn_error=_exhaustion())
    store.claim_backoff_seconds = 0.01
    recorded = []
    real_sleep = _time.sleep
    firestore_store.time.sleep = lambda d: recorded.append(d)
    try:
        for actor in ("agent-1", "agent-2", "agent-3"):
            store._contention_backoff(0, "route", actor)
    finally:
        firestore_store.time.sleep = real_sleep

    assert len(set(recorded)) == len(recorded), (
        f"actors backed off by identical amounts: {recorded}"
    )
    assert all(d > 0 for d in recorded)
