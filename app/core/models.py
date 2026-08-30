from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class Event:
    seq: int
    kind: str
    actor: str
    payload: Dict[str, Any]
    ts: str = ""


@dataclass
class WorkItem:
    id: str
    need: str
    claimed_by: Optional[str] = None
    status: str = "OPEN"


@dataclass
class CandidatePlan:
    """A proposal, bound to the world it was computed against.

    `basis_revision` is the operational-state revision the planner read. A plan
    may only verify or commit while the world still stands at that revision; if
    new external truth has arrived since, the plan is stale and is refused. The
    default of 0 is not a wildcard -- it is the revision of an undisturbed run,
    so a plan built after a disruption and never bound is refused rather than
    quietly accepted.
    """

    id: str
    scenario: str
    created_by: str
    actions: List[Dict[str, Any]]
    metrics: Dict[str, float] = field(default_factory=dict)
    verified: bool = False
    rejection_reason: Optional[str] = None
    basis_revision: int = 0


@dataclass
class OperationalState:
    """Authoritative operational truth.

    `revision` advances every time external truth arrives. It is the version
    candidate plans bind to, and it is what makes staleness detectable rather
    than merely likely.
    """

    scenario: str
    facts: Dict[str, Any]
    target: Dict[str, Any]
    work: Dict[str, WorkItem] = field(default_factory=dict)
    plans: Dict[str, CandidatePlan] = field(default_factory=dict)
    committed_plan_id: Optional[str] = None
    revision: int = 0
    # External event ids that have been fully absorbed, mapped to the revision
    # they produced. Pub/Sub delivers at least once, and with revision binding
    # in place a redelivery that advanced the revision twice would invalidate
    # plans computed against the revision in between -- correct work refused
    # because of a phantom change.
    #
    # A revision, not a timestamp: this value reaches the event trace, and
    # gate 6 compares trace payloads byte for byte across two seeded replays.
    # Only completed events appear here; see `Store.advance_revision`.
    processed_events: Dict[str, int] = field(default_factory=dict)
    # Events whose revision advanced but whose application has not finished,
    # mapped to {"revision", "started_at", "state"} where state is
    # "in_progress" or "abandoned". This lives on the state, not on the store
    # instance: a second instance -- another Cloud Run container, or a
    # rehydrated store -- must see an unfinished application, or it will
    # advance the revision a second time for the same message.
    pending_events: Dict[str, Dict[str, Any]] = field(default_factory=dict)
