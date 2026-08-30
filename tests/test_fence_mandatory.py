"""A fence must be stated, never defaulted into.

Issue #13 closed the case where a superseded worker's effects reach
authoritative state. This module closes the way that protection silently
decays: a mutator whose `fence` argument is optional. If `fence` may be
omitted, then the next adapter written against this store -- a Pub/Sub push
handler, an Agent Runtime dispatcher -- mutates authoritative state unfenced by
default, and nothing raises, nothing logs, and the trace looks correct.

These tests are structural on purpose. They do not exercise a scenario; they
assert properties of the mutator surface itself, so they fail when someone adds
a new authoritative mutator with a permissive default rather than only when
some future test happens to notice the consequence.
"""
from __future__ import annotations

import inspect

import pytest

from app.core.models import WorkItem
from app.scenarios import stormslot
from app.core.store import (UNFENCED, Fence, InMemoryStateStore, Store,
                            UnfencedMutationError, _MISSING)


def _fenced_methods(cls) -> dict:
    """Every public method on `cls` that takes a `fence` argument.

    Private helpers are excluded: `_require_fence` is the enforcement itself,
    not a mutator, and it takes `fence` positionally by construction.
    """
    out = {}
    for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        if "fence" in sig.parameters:
            out[name] = sig.parameters["fence"]
    return out


def _store() -> InMemoryStateStore:
    return InMemoryStateStore(stormslot.build_state())


# --- the structural guard ------------------------------------------------

def test_every_authoritative_mutator_requires_an_explicit_fence():
    """No mutator may default to a value that permits the write.

    This is the test that fails when someone adds a mutator with
    `fence=None` or `fence=UNFENCED` as its default. Both would let an event
    path mutate authoritative state without naming its attempt -- the first
    silently, the second while looking deliberate.
    """
    methods = _fenced_methods(InMemoryStateStore)
    assert methods, "found no fenced mutators; this test is looking in the wrong place"
    offenders = {
        name: param.default
        for name, param in methods.items()
        if param.default is not _MISSING and param.default is not inspect.Parameter.empty
    }
    assert not offenders, (
        f"these mutators can be called without stating a fence: {offenders}. "
        f"Give `fence` no default, or default it to _MISSING so omission raises."
    )


def test_the_protocol_and_the_implementation_agree():
    """The Store protocol must not advertise a laxer contract than the store.

    Scenario code is written against the protocol. If the protocol says `fence`
    is optional, a new caller is entitled to omit it, and the mismatch only
    surfaces at runtime in whichever backend is deployed.
    """
    proto = _fenced_methods(Store)
    impl = _fenced_methods(InMemoryStateStore)
    missing = set(impl) - set(proto)
    assert not missing, f"implementation has fenced mutators the protocol omits: {missing}"
    lax = {n: p.default for n, p in proto.items()
           if p.default is not _MISSING and p.default is not inspect.Parameter.empty}
    assert not lax, f"protocol advertises optional fences for: {lax}"


def test_both_backends_agree_on_the_fence_contract():
    """Firestore and in-memory must not drift on whether a fence is required.

    Parity matters more here than anywhere else: the demo runs in memory and
    the deployment runs on Firestore, so a laxer Firestore signature would mean
    the only path that mutates real authoritative state is the unprotected one.
    """
    firestore_store = pytest.importorskip(
        "app.core.firestore_store",
        reason="google-cloud-firestore not installed",
    )
    mem = _fenced_methods(InMemoryStateStore)
    fs = _fenced_methods(firestore_store.FirestoreStateStore)
    shared = set(mem) & set(fs)
    assert shared, "the two backends share no fenced mutators; parity check is vacuous"
    for name in sorted(shared):
        assert fs[name].default is mem[name].default, (
            f"{name}: Firestore default {fs[name].default!r} != "
            f"in-memory default {mem[name].default!r}"
        )
    lax = {n: fs[n].default for n in fs
           if fs[n].default is not _MISSING
           and fs[n].default is not inspect.Parameter.empty}
    assert not lax, f"Firestore advertises optional fences for: {lax}"


# --- the behaviour that guard protects -----------------------------------

def test_omitting_the_fence_raises_rather_than_writing():
    store = _store()
    with pytest.raises(UnfencedMutationError) as exc:
        store.create_work(WorkItem("route", "x"))
    assert "without a fence" in str(exc.value)
    assert not store.state.work, "the refused mutation still reached the store"


def test_none_is_no_longer_a_way_to_say_unfenced():
    """`None` used to mean unfenced. It must not quietly keep working.

    Every pre-#13 call site spelled "unfenced" as None. If None still passed,
    this whole change would be a no-op on exactly the code it exists to catch.
    """
    store = _store()
    with pytest.raises(UnfencedMutationError) as exc:
        store.create_work(WorkItem("route", "x"), fence=None)
    assert "fence=None" in str(exc.value)
    assert not store.state.work


def test_unfenced_is_accepted_so_seeded_paths_still_work():
    """Deterministic setup and replay must remain possible -- explicitly."""
    store = _store()
    store.create_work(WorkItem("route", "x"), fence=UNFENCED)
    assert "route" in store.state.work


def test_a_real_fence_is_accepted():
    store = _store()
    lease = store.advance_revision("weather-agent", "storm", event_id="msg-1")
    assert isinstance(lease.fence, Fence)
    store.create_work(WorkItem("route", "x"), fence=lease.fence)
    assert "route" in store.state.work


def test_a_new_event_path_caller_cannot_omit_the_fence():
    """The regression this module exists for.

    Simulates the future adapter: an event application is in flight, and a
    caller mutates authoritative state without carrying the attempt. Before
    this change that call succeeded silently; it must now refuse.
    """
    store = _store()
    lease = store.advance_revision("weather-agent", "storm", event_id="msg-1")
    store.create_work(WorkItem("route", "x"), fence=lease.fence)

    with pytest.raises(UnfencedMutationError):
        store.claim("route", "transport-agent")          # forgot the fence

    assert store.state.work["route"].claimed_by is None, (
        "an unfenced claim landed during an event application"
    )
    # and the fenced call still works, so the refusal is about the fence and
    # not about the store being wedged
    assert store.claim("route", "transport-agent", lease.fence) is True


def test_the_toolkit_does_not_reintroduce_the_default():
    """ActorToolkit must pass its fence through, not default it.

    The toolkit is the surface an actor mutates through. If it defaulted to
    UNFENCED, every actor built without an explicit fence would write unfenced
    while the store below it still looked strict.
    """
    from app.agents.tools import ActorToolkit

    sig = inspect.signature(ActorToolkit.__init__)
    assert sig.parameters["fence"].default is _MISSING, (
        "ActorToolkit defaults its fence; a toolkit built without one would "
        "write unfenced through a store that thinks it is protected"
    )

    store = _store()
    store.create_work(WorkItem("route", "x"), fence=UNFENCED)
    kit = ActorToolkit(store, "transport-agent", ["route"], [], "p")
    with pytest.raises(UnfencedMutationError):
        kit.claim_work("route")


def test_build_toolkits_requires_a_fence_from_its_caller():
    """The factory an adapter will use must not have a permissive default."""
    from app.agents.actors import build_toolkits

    sig = inspect.signature(build_toolkits)
    assert sig.parameters["fence"].default is inspect.Parameter.empty, (
        "build_toolkits defaults its fence; a D1 adapter could build six "
        "actors that all mutate unfenced without writing the word UNFENCED"
    )
