"""The seam where a bounded Harbor actor actually executes on a real model.

Everything else in `app.agents` describes an actor: its scope, its tools, what
it may not do. Nothing in it ever ran a model, so "no actor can commit" was a
statement about a shape rather than about an observed execution. This module is
the missing step, and it is deliberately the *only* new step: it takes an
`LlmAgent` that `build_actor_agent` already built around an existing
`ActorToolkit`, runs it through the ADK `Runner` against a real Gemini model,
and records what happened. It creates no tools, widens no scope, and touches no
authoritative state itself -- every write in a run of this module goes through
the same `ActorToolkit` methods the deterministic path uses.

Two things here fail closed, because both are the kind of weakness that is
otherwise caught only by someone remembering to look:

* `require_model_floor` refuses a model below the competition's Gemini 3.5
  floor. An older model that quietly answers is worse than an error: the run
  succeeds and the claim made about it is false.
* `assert_no_authority_tools` is checked against the function declarations in
  the actual `LlmRequest`, at the moment it is sent. Auditing the toolkit's own
  `tool_names()` would only re-check what `app.agents.tools` already promises;
  auditing the request checks what the model was really offered.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# The competition requires Gemini 3.5 or newer. Expressed as a comparable
# (major, minor) rather than a name list so a later model passes without an
# edit, and a `gemini-3-flash-preview` -- which reads as "3" and is therefore
# 3.0 -- does not.
MIN_MODEL_SERIES: Tuple[int, int] = (3, 5)

# Substrings that would mean the membrane had been handed to the model. This is
# checked against declarations in the outgoing request, not against a list this
# repo maintains about itself.
AUTHORITY_TOOL_MARKERS = ("commit", "verify", "revoke", "rebind",
                          "advance_revision", "mark_verified", "reject_plan")

_SERIES_RE = re.compile(r"(?:^|/)gemini-(\d+)(?:[.-](\d+))?", re.IGNORECASE)


class ModelFloorError(RuntimeError):
    """The model named is older than the floor this system claims to run on."""


class AuthorityLeakError(RuntimeError):
    """A tool that can move authoritative state was offered to a model."""


class CallbackConflictError(RuntimeError):
    """The agent already carries model callbacks the recorder would displace."""


class NoModelCallError(RuntimeError):
    """A run finished without any evidence that a model was actually called.

    Raised rather than returned, because the entire value of this module is the
    evidence. A run with no recorded model turn proves nothing and must not be
    mistaken for a quiet success.
    """


def model_series(model: str) -> Optional[Tuple[int, int]]:
    """(major, minor) for a Gemini model id, or None if it is not one.

    `gemini-3-flash-preview` yields (3, 0): an unnumbered minor is zero, not a
    wildcard. Treating it as "at least 3.anything" is how a 3.0 model would get
    through a 3.5 floor.
    """
    m = _SERIES_RE.search(model or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def require_model_floor(model: str, floor: Tuple[int, int] = MIN_MODEL_SERIES) -> str:
    """Return `model`, or refuse it for being below `floor`.

    An unrecognisable model id is refused too. The alternative is to assume a
    name this code cannot parse is new enough, which is the assumption most
    likely to be wrong at exactly the moment it matters.
    """
    series = model_series(model)
    if series is None:
        raise ModelFloorError(
            f"cannot establish the model series of {model!r}. This path claims "
            f"to run Gemini {floor[0]}.{floor[1]}+ and will not run a model it "
            f"cannot place against that floor.")
    if series < floor:
        raise ModelFloorError(
            f"{model!r} is Gemini {series[0]}.{series[1]}, below the required "
            f"floor of {floor[0]}.{floor[1]}. Refusing to run: a result from an "
            f"older model would be reported under a claim that is not true of it.")
    return model


def agent_model_id(agent: Any) -> str:
    """The model id an agent will actually call.

    `LlmAgent.model` is either a name or a `BaseLlm` that carries one. Reading
    `str(agent.model)` works for the first and yields a repr for the second,
    which the floor check would then refuse for being unparseable -- correct by
    accident, and wrong the moment a registry hands back a configured BaseLlm.
    """
    model = getattr(agent, "model", "")
    return str(getattr(model, "model", model) or "")


def declared_tool_names(llm_request: Any) -> List[str]:
    """Every function name declared in an outgoing model request."""
    names: List[str] = []
    config = getattr(llm_request, "config", None)
    for tool in (getattr(config, "tools", None) or []):
        for decl in (getattr(tool, "function_declarations", None) or []):
            name = getattr(decl, "name", None)
            if name:
                names.append(name)
    return names


def assert_no_authority_tools(names: List[str]) -> None:
    """Refuse to send a request that offers the model a way past the membrane."""
    leaked = sorted({n for n in names
                     if any(m in n.lower() for m in AUTHORITY_TOOL_MARKERS)})
    if leaked:
        raise AuthorityLeakError(
            f"refusing to call the model: {leaked} would let it move "
            f"authoritative state. An actor's strongest move is propose_plan.")


@dataclass(frozen=True)
class ModelTurn:
    """One response from the model, as the server described it."""

    model_version: Optional[str] = None
    finish_reason: Optional[str] = None
    interaction_id: Optional[str] = None
    prompt_tokens: Optional[int] = None
    candidate_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"model_version": self.model_version,
                "finish_reason": self.finish_reason,
                "interaction_id": self.interaction_id,
                "prompt_tokens": self.prompt_tokens,
                "candidate_tokens": self.candidate_tokens,
                "total_tokens": self.total_tokens}


@dataclass
class ActorRun:
    """What one bounded actor did on one real model invocation.

    `plan_ids` is read back out of the model's own `propose_plan` tool results,
    not out of the store: the point is which candidates *this actor* put in
    front of the verifier, and reading the store would also pick up candidates
    written by the deterministic path.
    """

    actor: str
    requested_model: str
    requested_model_in_request: Optional[str] = None
    tools_offered: List[str] = field(default_factory=list)
    turns: List[ModelTurn] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    final_text: str = ""
    plan_ids: List[str] = field(default_factory=list)

    @property
    def model_versions(self) -> List[str]:
        return sorted({t.model_version for t in self.turns if t.model_version})

    def as_dict(self) -> Dict[str, Any]:
        return {"actor": self.actor,
                "requested_model": self.requested_model,
                "model_in_request": self.requested_model_in_request,
                "model_versions_reported": self.model_versions,
                "tools_offered": self.tools_offered,
                "turns": [t.as_dict() for t in self.turns],
                "tool_calls": self.tool_calls,
                "tool_results": self.tool_results,
                "final_text": self.final_text,
                "plan_ids": self.plan_ids}


def _record_callbacks(run: ActorRun):
    """Recording-only hooks. They observe the request and response; they never
    alter either, and returning None is what tells ADK to proceed unchanged.

    The authority check lives in the *before* hook on purpose. Checking after
    the call would report a leak that had already happened.
    """

    def before_model(callback_context: Any, llm_request: Any):
        names = declared_tool_names(llm_request)
        assert_no_authority_tools(names)
        if not run.tools_offered:
            run.tools_offered = names
            run.requested_model_in_request = getattr(llm_request, "model", None)
        return None

    def after_model(callback_context: Any, llm_response: Any):
        usage = getattr(llm_response, "usage_metadata", None)
        finish = getattr(llm_response, "finish_reason", None)
        run.turns.append(ModelTurn(
            model_version=getattr(llm_response, "model_version", None),
            finish_reason=(getattr(finish, "name", None) or
                           (str(finish) if finish is not None else None)),
            interaction_id=getattr(llm_response, "interaction_id", None),
            prompt_tokens=getattr(usage, "prompt_token_count", None),
            candidate_tokens=getattr(usage, "candidates_token_count", None),
            total_tokens=getattr(usage, "total_token_count", None),
        ))
        return None

    return before_model, after_model


async def run_actor_async(agent: Any, briefing: str, *,
                          app_name: str = "harbor-probe",
                          user_id: str = "harbor-ops",
                          session_id: str = "shift-1") -> ActorRun:
    """Execute one bounded actor against its real model and record the run.

    `agent` is whatever `build_actor_agent` produced. Recording callbacks are
    installed on it for the duration of the run and removed afterwards. An agent
    that already carries model callbacks is refused rather than overwritten:
    silently dropping someone else's observer would take the authority check
    with it, and a guard that can be displaced without a sound is not a guard.
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    model_id = agent_model_id(agent)
    require_model_floor(model_id)
    if agent.before_model_callback is not None or agent.after_model_callback is not None:
        raise CallbackConflictError(
            f"{agent.name} already carries model callbacks. Installing the "
            f"recorder would displace them, and the authority check rides on "
            f"the before-model hook.")

    run = ActorRun(actor=agent.name, requested_model=model_id)
    before_model, after_model = _record_callbacks(run)
    agent.before_model_callback = before_model
    agent.after_model_callback = after_model

    session_service = InMemorySessionService()
    await session_service.create_session(app_name=app_name, user_id=user_id,
                                         session_id=session_id)
    runner = Runner(app_name=app_name, agent=agent, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=briefing)])
    try:
        async for event in runner.run_async(user_id=user_id, session_id=session_id,
                                            new_message=message):
            for call in (event.get_function_calls() or []):
                run.tool_calls.append({"name": call.name, "args": dict(call.args or {})})
            for resp in (event.get_function_responses() or []):
                payload = resp.response
                run.tool_results.append({"name": resp.name, "response": payload})
                if resp.name == "propose_plan" and isinstance(payload, dict):
                    # ADK wraps a non-dict return; a dict return arrives as-is.
                    inner = payload.get("result", payload)
                    pid = inner.get("plan_id") if isinstance(inner, dict) else None
                    if pid:
                        run.plan_ids.append(pid)
            if event.is_final_response() and event.content and event.content.parts:
                text = "".join(p.text or "" for p in event.content.parts)
                if text:
                    run.final_text = text
    finally:
        await runner.close()
        agent.before_model_callback = None
        agent.after_model_callback = None

    if not run.turns:
        raise NoModelCallError(
            f"{agent.name} produced no recorded model turn. Nothing here is "
            f"evidence of a real invocation, so this run is not a result.")
    return run


def run_actor(agent: Any, briefing: str, **kwargs: Any) -> ActorRun:
    """Blocking wrapper, for the CLI probe and for tests."""
    return asyncio.run(run_actor_async(agent, briefing, **kwargs))
