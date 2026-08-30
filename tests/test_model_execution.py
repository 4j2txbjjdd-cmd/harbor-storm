"""The execution seam, tested without a model.

Read the distinction here before adding to this file. These tests prove that
`app.agents.execution` wires a bounded actor to *a* model correctly, and that
its two fail-closed guards fail closed. They do **not** prove that Harbor runs a
real Gemini model, and no test in this file ever could -- a test double that
answers instantly is exactly what such a proof must exclude.

That proof is `app.agents.probe`, which calls the live API and is run by hand
against a committed SHA. A green suite here plus a red probe means the wiring is
right and the claim is still false.
"""
import asyncio
from typing import AsyncGenerator

import pytest

from app.agents.actors import build_actor_agent
from app.agents.execution import (AuthorityLeakError, CallbackConflictError,
                                  ModelFloorError,
                                  NoModelCallError, agent_model_id,
                                  assert_no_authority_tools, declared_tool_names,
                                  model_series, require_model_floor, run_actor)
from app.agents.probe import BRIEFING, SEEDED_ACTORS, WORK_ID
from app.core.store import UNFENCED, InMemoryStateStore
from app.core.verify import verify_and_commit
from app.demo import weather_fixture
from app.scenarios import harborwindow

STUB_MODEL = "gemini-3.5-flash-test-double"


# --- the model floor -------------------------------------------------

@pytest.mark.parametrize("model,expected", [
    ("gemini-3.5-flash", (3, 5)),
    ("gemini-3.7-flash", (3, 7)),
    ("gemini-3.1-pro-preview", (3, 1)),
    ("gemini-3-flash-preview", (3, 0)),
    ("gemini-2.5-flash", (2, 5)),
    ("publishers/google/models/gemini-3.6-flash", (3, 6)),
    ("gpt-4o", None),
    ("", None),
])
def test_model_series_parsing(model, expected):
    assert model_series(model) == expected


@pytest.mark.parametrize("model", ["gemini-3.5-flash", "gemini-3.5-flash-lite",
                                   "gemini-3.6-flash", "gemini-3.7-flash"])
def test_floor_admits_gemini_35_and_newer(model):
    assert require_model_floor(model) == model


@pytest.mark.parametrize("model", [
    "gemini-2.5-flash",       # the previous default; the exact silent downgrade
    "gemini-2.0-flash-001",
    "gemini-1.5-pro-002",
    "gemini-3-flash-preview",  # reads as 3, which is 3.0, which is below 3.5
])
def test_floor_refuses_anything_below_35(model):
    """Negative control for the gate. A run on these must not be reportable."""
    with pytest.raises(ModelFloorError, match="below the required floor"):
        require_model_floor(model)


@pytest.mark.parametrize("model", ["gpt-4o", "some-internal-alias", ""])
def test_floor_refuses_a_model_it_cannot_place(model):
    """Unrecognised is refused, not assumed new enough."""
    with pytest.raises(ModelFloorError, match="cannot establish the model series"):
        require_model_floor(model)


def test_agent_model_id_sees_through_a_model_object():
    class Holder:
        model = type("M", (), {"model": "gemini-3.5-flash"})()
    assert agent_model_id(Holder()) == "gemini-3.5-flash"


# --- the authority guard ---------------------------------------------

def test_authority_guard_passes_the_real_actor_tool_surface():
    assert_no_authority_tools(["claim_work", "read_facts", "report_constraint",
                               "propose_plan", "read_trace"])


@pytest.mark.parametrize("leaked", ["commit_plan", "verify_and_commit",
                                    "revoke_commit", "mark_verified",
                                    "rebind_plan", "advance_revision"])
def test_authority_guard_refuses_a_leaked_tool(leaked):
    with pytest.raises(AuthorityLeakError, match="authoritative state"):
        assert_no_authority_tools(["read_facts", leaked])


def test_declared_tool_names_reads_the_outgoing_request():
    """The guard must audit the request, not this repo's own tool_names()."""
    class Decl:
        def __init__(self, name): self.name = name

    class Tool:
        function_declarations = [Decl("claim_work"), Decl("propose_plan")]

    class Config:
        tools = [Tool()]

    class Request:
        config = Config()

    assert declared_tool_names(Request()) == ["claim_work", "propose_plan"]


# --- the adapter, driven by a test double ----------------------------

class _StubLlm:
    """A BaseLlm that answers from a script. Never reaches the network."""

    def __new__(cls, script):
        from google.adk.models import BaseLlm

        class Impl(BaseLlm):
            def __init__(self, script):
                super().__init__(model=STUB_MODEL)
                object.__setattr__(self, "_script", list(script))

            async def generate_content_async(self, llm_request, stream=False
                                             ) -> AsyncGenerator:
                from google.adk.models import LlmResponse
                from google.genai import types
                # Prove the guard sees a real request: this is what ADK built.
                assert_no_authority_tools(declared_tool_names(llm_request))
                parts = self._script.pop(0) if self._script else [
                    types.Part(text="done")]
                yield LlmResponse(
                    content=types.Content(role="model", parts=parts),
                    model_version=STUB_MODEL)

        return Impl(script)


def _call(name, args):
    from google.genai import types
    return [types.Part(function_call=types.FunctionCall(name=name, args=args))]


def _seeded_store():
    store = InMemoryStateStore(harborwindow.build_state())
    seeded = harborwindow.seed(store, weather_fixture(), UNFENCED,
                               claim_for=SEEDED_ACTORS)
    assert seeded is not None
    return store, seeded


def test_adapter_carries_a_scripted_actor_through_to_a_commit():
    """The whole seam, with the model replaced by a script.

    Hour 14 is the one departure the seeded forecast leaves safe, so a script
    that proposes it must commit -- through the same verifier, from the same
    toolkit, with no shortcut for the fact that a double produced it.
    """
    from google.genai import types
    store, (harbor, island) = _seeded_store()
    script = [
        _call("claim_work", {"work_id": WORK_ID}),
        _call("read_facts", {}),
        _call("propose_plan", {"actions": [{"type": "depart", "hour": 14}]}),
        [types.Part(text="proposed hour 14")],
    ]
    agent, toolkit = build_actor_agent(store, "harborwindow", "window-agent",
                                       UNFENCED, _StubLlm(script))
    run = run_actor(agent, BRIEFING, session_id="stub-1")

    assert [c["name"] for c in run.tool_calls] == [
        "claim_work", "read_facts", "propose_plan"]
    assert run.plan_ids, "the adapter did not recover the proposed plan id"
    assert store.state.work[WORK_ID].claimed_by == "window-agent"

    plan_id = run.plan_ids[-1]
    verifier = harborwindow.make_verifier(store, harbor, island)
    assert verify_and_commit(store, plan_id, verifier, UNFENCED) is True
    assert store.state.committed_plan_id == plan_id


def test_adapter_records_the_tool_surface_the_model_was_offered():
    from google.genai import types
    store, _ = _seeded_store()
    agent, _ = build_actor_agent(store, "harborwindow", "window-agent", UNFENCED,
                                 _StubLlm([[types.Part(text="nothing to do")]]))
    run = run_actor(agent, BRIEFING, session_id="stub-2")
    assert run.tools_offered == ["claim_work", "read_facts", "report_constraint",
                                 "propose_plan", "read_trace"]
    assert not any("commit" in t or "verify" in t for t in run.tools_offered)
    assert run.model_versions == [STUB_MODEL]


def test_a_scripted_actor_still_cannot_commit_its_own_plan():
    """The double is given the strongest move it has, and it is not enough."""
    from google.genai import types
    store, _ = _seeded_store()
    script = [
        _call("read_facts", {}),
        _call("propose_plan", {"actions": [{"type": "depart", "hour": 12}]}),
        [types.Part(text="please commit this")],
    ]
    agent, _ = build_actor_agent(store, "harborwindow", "window-agent", UNFENCED,
                                 _StubLlm(script))
    run = run_actor(agent, BRIEFING, session_id="stub-3")
    assert run.plan_ids
    assert store.state.committed_plan_id is None
    assert store.get_plan(run.plan_ids[-1]).verified is False


def test_a_run_with_no_model_turn_is_not_a_result():
    """A run that never reached a model must raise, not return a quiet success.

    This is the guard that stops "the probe ran and nothing broke" from being
    mistaken for "a model answered".
    """
    from app.agents import execution

    async def _run():
        agent, _ = build_actor_agent(
            InMemoryStateStore(harborwindow.build_state()),
            "harborwindow", "window-agent", UNFENCED, "gemini-3.5-flash")

        async def _empty(*a, **k):
            if False:
                yield None

        class FakeRunner:
            def __init__(self, **kw): pass
            def run_async(self, **kw): return _empty()
            async def close(self): pass

        import google.adk.runners as runners
        real = runners.Runner
        runners.Runner = FakeRunner
        try:
            await execution.run_actor_async(agent, "hello", session_id="stub-4")
        finally:
            runners.Runner = real

    with pytest.raises(NoModelCallError, match="no recorded model turn"):
        asyncio.run(_run())


def test_an_agent_that_already_has_callbacks_is_refused():
    """The authority check rides on the before-model hook. Displacing an
    existing observer would take the check with it, silently."""
    from google.genai import types
    store, _ = _seeded_store()
    agent, _ = build_actor_agent(store, "harborwindow", "window-agent", UNFENCED,
                                 _StubLlm([[types.Part(text="hi")]]))
    agent.before_model_callback = lambda ctx, req: None
    with pytest.raises(CallbackConflictError, match="already carries model callbacks"):
        run_actor(agent, BRIEFING, session_id="stub-5")


def test_the_recorder_is_removed_after_a_run():
    from google.genai import types
    store, _ = _seeded_store()
    agent, _ = build_actor_agent(store, "harborwindow", "window-agent", UNFENCED,
                                 _StubLlm([[types.Part(text="hi")]]))
    run_actor(agent, BRIEFING, session_id="stub-6")
    assert agent.before_model_callback is None
    assert agent.after_model_callback is None
