from __future__ import annotations
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from app.core.models import Event, OperationalState, WorkItem, CandidatePlan


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# How long an application that stopped reporting is presumed still alive.
#
# This is the backstop for a worker killed so hard it could not mark its own
# failure -- OOM, SIGKILL, a lost container. It is deliberately NOT the primary
# repair path: a worker that merely raises marks the application abandoned on
# its way out, and the next delivery resumes immediately regardless of this
# value. Relying on the lease alone would be wrong in the common case, because
# a redelivery after a NACK arrives inside any sane lease, long before the
# worker could be presumed dead.
EVENT_LEASE_SECONDS = 60.0


@dataclass(frozen=True)
class Fence:
    """Identity of one application attempt for one external event.

    Revision numbers cannot separate a superseded worker from its replacement,
    because a resumed application deliberately does not advance the revision --
    both workers legitimately operate at the same world revision. The attempt
    number closes that dimension: every effect produced for an event carries
    the attempt that produced it, a resume mints the next attempt, and effects
    from an older attempt are refused.
    """

    event_id: str
    attempt: int


class _Unfenced:
    """Explicit declaration that a mutation has no delivery identity.

    Passing this says "nothing can supersede this write", which is true of
    seeded setup, deterministic replay and direct CLI triggers. It exists so
    that statement has to be made rather than defaulted into: a caller who
    simply forgets the fence is a bug, and a caller who means it is not, and
    the two used to be spelled identically.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNFENCED"


UNFENCED = _Unfenced()


class _MissingFence:
    """Default for `fence`, meaning the caller said nothing at all."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<no fence supplied>"


_MISSING = _MissingFence()

# What an authoritative mutator will accept. `None` is deliberately absent:
# it used to mean "unfenced" and now means nothing, so old call sites fail
# loudly instead of silently keeping the behaviour the fence was added to end.
FenceArg = Any


class UnfencedMutationError(RuntimeError):
    """An authoritative mutation was attempted without saying how it is fenced.

    Raised when `fence` is omitted, or is None. The fix is never to pass None:
    it is to pass the Fence of the attempt doing the work, or UNFENCED to
    declare on the record that this write has no delivery identity.
    """


def _check_fence_supplied(fence: FenceArg, what: str) -> None:
    """Refuse a mutation that never said how it is fenced.

    Shared by both backends so the in-memory store and Firestore cannot drift
    on the one question that decides whether a write is authoritative.
    """
    if fence is _MISSING:
        raise UnfencedMutationError(
            f"refusing to {what} without a fence. Pass the Fence of the "
            f"attempt doing the work, or store.UNFENCED to declare that this "
            f"write has no delivery identity.")
    if fence is None:
        raise UnfencedMutationError(
            f"refusing to {what} with fence=None. None no longer means "
            f"unfenced -- pass store.UNFENCED to say that deliberately.")


@dataclass(frozen=True)
class Lease:
    """The right to apply one external event, at one revision, as one attempt.

    `fence` is UNFENCED for work with no delivery identity -- the CLI demo, a
    direct trigger -- which cannot be superseded because nothing else claims
    the same event.
    """

    revision: int
    fence: FenceArg = UNFENCED


class EventAlreadyAppliedError(RuntimeError):
    """An effect was attempted for an event whose application already finished.

    Distinct from being superseded: nobody replaced this worker, its own work
    is simply over. Conflating the two would make the trace misreport which
    failure occurred. Carries its refusal record for the same reason
    SupersededWorkerError does.
    """

    def __init__(self, message: str, refusal: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.refusal = refusal


class ClaimContentionError(RuntimeError):
    """A claim could not be resolved into an outcome under contention.

    Distinct from losing a claim. Losing is an *answer*: another actor owns the
    work, and `claim` returns False with a CLAIM_REFUSED on the record. This is
    the absence of an answer -- the backend exhausted its bounded retries and a
    fresh authoritative read still could not establish who owns the item.

    It exists because the alternative is worse in both directions. Returning
    False would manufacture a refusal naming no claimant, putting a false
    account of the failure on the trace that the membrane depends on. Letting
    the backend's own ValueError escape would make an infrastructure outcome
    indistinguishable from a bug in `claim`, and would break the contract that
    `claim` returns a bool.

    A caller seeing this knows the work item's ownership is *unknown*, and that
    retrying is legitimate because nothing was decided.
    """


class SupersededWorkerError(RuntimeError):
    """A worker tried to change authoritative state after being replaced.

    Its application attempt was resumed by someone else, so anything it does
    from here describes a world another worker has already moved past.

    The refusal record travels on the exception. A backend that cannot write
    it inside the transaction that raises -- Firestore rolls such a write back
    -- has to record it afterwards, and parking it on the store instead would
    make it shared mutable state: concurrent workers on one store would
    overwrite each other's evidence, and the trace would attribute a refusal
    to the wrong attempt or lose it entirely.
    """

    def __init__(self, message: str, refusal: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.refusal = refusal


def _expired(started_at: str, lease_seconds: float) -> bool:
    """True if an in-flight marker is old enough to presume its worker died."""
    try:
        started = datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return True          # unreadable marker: presume abandoned, allow repair
    return datetime.now(timezone.utc) - started > timedelta(seconds=lease_seconds)


def _stale_reason(plan_revision: int, state_revision: int) -> str:
    return (f"stale: plan bound to revision {plan_revision}, "
            f"world is at revision {state_revision}")


@runtime_checkable
class Store(Protocol):
    """The authoritative-state surface every scenario is written against.

    Two implementations exist: InMemoryStateStore (tests, deterministic demo)
    and FirestoreStateStore (deployed). Scenario code must never reach past
    this surface to mutate state objects directly, because the Firestore
    implementation has no shared object identity to mutate.
    """

    state: OperationalState

    def emit(self, kind: str, actor: str, payload: Dict[str, Any]) -> Event: ...
    def create_work(self, item: WorkItem, actor: str = "system",
                    fence: FenceArg = _MISSING) -> None: ...
    def claim(self, work_id: str, actor: str,
              fence: FenceArg = _MISSING) -> bool: ...
    def release(self, work_id: str, actor: str, reason: str,
                fence: FenceArg = _MISSING) -> None: ...
    def add_plan(self, plan: CandidatePlan,
                 fence: FenceArg = _MISSING) -> None: ...
    def get_plan(self, plan_id: str) -> CandidatePlan: ...
    def mark_verified(self, plan_id: str, actor: str,
                      fence: FenceArg = _MISSING) -> bool: ...
    def reject_plan(self, plan_id: str, actor: str, reason: str,
                    fence: FenceArg = _MISSING) -> None: ...
    def commit_plan(self, plan_id: str, actor: str,
                    fence: FenceArg = _MISSING) -> bool: ...
    def revoke_commit(self, plan_id: str, actor: str, reason: str,
                      fence: FenceArg = _MISSING) -> bool: ...
    def advance_revision(self, actor: str, reason: str,
                         payload: Optional[Dict[str, Any]] = None,
                         event_id: Optional[str] = None) -> Optional["Lease"]: ...
    def has_processed_event(self, event_id: str) -> bool: ...
    def complete_event(self, event_id: Optional[str], actor: str,
                       fence: FenceArg = _MISSING) -> None: ...
    def abandon_event(self, event_id: Optional[str], actor: str, reason: str,
                      fence: FenceArg = _MISSING) -> None: ...
    def rebind_plan(self, plan_id: str, actor: str, expected_revision: int,
                    fence: FenceArg = _MISSING) -> Optional[int]: ...
    def revoke_if_stale(self, plan_id: str, actor: str, reason: str,
                        fence: FenceArg = _MISSING) -> bool: ...
    def revoke_if_revision_current(self, plan_id: str, actor: str, reason: str,
                                   expected_revision: int,
                                   fence: FenceArg = _MISSING) -> bool: ...
    def snapshot(self) -> Dict[str, Any]: ...
    def trace(self) -> List[Dict[str, Any]]: ...


class InMemoryStateStore:
    """Deterministic in-process substrate.

    Atomic claim and commit operations are protected by one lock. The
    FirestoreStateStore mirrors these exact semantics with transactions.
    """

    def __init__(self, state: OperationalState):
        self.state = state
        self.events: List[Event] = []
        self._lock = Lock()
        self.event_lease_seconds = EVENT_LEASE_SECONDS

    # --- event log -------------------------------------------------

    def _append(self, kind: str, actor: str, payload: Dict[str, Any]) -> Event:
        ev = Event(seq=len(self.events) + 1, kind=kind, actor=actor,
                   payload=payload, ts=_now())
        self.events.append(ev)
        return ev

    def emit(self, kind: str, actor: str, payload: Dict[str, Any]) -> Event:
        with self._lock:
            return self._append(kind, actor, payload)

    # --- work items ------------------------------------------------

    def create_work(self, item: WorkItem, actor: str = "system",
                    fence: FenceArg = _MISSING) -> None:
        with self._lock:
            self._require_fence(fence, "create work")
            if item.id in self.state.work:
                return
            self.state.work[item.id] = item
            self._append("WORK_CREATED", actor, {"id": item.id, "need": item.need})

    def claim(self, work_id: str, actor: str,
              fence: FenceArg = _MISSING) -> bool:
        with self._lock:
            self._require_fence(fence, "claim work")
            item = self.state.work[work_id]
            if item.claimed_by == actor:
                # Idempotent lease renewal by the holder; not contention.
                self._append("CLAIM_REAFFIRMED", actor, {"work_id": work_id})
                return True
            if item.status != "OPEN" or item.claimed_by is not None:
                self._append("CLAIM_REFUSED", actor,
                             {"work_id": work_id, "current_claimant": item.claimed_by})
                return False
            item.claimed_by = actor
            item.status = "CLAIMED"
            self._append("CLAIMED", actor, {"work_id": work_id})
            return True

    def release(self, work_id: str, actor: str, reason: str,
                fence: FenceArg = _MISSING) -> None:
        with self._lock:
            self._require_fence(fence, "release work")
            item = self.state.work[work_id]
            item.claimed_by = None
            item.status = "OPEN"
            self._append("RELEASED", actor, {"work_id": work_id, "reason": reason})

    # --- external truth --------------------------------------------

    def has_processed_event(self, event_id: str) -> bool:
        return event_id in self.state.processed_events

    def _require_fence(self, fence: FenceArg, what: str) -> None:
        """Refuse an effect from a superseded application attempt.

        Called with the lock already held, so the check and the write it guards
        cannot be separated by a resume landing in between.
        """
        _check_fence_supplied(fence, what)
        if fence is UNFENCED:
            return
        if fence.event_id in self.state.processed_events:
            # Finished, not superseded. Saying "superseded by attempt None"
            # here would put a false account of the failure on the record,
            # immediately after a correct EVENT_APPLIED. It still belongs on
            # the record: a refused effect that exists only as a stack trace
            # is the failure the event log exists to prevent, and that holds
            # for both kinds of refusal.
            refusal = {"event_id": fence.event_id, "attempt": fence.attempt,
                       "applied_at_revision": self.state.processed_events[fence.event_id],
                       "effect": what}
            self._append("EFFECT_REFUSED_ALREADY_APPLIED", "store", refusal)
            raise EventAlreadyAppliedError(
                f"event {fence.event_id} has already been applied; "
                f"attempt {fence.attempt} may not {what}", refusal)
        pending = self.state.pending_events.get(fence.event_id)
        current = pending.get("attempt") if pending else None
        if current == fence.attempt:
            return
        refusal = {"event_id": fence.event_id, "attempt": fence.attempt,
                   "current_attempt": current, "effect": what}
        self._append("EFFECT_REFUSED_SUPERSEDED", "store", refusal)
        raise SupersededWorkerError(
            f"attempt {fence.attempt} of event {fence.event_id} tried to {what} "
            f"after being superseded by attempt {current}", refusal)

    def complete_event(self, event_id: Optional[str], actor: str,
                       fence: FenceArg = _MISSING) -> None:
        """Record that an event's application finished, not merely started.

        Absorption is two-phase because the revision advance and the work it
        licenses cannot share a transaction: re-verification, revocation and
        replanning all happen after the advance commits. If the marker meant
        "seen" rather than "finished", a process that died in between would
        leave the event marked absorbed and never applied -- and redelivery,
        which is the only repair at-least-once delivery offers, would be
        refused forever. So the marker is only closed here, once the work is
        actually done.
        """
        if event_id is None:
            return
        with self._lock:
            if event_id in self.state.processed_events:
                return          # already applied; completing again is a no-op
            self._require_fence(fence, "declare an event applied")
            pending = self.state.pending_events.pop(event_id, None)
            if pending is None:
                return
            revision = int(pending["revision"])
            self.state.processed_events[event_id] = revision
            self._append("EVENT_APPLIED", actor,
                         {"event_id": event_id, "revision": revision})

    def abandon_event(self, event_id: Optional[str], actor: str, reason: str,
                      fence: FenceArg = _MISSING) -> None:
        """Mark an application as failed so the next delivery repairs it now.

        A worker that raises knows it failed. Saying so is far better than
        letting the lease time out: the redelivery that follows a NACK arrives
        within seconds, long inside any lease, and would otherwise be dismissed
        as contention with a worker that is already dead.

        Fenced like every other write to the marker, and for the sharpest
        reason: `state` is the field advance_revision reads to decide who may
        work. A superseded attempt marking it abandoned would invite a third
        worker into an application its replacement is still performing, and
        evict that replacement -- the loser of a race ejecting the winner,
        through the one primitive left unguarded.
        """
        if event_id is None:
            return
        with self._lock:
            if event_id in self.state.processed_events:
                return          # already applied; nothing to abandon
            self._require_fence(fence, "abandon an event")
            pending = self.state.pending_events.get(event_id)
            if pending is None or pending.get("state") == "abandoned":
                return
            pending["state"] = "abandoned"
            self._append("EVENT_APPLICATION_ABANDONED", actor,
                         {"event_id": event_id, "reason": reason,
                          "revision": int(pending["revision"])})

    def advance_revision(self, actor: str, reason: str,
                         payload: Optional[Dict[str, Any]] = None,
                         event_id: Optional[str] = None) -> Optional["Lease"]:
        """Record that external truth arrived and the world moved.

        Every candidate computed against the previous revision is stale from
        this moment. Callers must advance *before* re-verifying anything, so a
        plan cannot slip through on the strength of the world it used to know.

        When `event_id` is given, the duplicate check and the advance share one
        lock. Pub/Sub delivers at least once, and a redelivery that advanced the
        revision a second time would invalidate every plan computed against the
        revision in between -- correct work refused on the strength of a change
        that never happened. Checking outside the lock would race exactly the
        way two concurrent deliveries of the same message do.

        Returns a Lease carrying the revision to work at -- newly advanced, or
        the existing one if a previous delivery advanced and never finished --
        and the fence identifying this attempt. Returns None when the event is
        fully absorbed, or while another attempt still holds it.
        An event with no id is not deduplicated and always advances; that is
        the local and demo path, where each trigger is genuinely a new event.
        """
        with self._lock:
            if event_id is not None:
                done = self.state.processed_events.get(event_id)
                if done is not None:
                    self._append("DUPLICATE_EVENT_IGNORED", actor,
                                 {"event_id": event_id, "reason": reason,
                                  "revision": self.state.revision,
                                  "first_seen_revision": done})
                    return None
                pending = self.state.pending_events.get(event_id)
                if pending is not None:
                    revision = int(pending["revision"])
                    alive = (pending.get("state") == "in_progress"
                             and not _expired(pending.get("started_at", ""),
                                              self.event_lease_seconds))
                    if alive:
                        # Another delivery is applying this message right now.
                        # The caller must NOT acknowledge: acknowledging would
                        # end redelivery, and if that worker then fails there
                        # would be nothing left to repair the run.
                        self._append("DUPLICATE_EVENT_IN_FLIGHT", actor,
                                     {"event_id": event_id, "reason": reason,
                                      "revision": revision})
                        return None
                    # Abandoned, or silent past its lease. Do not advance again
                    # -- but redo the work. Redelivery is the only repair an
                    # at-least-once transport offers.
                    pending["state"] = "in_progress"
                    pending["started_at"] = _now()
                    pending["attempt"] = int(pending.get("attempt", 1)) + 1
                    self._append("EVENT_APPLICATION_RESUMED", actor,
                                 {"event_id": event_id, "reason": reason,
                                  "revision": revision,
                                  "attempt": pending["attempt"]})
                    return Lease(revision, Fence(event_id, pending["attempt"]))
            self.state.revision += 1
            if event_id is not None:
                self.state.pending_events[event_id] = {
                    "revision": self.state.revision, "started_at": _now(),
                    "state": "in_progress", "attempt": 1}
            body: Dict[str, Any] = dict(payload or {})
            body.update({"revision": self.state.revision, "reason": reason})
            if event_id is not None:
                body["event_id"] = event_id
            self._append("STATE_REVISION_ADVANCED", actor, body)
            if event_id is None:
                return Lease(self.state.revision)
            return Lease(self.state.revision, Fence(event_id, 1))

    # --- candidate plans -------------------------------------------

    def add_plan(self, plan: CandidatePlan,
                 fence: FenceArg = _MISSING) -> None:
        with self._lock:
            self._require_fence(fence, "propose a plan")
            self.state.plans[plan.id] = plan
            self._append("PLAN_PROPOSED", plan.created_by,
                         {"plan_id": plan.id, "scenario": plan.scenario,
                          "actions": plan.actions, "metrics": plan.metrics})

    def get_plan(self, plan_id: str) -> CandidatePlan:
        return self.state.plans[plan_id]

    def mark_verified(self, plan_id: str, actor: str,
                      fence: FenceArg = _MISSING) -> bool:
        with self._lock:
            self._require_fence(fence, "verify a plan")
            plan = self.state.plans[plan_id]
            if plan.basis_revision != self.state.revision:
                plan.verified = False
                plan.rejection_reason = _stale_reason(plan.basis_revision,
                                                      self.state.revision)
                self._append("VERIFY_REFUSED_STALE", actor,
                             {"plan_id": plan_id,
                              "plan_revision": plan.basis_revision,
                              "state_revision": self.state.revision})
                return False
            plan.verified = True
            plan.rejection_reason = None
            self._append("PLAN_VERIFIED", actor,
                         {"plan_id": plan_id, "revision": plan.basis_revision})
            return True

    def reject_plan(self, plan_id: str, actor: str, reason: str,
                    fence: FenceArg = _MISSING) -> None:
        with self._lock:
            self._require_fence(fence, "reject a plan")
            plan = self.state.plans[plan_id]
            plan.verified = False
            plan.rejection_reason = reason
            self._append("PLAN_REJECTED", actor, {"plan_id": plan_id, "reason": reason})

    def commit_plan(self, plan_id: str, actor: str,
                    fence: FenceArg = _MISSING) -> bool:
        with self._lock:
            self._require_fence(fence, "commit a plan")
            plan = self.state.plans[plan_id]
            if not plan.verified:
                self._append("COMMIT_REFUSED", actor,
                             {"plan_id": plan_id, "reason": "unverified"})
                return False
            if plan.basis_revision != self.state.revision:
                self._append("COMMIT_REFUSED", actor,
                             {"plan_id": plan_id, "reason": "stale",
                              "plan_revision": plan.basis_revision,
                              "state_revision": self.state.revision})
                return False
            self.state.committed_plan_id = plan_id
            self._append("PLAN_COMMITTED", actor,
                         {"plan_id": plan_id, "revision": plan.basis_revision})
            return True

    def revoke_commit(self, plan_id: str, actor: str, reason: str,
                      fence: FenceArg = _MISSING) -> bool:
        """Withdraw an already-committed plan that later verification refutes."""
        with self._lock:
            self._require_fence(fence, "revoke a commitment")
            if self.state.committed_plan_id != plan_id:
                self._append("REVOKE_REFUSED", actor,
                             {"plan_id": plan_id, "reason": "not the committed plan"})
                return False
            plan = self.state.plans[plan_id]
            plan.verified = False
            plan.rejection_reason = reason
            self.state.committed_plan_id = None
            self._append("COMMIT_REVOKED", actor, {"plan_id": plan_id, "reason": reason})
            return True

    def revoke_if_revision_current(self, plan_id: str, actor: str, reason: str,
                                   expected_revision: int,
                                   fence: FenceArg = _MISSING) -> bool:
        """Withdraw a commitment only while the verdict that condemned it is current.

        A negative verdict is evidence about one particular world. If truth
        arrives before the verdict is acted on, the verdict describes a world
        that is gone, and another worker may already have re-verified the same
        commitment against the newer facts and repaired it. Acting on the older
        verdict would let a stale failure undo a fresh success.

        This deliberately does *not* compare `basis_revision` to the current
        revision. A plan freshly bound to the current revision can still be
        genuinely refuted, and refusing to revoke it would strand a commitment
        the verifier has just rejected. The question here is only whether the
        world still stands where the verdict was reached.

        Returns True only if it actually revoked.
        """
        with self._lock:
            self._require_fence(fence, "revoke on a negative verdict")
            if self.state.committed_plan_id != plan_id:
                self._append("REVOKE_REFUSED", actor,
                             {"plan_id": plan_id, "reason": "not the committed plan"})
                return False
            if self.state.revision != expected_revision:
                self._append("REVOKE_SKIPPED_OBSOLETE_VERDICT", actor,
                             {"plan_id": plan_id,
                              "verdict_revision": expected_revision,
                              "state_revision": self.state.revision,
                              "reason": "world moved before the negative verdict "
                                        "was acted on"})
                return False
            plan = self.state.plans[plan_id]
            plan.verified = False
            plan.rejection_reason = reason
            self.state.committed_plan_id = None
            self._append("COMMIT_REVOKED", actor,
                         {"plan_id": plan_id, "reason": reason,
                          "state_revision": self.state.revision})
            return True

    def revoke_if_stale(self, plan_id: str, actor: str, reason: str,
                        fence: FenceArg = _MISSING) -> bool:
        """Withdraw a commitment only while it is still stale.

        A worker that failed to re-bind cannot assume the commitment still needs
        withdrawing. Another worker may have re-verified the same plan against
        the newer world and repaired it in the meantime, and an unconditional
        revoke would then destroy work that is valid -- the loser of a race
        undoing the winner.

        So the check and the withdrawal share one lock: if the plan is bound to
        the current revision, someone has already validated it against the world
        as it stands and it is left alone. Returns True only if it actually
        revoked.
        """
        with self._lock:
            self._require_fence(fence, "revoke a stale commitment")
            if self.state.committed_plan_id != plan_id:
                self._append("REVOKE_REFUSED", actor,
                             {"plan_id": plan_id, "reason": "not the committed plan"})
                return False
            plan = self.state.plans[plan_id]
            if plan.basis_revision == self.state.revision:
                self._append("REVOKE_SKIPPED_REPAIRED", actor,
                             {"plan_id": plan_id,
                              "state_revision": self.state.revision,
                              "reason": "already re-verified against the current "
                                        "revision by another worker"})
                return False
            plan.verified = False
            plan.rejection_reason = reason
            self.state.committed_plan_id = None
            self._append("COMMIT_REVOKED", actor,
                         {"plan_id": plan_id, "reason": reason,
                          "plan_revision": plan.basis_revision,
                          "state_revision": self.state.revision})
            return True

    def rebind_plan(self, plan_id: str, actor: str, expected_revision: int,
                    fence: FenceArg = _MISSING) -> Optional[int]:
        """Re-bind a plan to the revision it was actually re-verified against.

        Only `reverify_committed` should call this, and only after the verifier
        has recomputed the plan against the new facts and passed. It is an
        explicit, logged re-binding rather than an exemption: the trace shows
        which revision the plan moved from and to.

        `expected_revision` is the revision the caller verified against, and the
        re-bind happens only while the world still stands there. Stamping
        whatever is current instead would reintroduce the very gap this closes:
        truth arriving *during* verification would be silently absorbed, and a
        plan would end up bound to a world no verifier ever checked it against.
        Returns the new revision, or None if the world moved underneath.
        """
        with self._lock:
            self._require_fence(fence, "rebind a plan")
            plan = self.state.plans[plan_id]
            now = self.state.revision
            if now != expected_revision:
                self._append("REBIND_REFUSED_STALE", actor,
                             {"plan_id": plan_id,
                              "verified_against_revision": expected_revision,
                              "state_revision": now})
                return None
            was = plan.basis_revision
            plan.basis_revision = now
            self._append("PLAN_REBOUND", actor,
                         {"plan_id": plan_id, "from_revision": was,
                          "to_revision": now})
            return now

    # --- read models -----------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        return asdict(self.state)

    def trace(self) -> List[Dict[str, Any]]:
        return [asdict(e) for e in self.events]


# Back-compatible name used by the original demo, tests and docs.
StateStore = InMemoryStateStore
