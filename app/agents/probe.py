"""Real-model probe: one bounded actor, executed on Gemini, judged by Harbor.

    .venv/bin/python -m app.agents.probe
    .venv/bin/python -m app.agents.probe --model gemini-3.5-flash --json

Everything else in this repo can be true of a system that never called a model.
This probe exists to close that one gap and nothing else. It runs the existing
HarborWindow `window-agent` -- the same scope, the same `ActorToolkit`, the same
five tools -- against a real Gemini model, and then hands whatever the model
produced to `app.core.verify.verify_and_commit`, which is ordinary deterministic
code the model cannot reach.

Two cases run, and the second is the one that matters:

  accept   The world is the seeded baseline. The model reads its scope, reads
           the trace for what the other actors published, and proposes. If its
           candidate survives independent recomputation, Harbor commits it.

  refuse   Identical up to the proposal. Then real external truth arrives and
           the world moves to a new revision -- exactly the disruption this
           system is built around. The model's candidate is now bound to a world
           that no longer exists, and Harbor refuses it. The proposal is not
           refused for being wrong; it is refused because the model does not get
           to decide when its reasoning is still true. Authoritative state is
           unchanged, and the trace says so.

The probe never writes authoritative state on the model's behalf. Every claim,
constraint and proposal on the trace under `window-agent` was produced by a tool
call the model itself made.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.agents.actors import DEFAULT_MODEL, build_actor_agent
from app.agents.execution import ActorRun, require_model_floor, run_actor
from app.config import Settings, make_store
from app.core.store import UNFENCED
from app.core.verify import verify_and_commit
from app.demo import disrupted_weather_fixture, weather_fixture
from app.scenarios import harborwindow

SCENARIO = "harborwindow"
ACTOR = "window-agent"
WORK_ID = "window"

# Every actor except the one under test is claimed for by the seeding, as the
# deterministic path does. `window` is deliberately left OPEN: the model has to
# claim it through `claim_work`, and that tool call is part of the evidence.
SEEDED_ACTORS = tuple(pair for pair in harborwindow.ACTOR_WORK
                      if pair[0] != WORK_ID)

# The work order. It names the job and the shape of a candidate plan for this
# operation; it contains no weather, no limits, and no hour. The departure hour
# has to come out of what the model reads through its own tools, or the run
# proves nothing.
BRIEFING = """Shift start. Work item "window" is open and unclaimed.

Do your job in this order:

  1. claim_work("window")
  2. read_facts()  -- your marine safety limits and the crossing duration
  3. read_trace()  -- the measured forecast, and the constraints the cargo and
     harbormaster actors have already published. You cannot read their facts
     directly; the trace is the only channel between scopes.
  4. report_constraint(...) -- the marine limit only you can see
  5. propose_plan(...) -- your candidate

A candidate plan for this operation is a list of actions in this shape:

  {"type": "reserve_boat", "departure_hour": <int hour>}
  {"type": "load_cargo", "kg": <int>}
  {"type": "depart", "hour": <int hour>}

Which hour is correct is yours to work out from what you read. Nothing in this
briefing tells you, and nothing in it implies an answer.

Propose your best candidate even if you are not certain it will pass. You are
not the verifier, and your confidence is not evidence.
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plan_view(store, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not plan_id:
        return None
    p = store.get_plan(plan_id)
    return {"id": p.id, "created_by": p.created_by, "actions": p.actions,
            "metrics": p.metrics, "verified": p.verified,
            "rejection_reason": p.rejection_reason,
            "basis_revision": p.basis_revision}


def _trace_view(store) -> List[Dict[str, Any]]:
    keep = ("plan_id", "work_id", "reason", "revision", "severe_hours",
            "harbor_hours", "state_revision", "plan_revision", "event_id")
    out = []
    for e in store.trace():
        detail = {k: v for k, v in (e.get("payload") or {}).items() if k in keep}
        out.append({"seq": e["seq"], "kind": e["kind"], "actor": e["actor"],
                    "detail": detail})
    return out


def _execute_actor(store, model: str) -> ActorRun:
    """Seed the world, then let the real model be `window-agent` in it."""
    seeded = harborwindow.seed(store, weather_fixture(), UNFENCED,
                               claim_for=SEEDED_ACTORS)
    if seeded is None:
        raise RuntimeError("seeding aborted: a work item could not be claimed")
    agent, _toolkit = build_actor_agent(store, SCENARIO, ACTOR, UNFENCED, model)
    return run_actor(agent, BRIEFING)


def _case_result(case: str, store, run: ActorRun, plan_id: Optional[str],
                 committed: Optional[bool], expected: str,
                 extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    verdict = ("no proposal" if plan_id is None
               else "accepted" if committed else "rejected")
    result = {
        "case": case,
        "expected": expected,
        "verifier_decision": verdict,
        "as_expected": verdict == expected,
        "actor_run": run.as_dict(),
        "proposed_plan": _plan_view(store, plan_id),
        "authoritative_state": {
            "committed_plan_id": store.state.committed_plan_id,
            "revision": store.state.revision,
            "work_window_claimed_by": store.state.work[WORK_ID].claimed_by,
        },
        "trace": _trace_view(store),
    }
    if extra:
        result.update(extra)
    return result


def case_accept(settings: Settings, model: str, run_id: str) -> Dict[str, Any]:
    """The model proposes into an undisturbed world. Harbor decides."""
    store = make_store(settings, run_id, SCENARIO)
    run = _execute_actor(store, model)
    plan_id = run.plan_ids[-1] if run.plan_ids else None
    committed = None
    if plan_id:
        harbor = weather_fixture().hourly(store.state.facts["harbor"])
        island = weather_fixture().hourly(store.state.facts["island"])
        verifier = harborwindow.make_verifier(store, harbor, island)
        committed = verify_and_commit(store, plan_id, verifier, UNFENCED)
    return _case_result("accept", store, run, plan_id, committed, "accepted")


def case_refuse(settings: Settings, model: str, run_id: str) -> Dict[str, Any]:
    """Negative control. Real model output, and Harbor still refuses it.

    New marine truth lands between the proposal and the verification, through
    the same `advance_revision` path a Pub/Sub weather event uses. The candidate
    was bound to the previous revision, so it can no longer verify -- whatever
    it says, and however well the model reasoned to it.

    The content verdict is reported alongside, purely as evidence: under the new
    forecast the plan is not merely out of date, it is unsafe. That verdict
    changes nothing here. The refusal already happened, on staleness, before any
    feasibility question was asked.
    """
    store = make_store(settings, run_id, SCENARIO)
    run = _execute_actor(store, model)
    plan_id = run.plan_ids[-1] if run.plan_ids else None
    if plan_id is None:
        return _case_result("refuse", store, run, None, None, "rejected")

    facts = store.state.facts
    bound_revision = store.get_plan(plan_id).basis_revision

    disrupted = disrupted_weather_fixture()
    d_harbor = disrupted.hourly(facts["harbor"])
    d_island = disrupted.hourly(facts["island"])

    event_id = f"{run_id}-marine-update"
    lease = store.advance_revision(
        "weather-agent", "marine forecast updated",
        {"harbor": facts["harbor"], "island": facts["island"]}, event_id=event_id)
    if lease is None:
        raise RuntimeError(f"could not take the lease for event {event_id!r}")
    fence = lease.fence
    store.emit("MARINE_WEATHER_UPDATED", "weather-agent",
               {"harbor": facts["harbor"], "island": facts["island"]})

    verifier = harborwindow.make_verifier(store, d_harbor, d_island)
    committed = verify_and_commit(store, plan_id, verifier, fence)
    store.complete_event(event_id, "weather-agent", fence)

    # Pure function, no state change: what the verifier would have said about
    # the plan's content had staleness not already ended the question.
    content_ok, content_reason = verifier(store.get_plan(plan_id))
    return _case_result(
        "refuse", store, run, plan_id, committed, "rejected",
        extra={"control": {
            "mechanism": "external truth advanced the world after the proposal",
            "plan_bound_to_revision": bound_revision,
            "world_revision_at_verification": store.state.revision,
            "content_verdict_under_new_forecast": {
                "feasible": content_ok, "reason": content_reason},
        }})


CASES = {"accept": case_accept, "refuse": case_refuse}


def new_run_prefix() -> str:
    """A fresh id per probe. On Firestore a run id is a document: reusing one
    resumes that world rather than seeding a new one, so a second probe under
    the same id would plan on top of the first probe's commit."""
    return f"gemini-probe-{uuid.uuid4().hex[:8]}"


def probe(model: str, cases: List[str], settings: Optional[Settings] = None,
          run_prefix: Optional[str] = None) -> Dict[str, Any]:
    settings = settings or Settings.from_env()
    require_model_floor(model)
    run_prefix = run_prefix or new_run_prefix()
    results = [CASES[c](settings, model, f"{run_prefix}-{c}") for c in cases]
    return {
        "probe": "harbor-gemini-execution",
        "started_at": _now(),
        "run_prefix": run_prefix,
        "scenario": SCENARIO,
        "actor": ACTOR,
        "requested_model": model,
        "state_backend": settings.state_backend,
        "briefing": BRIEFING,
        "cases": results,
        "all_as_expected": all(r["as_expected"] for r in results),
    }


def render(report: Dict[str, Any]) -> str:
    lines = [f"harbor gemini execution probe  —  {report['started_at']}",
             f"  scenario {report['scenario']}   actor {report['actor']}",
             f"  model    {report['requested_model']}   backend {report['state_backend']}",
             f"  run      {report['run_prefix']}",
             ""]
    for r in report["cases"]:
        run = r["actor_run"]
        mark = "OK " if r["as_expected"] else "!! "
        lines.append(f"{mark}case {r['case']}: verifier {r['verifier_decision']} "
                     f"(expected {r['expected']})")
        lines.append(f"      model reported: {', '.join(run['model_versions_reported']) or '-'}"
                     f"   turns: {len(run['turns'])}")
        lines.append(f"      tools offered:  {', '.join(run['tools_offered'])}")
        lines.append(f"      tools called:   "
                     f"{', '.join(c['name'] for c in run['tool_calls']) or '-'}")
        plan = r["proposed_plan"]
        if plan:
            lines.append(f"      candidate {plan['id']} by {plan['created_by']} "
                         f"@revision {plan['basis_revision']}: {json.dumps(plan['actions'])}")
            if plan["rejection_reason"]:
                lines.append(f"      rejected: {plan['rejection_reason']}")
        ctrl = r.get("control")
        if ctrl:
            v = ctrl["content_verdict_under_new_forecast"]
            lines.append(f"      control: bound to r{ctrl['plan_bound_to_revision']}, "
                         f"world at r{ctrl['world_revision_at_verification']}; "
                         f"content under new forecast: {v['reason']}")
        st = r["authoritative_state"]
        lines.append(f"      authoritative: committed_plan_id="
                     f"{st['committed_plan_id']}  revision={st['revision']}  "
                     f"window claimed by {st['work_window_claimed_by']}")
        lines.append("")
    lines.append("ALL AS EXPECTED" if report["all_as_expected"]
                 else "NOT AS EXPECTED — read the cases above")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"Gemini model id (default {DEFAULT_MODEL}); must be 3.5 or newer")
    ap.add_argument("--case", choices=(*CASES, "both"), default="both")
    ap.add_argument("--json", action="store_true", help="full evidence to stdout")
    ap.add_argument("--out", help="write the full evidence JSON to this path")
    ap.add_argument("--run-prefix", default=None,
                    help="pin the run id (default: a fresh one per probe)")
    a = ap.parse_args(argv)

    cases = list(CASES) if a.case == "both" else [a.case]
    report = probe(a.model, cases, run_prefix=a.run_prefix)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str) if a.json else render(report))
    return 0 if report["all_as_expected"] else 1


if __name__ == "__main__":
    sys.exit(main())
