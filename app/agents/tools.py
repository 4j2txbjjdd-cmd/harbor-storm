"""Scope-bounded tools for the ADK-wrapped actors.

The design constraint is that wrapping actors in ADK must not turn the system
into a central chat orchestrator. Two rules keep that from happening:

1. **No tool can commit.** The toolkit exposes claim, report, and propose.
   There is deliberately no commit tool and no verify tool. An agent's most
   powerful move is to put a candidate in front of the verifier, and the
   verifier is ordinary deterministic code that no model can reach.
2. **Each actor gets only its own scope.** A toolkit is constructed for one
   actor and refuses work items and facts outside that actor's scope, so two
   agents cannot collapse into one by quietly sharing information.

The result is that swapping the deterministic proposer for an LLM proposer
changes who writes the candidate and changes nothing about what can commit.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional

from app.core.models import CandidatePlan, WorkItem
from app.core.store import Fence, FenceArg, Store, _MISSING


class UnboundProposalError(RuntimeError):
    """An actor proposed a plan without first reading the facts it binds to.

    A proposal is a claim about a particular world. An actor that never read
    that world has no basis to bind to, and substituting whatever revision is
    current would manufacture a binding it never earned -- exactly the stale
    reasoning the revision check exists to catch.
    """


class ToolScopeError(PermissionError):
    """An actor reached for something outside its scope."""


class ActorToolkit:
    """The complete set of actions one bounded actor may take."""

    def __init__(self, store: Store, actor: str, work_ids: List[str],
                 visible_facts: List[str], plan_prefix: str,
                 fence: "FenceArg" = _MISSING):
        self.store = store
        self.actor = actor
        self.work_ids = list(work_ids)
        self.visible_facts = list(visible_facts)
        # The newest revision this actor has actually observed. A proposal binds
        # to this, not to the revision at propose time: an actor that read facts
        # at R, slept, and woke after new truth moved the world to R+1 must have
        # its proposal refused, not silently stamped as current. Left unset until
        # the actor reads, because the describe-only path carries no store.
        self._observed_revision: Optional[int] = None
        self.plan_prefix = plan_prefix
        self._proposed = 0
        # Carried so an actor proposing during an event application produces an
        # effect that names the attempt it belongs to, like every other effect.
        # Not defaulted to UNFENCED: a toolkit built without saying how it is
        # fenced must fail at the store, not quietly write unfenced.
        self.fence = fence

    # --- scope guards ----------------------------------------------

    def _check_work(self, work_id: str) -> None:
        if work_id not in self.work_ids:
            raise ToolScopeError(
                f"{self.actor} may not act on work item {work_id!r}; its scope is "
                f"{self.work_ids}"
            )

    # --- tools -----------------------------------------------------

    def claim_work(self, work_id: str) -> Dict[str, Any]:
        """Claim a work item atomically. Returns whether this actor now holds it."""
        self._check_work(work_id)
        won = self.store.claim(work_id, self.actor, self.fence)
        return {"work_id": work_id, "claimed": won,
                "held_by": self.store.state.work[work_id].claimed_by}

    def read_facts(self) -> Dict[str, Any]:
        """Read only the operational facts inside this actor's scope."""
        facts = self.store.state.facts
        self._observed_revision = self.store.state.revision
        missing = [k for k in self.visible_facts if k not in facts]
        if missing:
            raise ToolScopeError(
                f"{self.actor} expects facts {missing} which this scenario does not "
                f"define; refusing to return a partial world"
            )
        return {k: facts[k] for k in self.visible_facts}

    def report_constraint(self, constraint: Dict[str, Any]) -> Dict[str, Any]:
        """Publish the constraint only this actor can see, onto the event trace."""
        ev = self.store.emit("CONSTRAINT_REPORTED", self.actor, dict(constraint))
        return {"recorded": True, "kind": ev.kind, "actor": ev.actor}

    def propose_plan(self, actions: List[Dict[str, Any]],
                     metrics: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Put a candidate plan in front of the verifier.

        This is the strongest action available to any agent. It does not commit
        anything: the plan is recorded as a candidate, and a deterministic
        verifier decides. Metrics supplied here are recorded for the trace and
        are ignored by the verifier.
        """
        if self._observed_revision is None:
            raise UnboundProposalError(
                f"{self.actor} proposed a plan without calling read_facts() first. "
                f"A proposal binds to the world the actor observed; with nothing "
                f"observed there is no basis revision to bind to. Call read_facts() "
                f"and propose against what it returns."
            )
        self._proposed += 1
        plan = CandidatePlan(
            id=f"{self.plan_prefix}-{self._proposed}",
            scenario=self.store.state.scenario,
            created_by=self.actor,
            actions=list(actions),
            metrics=dict(metrics or {}),
            basis_revision=self._observed_revision,
        )
        self.store.add_plan(plan, self.fence)
        return {"plan_id": plan.id, "status": "candidate",
                "note": "recorded as a candidate; the verifier decides whether it commits"}

    def read_trace(self) -> List[Dict[str, Any]]:
        """Read the shared event log. Read-only."""
        return self.store.trace()

    # --- ADK wiring -------------------------------------------------

    def as_tools(self) -> List[Any]:
        """Wrap the scoped methods as ADK FunctionTools."""
        from google.adk.tools import FunctionTool
        return [FunctionTool(fn) for fn in (self.claim_work, self.read_facts,
                                            self.report_constraint,
                                            self.propose_plan, self.read_trace)]

    def tool_names(self) -> List[str]:
        return ["claim_work", "read_facts", "report_constraint",
                "propose_plan", "read_trace"]
