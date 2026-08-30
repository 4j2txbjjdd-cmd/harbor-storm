"""ADK agent definitions for the bounded actors.

Each actor is one LlmAgent holding one ActorToolkit. There is no orchestrator
agent and no agent that can commit: agents propose, deterministic code decides.
`app.scenarios.*` remains the default path and stays fully deterministic; these
agents are an alternative *proposer*, which is the only role a model is allowed
to play in this system.
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from app.agents.tools import ActorToolkit
from app.core.store import Store, FenceArg, UNFENCED

# The competition requires Gemini 3.5+. The default is that floor rather than
# whatever happened to be current when this file was written: a default below
# the floor is a silent downgrade waiting for someone to forget to set
# ADK_MODEL, and `app.agents.execution.require_model_floor` refuses it anyway.
DEFAULT_MODEL = os.environ.get("ADK_MODEL", "gemini-3.5-flash")


@dataclass(frozen=True)
class ActorScope:
    name: str
    role: str
    work_ids: List[str]
    visible_facts: List[str]
    plan_prefix: str


# The scopes are genuinely disjoint on purpose. Two actors with the same facts
# and the same actions are one actor wearing two names, which fails hard gate 2.
ACTOR_SCOPES: Dict[str, List[ActorScope]] = {
    "stormslot": [
        ActorScope(
            "transport-agent",
            "You own the truck and the road. You know the route estimates and "
            "nothing about port handover rules or warehouse shifts.",
            ["route"],
            ["port", "warehouse", "truck_available_hour", "container_ready_hour",
             "pickup_deadline_hour"],
            "stormslot-agent-plan",
        ),
        ActorScope(
            "port-agent",
            "You own the terminal handover slot. You know which hours the port "
            "can physically release the container and what is currently booked.",
            ["slot"],
            ["port", "port_handover_hours", "booked_pickup_hour", "container_ready_hour"],
            "stormslot-port-plan",
        ),
        ActorScope(
            "warehouse-agent",
            "You own the receiving dock. You know opening hours and the shift "
            "change, and nothing about the road or the port.",
            ["receive"],
            ["warehouse", "warehouse_open_hour", "warehouse_close_hour",
             "warehouse_shift_change_hour"],
            "stormslot-wh-plan",
        ),
    ],
    "harborwindow": [
        ActorScope(
            "window-agent",
            "You own marine weather safety. You know the wind and rain limits "
            "and the crossing duration, and nothing about cargo or berths.",
            ["window"],
            ["harbor", "island", "max_wind_kph", "max_rain_mm", "crossing_hours"],
            "harbor-agent-plan",
        ),
        ActorScope(
            "cargo-agent",
            "You own the manifest. You know the cargo weight and the boat's "
            "capacity, and nothing about weather or sailing slots.",
            ["load"],
            ["cargo_kg", "boat_capacity_kg", "cargo_ready_hour"],
            "harbor-cargo-plan",
        ),
        ActorScope(
            "harbormaster-agent",
            "You own the sailing schedule. You know which slots exist, what is "
            "booked, and the island landing cutoff.",
            ["slot"],
            ["sailing_slots", "booked_departure_hour", "island_landing_cutoff_hour",
             "latest_departure_hour"],
            "harbor-master-plan",
        ),
    ],
}

INSTRUCTION = """You are {name} in an autonomous logistics operation.

{role}

Your job is to protect the operation using only the tools you have. Work in
this order: claim your work item, read the facts in your scope, report the
constraint only you can see, and if you have enough information, propose a
candidate plan.

Two things you must understand about this system:

- You cannot commit anything. `propose_plan` records a candidate. A
  deterministic verifier recomputes it from authoritative facts and decides.
  Confidence in your proposal has no effect on that decision, and any metrics
  you attach are recorded for the trace but ignored by the verifier.
- You cannot see outside your scope, and that is deliberate. If a decision
  needs facts you do not have, report your constraint and let the actor who
  owns those facts propose. Do not guess at another actor's constraint.

Never claim an outcome you have not achieved. Saying the container is safe
does not make it safe; only a verified, committed plan does that.
"""


def build_toolkits(store: Store, scenario: str,
                   fence: FenceArg) -> Dict[str, ActorToolkit]:
    """Build one bounded toolkit per actor for `scenario`.

    `fence` is required and has no default on purpose. These toolkits are the
    surface an actor mutates state through, so a caller building them during an
    event application must name the attempt they belong to; a caller outside one
    passes UNFENCED and says so. Defaulting here would put the silent unfenced
    path back one layer above the store, where it is harder to see.
    """
    if scenario not in ACTOR_SCOPES:
        raise KeyError(f"no actor scopes defined for scenario {scenario!r}")
    return {
        scope.name: ActorToolkit(store, scope.name, scope.work_ids,
                                 scope.visible_facts, scope.plan_prefix, fence)
        for scope in ACTOR_SCOPES[scenario]
    }


def scope_for(scenario: str, actor: str) -> ActorScope:
    """The one scope named `actor` in `scenario`."""
    for scope in ACTOR_SCOPES.get(scenario, ()):
        if scope.name == actor:
            return scope
    raise KeyError(
        f"no actor {actor!r} in scenario {scenario!r}; defined actors are "
        f"{[s.name for s in ACTOR_SCOPES.get(scenario, ())]}")


def _agent_for(scope: ActorScope, toolkit: ActorToolkit, model: str) -> Any:
    from google.adk.agents import LlmAgent
    return LlmAgent(
        name=scope.name.replace("-", "_"),
        model=model,
        description=scope.role,
        instruction=INSTRUCTION.format(name=scope.name, role=scope.role),
        tools=toolkit.as_tools(),
        # Peer transfer would let one actor delegate its way around its own
        # scope, which is the collapse hard gate 2 forbids.
        disallow_transfer_to_peers=True,
        disallow_transfer_to_parent=True,
    )


def build_actor_agent(store: Store, scenario: str, actor: str, fence: FenceArg,
                      model: str = DEFAULT_MODEL) -> Tuple[Any, ActorToolkit]:
    """One actor's agent and the toolkit it acts through, as a pair.

    `build_actor_agents` discards the toolkits, which is fine while nobody runs
    the agents: nothing needs to know what the actor did. A caller that actually
    executes the model does need it -- the toolkit is where the actor's reads and
    proposals land -- and reconstructing one afterwards would be a second object
    with a different observed revision, which is precisely the binding the
    toolkit exists to hold.

    `fence` is required for the same reason it is required on `build_toolkits`:
    this returns a surface that mutates authoritative state, and a caller that
    has not said how its writes are fenced has not finished thinking.
    """
    scope = scope_for(scenario, actor)
    toolkit = ActorToolkit(store, scope.name, scope.work_ids, scope.visible_facts,
                           scope.plan_prefix, fence)
    return _agent_for(scope, toolkit, model), toolkit


def build_actor_agents(store: Store, scenario: str, model: str = DEFAULT_MODEL) -> List[Any]:
    """One LlmAgent per bounded actor. No orchestrator, no committing agent."""
    toolkits = build_toolkits(store, scenario, UNFENCED)
    return [_agent_for(scope, toolkits[scope.name], model)
            for scope in ACTOR_SCOPES[scenario]]


def describe_actors(scenario: str) -> Dict[str, Any]:
    """Scope map, for the dashboard and for auditing gate 2 without an API call."""
    scopes = ACTOR_SCOPES[scenario]
    return {
        "scenario": scenario,
        "actors": [
            {"name": s.name, "work_ids": s.work_ids, "visible_facts": s.visible_facts,
             "tools": ActorToolkit(None, s.name, s.work_ids, s.visible_facts,
                                   s.plan_prefix, UNFENCED).tool_names()}
            for s in scopes
        ],
        "can_commit": [],
        "note": ("No actor has a commit tool. Verification is deterministic code "
                 "outside every agent's reach."),
    }
