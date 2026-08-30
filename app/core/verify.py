from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.core.models import CandidatePlan
from app.core.store import Fence, Store, FenceArg, _MISSING


Verifier = Callable[[CandidatePlan], Tuple[bool, str]]


def verify_and_commit(store: Store, plan_id: str, verifier: Verifier,
                      fence: FenceArg = _MISSING) -> bool:
    """The membrane. Authoritative state moves only through this function.

    A plan is never trusted because an agent asserted it. It is recomputed
    against the same facts, and only a pass reaches commit_plan.

    Recomputation alone is not enough once execution is asynchronous. An agent
    may propose against revision R, sleep, and return after new truth has moved
    the world to R+1; the verifier would then pass a plan computed for a world
    that no longer exists. So staleness is checked first, and the store checks
    it again inside mark_verified and commit_plan -- the caller cannot skip it
    by reaching past this function.
    """
    plan = store.get_plan(plan_id)
    if plan.basis_revision != store.state.revision:
        store.reject_plan(
            plan_id, "verifier",
            f"stale: plan bound to revision {plan.basis_revision}, "
            f"world is at revision {store.state.revision}", fence)
        return False
    ok, reason = verifier(plan)
    if not ok:
        store.reject_plan(plan_id, "verifier", reason, fence)
        return False
    if not store.mark_verified(plan_id, "verifier", fence):
        return False
    return store.commit_plan(plan_id, "verifier", fence)


def reverify_committed(store: Store, verifier: Verifier,
                       fence: FenceArg = _MISSING) -> bool:
    """Re-run the verifier against the committed plan after new facts arrive.

    Returns True if the commitment still holds. If it no longer holds the
    commitment is revoked and the caller must replan.
    """
    plan_id = store.state.committed_plan_id
    if plan_id is None:
        return False

    # The revision the verifier is about to judge against. Verification is not
    # instantaneous, and nothing stops fresh truth landing while it runs, so the
    # re-bind below is conditional on the world still standing here.
    observed = store.state.revision

    ok, reason = verifier(store.get_plan(plan_id))
    if not ok:
        # A negative verdict may withdraw the commitment only while the world
        # still stands where that verdict was reached. If truth arrived while
        # the verifier ran, another worker may already have re-verified this
        # same commitment against the newer facts and repaired it, and acting
        # on the older verdict would let a stale failure undo a fresh success.
        store.revoke_if_revision_current(plan_id, "verifier", reason, observed,
                                         fence)
        return False

    # The plan was computed against an older revision but still holds under the
    # new facts. Re-bind it explicitly so it is bound to the world that actually
    # verified it; an unrebounded plan would be refused at the next commit, and
    # a silent exemption would defeat the point of binding.
    revision = store.rebind_plan(plan_id, "verifier", observed, fence)
    if revision is None:
        # Truth arrived mid-verification. The verdict describes a world that is
        # already gone, so it cannot be used to reaffirm anything. Withdraw the
        # commitment -- but only if it is still stale. Losing the re-bind race
        # means another worker may have re-verified this same plan against the
        # newer world and repaired it, and revoking unconditionally would have
        # the loser of the race undo the winner.
        store.revoke_if_stale(
            plan_id, "verifier",
            f"world advanced past revision {observed} during re-verification",
            fence)
        return False

    store.emit("COMMIT_REAFFIRMED", "verifier",
               {"plan_id": plan_id, "revision": revision})
    return True


def check_trace_integrity(trace: List[Dict[str, Any]]) -> List[str]:
    """Verify the displayed order rather than trusting it.

    The trace is evidence: its job is to show that verification preceded
    commitment. Event ids carry no shared counter, so under cross-instance
    clock skew a displayed order could invert a pair that the transactions
    themselves got right. Rather than let a judge read an inverted trace as
    fact, check the pairs that carry meaning and report violations loudly.

    Returns a list of human-readable violations; empty means the trace is
    self-consistent.
    """
    first: Dict[str, Dict[str, int]] = {}
    for e in trace:
        pid = (e.get("payload") or {}).get("plan_id")
        kind, seq = e.get("kind"), e.get("seq")
        if not pid or kind is None or seq is None:
            continue
        first.setdefault(pid, {}).setdefault(kind, seq)

    problems: List[str] = []
    for pid, seen in sorted(first.items()):
        proposed = seen.get("PLAN_PROPOSED")
        verified = seen.get("PLAN_VERIFIED")
        for kind in ("PLAN_VERIFIED", "PLAN_COMMITTED", "PLAN_REJECTED"):
            at = seen.get(kind)
            if at is not None and proposed is None:
                problems.append(f"{pid}: {kind} at {at} with no PLAN_PROPOSED")
            elif at is not None and at < proposed:
                problems.append(
                    f"{pid}: {kind} at {at} displayed before PLAN_PROPOSED at {proposed}")
        for kind in ("PLAN_COMMITTED", "COMMIT_REVOKED"):
            at = seen.get(kind)
            if at is None:
                continue
            if verified is None:
                problems.append(f"{pid}: {kind} at {at} with no preceding PLAN_VERIFIED")
            elif at < verified:
                problems.append(
                    f"{pid}: {kind} at {at} displayed before PLAN_VERIFIED at {verified}")
    return problems
