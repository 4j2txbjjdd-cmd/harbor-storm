"""ADK actor boundaries.

Wrapping actors in ADK is only worth doing if the wrapper cannot widen what an
actor may do. These tests are the guard: an agent's strongest move must remain
"propose a candidate", and the scopes must stay genuinely disjoint.
"""
import pytest

from app.agents import ACTOR_SCOPES, ActorToolkit, ToolScopeError, describe_actors
from app.agents.actors import build_toolkits
from app.core.models import WorkItem
from app.core.store import UNFENCED
from app.core.store import InMemoryStateStore
from app.core.verify import verify_and_commit
from app.demo import route_fixture, weather_fixture
from app.scenarios import harborwindow, stormslot

SCENARIOS = ("stormslot", "harborwindow")


def _store(scenario):
    build = stormslot.build_state if scenario == "stormslot" else harborwindow.build_state
    return InMemoryStateStore(build())


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_actor_has_a_commit_or_verify_tool(scenario):
    """The whole architecture rests on this. An agent may never commit."""
    for name, kit in build_toolkits(_store(scenario), scenario, UNFENCED).items():
        names = kit.tool_names()
        assert not any(("commit" in t) or ("verify" in t) or ("revoke" in t)
                       for t in names), f"{name} exposes {names}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_actor_scopes_are_disjoint(scenario):
    """Two actors with identical scope are one actor. Hard gate 2."""
    scopes = ACTOR_SCOPES[scenario]
    assert len(scopes) >= 2
    work = [frozenset(s.work_ids) for s in scopes]
    facts = [frozenset(s.visible_facts) for s in scopes]
    assert len(set(work)) == len(work), "work scopes overlap"
    assert len(set(facts)) == len(facts), "fact scopes are identical"
    for a in range(len(scopes)):
        for b in range(a + 1, len(scopes)):
            assert not (work[a] & work[b]), f"{scopes[a].name}/{scopes[b].name} share work"


def test_actor_cannot_claim_outside_its_scope():
    store = _store("stormslot")
    store.create_work(WorkItem("route", "find departure"), fence=UNFENCED)
    store.create_work(WorkItem("slot", "protect pickup"), fence=UNFENCED)
    kits = build_toolkits(store, "stormslot", UNFENCED)
    assert kits["transport-agent"].claim_work("route")["claimed"] is True
    with pytest.raises(ToolScopeError, match="may not act on work item"):
        kits["transport-agent"].claim_work("slot")


# Site identifiers are a shared vocabulary, not shared information: the
# transport agent routes *to* the warehouse and the warehouse agent *is* it.
# Both must name the same place. What must not be shared is any fact that
# constrains a decision.
LOCATION_KEYS = {"port", "warehouse", "harbor", "island"}


def test_actor_reads_only_its_own_facts():
    store = _store("stormslot")
    kits = build_toolkits(store, "stormslot", UNFENCED)
    transport = kits["transport-agent"].read_facts()
    warehouse = kits["warehouse-agent"].read_facts()
    assert "warehouse_shift_change_hour" not in transport
    assert "warehouse_close_hour" not in transport
    assert "truck_available_hour" not in warehouse
    assert "pickup_deadline_hour" not in warehouse
    shared = (set(transport) & set(warehouse)) - LOCATION_KEYS
    assert shared == set(), f"actors share decision-relevant facts: {shared}"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_no_two_actors_share_a_constraint_fact(scenario):
    """Every actor must hold at least one fact no other actor can see."""
    scopes = ACTOR_SCOPES[scenario]
    for s in scopes:
        others = set().union(*(set(o.visible_facts) for o in scopes if o is not s))
        exclusive = (set(s.visible_facts) - others) - LOCATION_KEYS
        assert exclusive, f"{s.name} sees nothing the others do not"


def test_missing_scoped_fact_refuses_a_partial_world():
    store = _store("stormslot")
    kit = ActorToolkit(store, "ghost-agent", [], ["a_fact_that_does_not_exist"], "x", fence=UNFENCED)
    with pytest.raises(ToolScopeError, match="refusing to return a partial world"):
        kit.read_facts()


def test_proposing_does_not_commit():
    store = _store("stormslot")
    kit = build_toolkits(store, "stormslot", UNFENCED)["transport-agent"]
    kit.read_facts()          # a proposal binds to the world the actor observed
    result = kit.propose_plan(
        actions=[{"type": "dispatch_truck", "hour": 16}],
        metrics={"confidence": 1.0, "self_reported_status": 1.0},
    )
    assert result["status"] == "candidate"
    assert store.state.committed_plan_id is None
    assert store.get_plan(result["plan_id"]).verified is False


def test_an_agent_proposal_faces_the_same_verifier():
    """An LLM-authored candidate is refused exactly like a coded one."""
    store = _store("stormslot")
    kit = build_toolkits(store, "stormslot", UNFENCED)["transport-agent"]
    kit.read_facts()          # a proposal binds to the world the actor observed
    verifier = stormslot.make_verifier(store, weather_fixture().hourly("PORT_A"),
                                       route_fixture())

    bad = kit.propose_plan([{"type": "dispatch_truck", "hour": 16}],
                           {"confidence": 0.99})
    assert verify_and_commit(store, bad["plan_id"], verifier, fence=UNFENCED) is False
    assert "severe weather" in store.get_plan(bad["plan_id"]).rejection_reason

    good = kit.propose_plan([{"type": "dispatch_truck", "hour": 14}], {})
    assert verify_and_commit(store, good["plan_id"], verifier, fence=UNFENCED) is True
    assert store.state.committed_plan_id == good["plan_id"]


def test_report_constraint_lands_on_the_trace():
    store = _store("harborwindow")
    kit = build_toolkits(store, "harborwindow", UNFENCED)["cargo-agent"]
    kit.report_constraint({"cargo_kg": 320, "boat_capacity_kg": 500})
    reported = [e for e in store.events if e.kind == "CONSTRAINT_REPORTED"]
    assert len(reported) == 1 and reported[0].actor == "cargo-agent"


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_describe_actors_reports_nobody_can_commit(scenario):
    d = describe_actors(scenario)
    assert d["can_commit"] == []
    assert len(d["actors"]) >= 2


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_agents_cannot_delegate_around_their_scope(scenario):
    """Peer transfer would let an actor route past its own boundary."""
    agents = __import__("app.agents", fromlist=["build_actor_agents"]) \
        .build_actor_agents(_store(scenario), scenario)
    assert agents, "no agents built"
    for a in agents:
        assert a.disallow_transfer_to_peers is True
        assert a.disallow_transfer_to_parent is True
        assert not any("commit" in t.name for t in a.tools)
