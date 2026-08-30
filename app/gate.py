"""Decision-gate harness.

Six hard gates a scenario must survive. Five of the six are mechanically
checkable, so they are checked here rather than asserted in prose. The sixth,
demo legibility, stays human judgement; the other five do not.

    .venv/bin/python -m app.gate            # table
    .venv/bin/python -m app.gate --json     # machine-readable

A scenario that fails any hard gate is not scored. It is fixed or dropped.
"""
from __future__ import annotations
import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from app.core.models import CandidatePlan
from app.core.store import UNFENCED, InMemoryStateStore
from app.demo import (disrupted_weather_fixture, route_fixture, weather_fixture)
from app.providers.weather import MockWeatherProvider, WeatherPoint
from app.scenarios import harborwindow, stormslot

SCENARIOS = ("stormslot", "harborwindow")


@dataclass
class GateResult:
    gate: str
    passed: Optional[bool]      # None = not mechanically checkable
    evidence: str

    @property
    def mark(self) -> str:
        return {True: "PASS", False: "FAIL", None: "manual"}[self.passed]


def _fresh(scenario: str):
    build = stormslot.build_state if scenario == "stormslot" else harborwindow.build_state
    return InMemoryStateStore(build())


def _run(scenario: str, store, weather):
    if scenario == "stormslot":
        return stormslot.run(store, weather, route_fixture())
    return harborwindow.run(store, weather)


def _disrupt(scenario: str, store, weather):
    if scenario == "stormslot":
        return stormslot.disrupt(store, weather, route_fixture())
    return harborwindow.disrupt(store, weather)


def _calm_weather(scenario: str) -> MockWeatherProvider:
    """Same world, benign forecast. Used to prove weather is load-bearing."""
    if scenario == "stormslot":
        return MockWeatherProvider({
            "PORT_A": [WeatherPoint(h, wind_kph=10, rain_mm=1) for h in range(13, 19)],
        })
    return MockWeatherProvider({
        "HARBOR_A": [WeatherPoint(h, wind_kph=12, rain_mm=2) for h in range(12, 18)],
        "ISLAND_B": [WeatherPoint(h, wind_kph=12, rain_mm=2) for h in range(12, 18)],
    })


def _departure(snap: Dict[str, Any]) -> Optional[float]:
    pid = snap["committed_plan_id"]
    if not pid:
        return None
    return snap["plans"][pid]["metrics"].get("departure_hour")


# --- the gates -----------------------------------------------------

def gate_weather_changes_a_decision(scenario: str) -> GateResult:
    stormy = _fresh(scenario)
    calm = _fresh(scenario)
    s_snap = _run(scenario, stormy, weather_fixture())
    c_snap = _run(scenario, calm, _calm_weather(scenario))
    s_dep, c_dep = _departure(s_snap), _departure(c_snap)
    booked = (calm.state.facts.get("booked_pickup_hour")
              or calm.state.facts.get("booked_departure_hour"))
    ok = s_dep != c_dep and c_dep == booked
    return GateResult(
        "1. Weather changes an operational decision",
        ok,
        f"calm forecast keeps the booked {booked}:00; storm forecast moves it to "
        f"{int(s_dep)}:00" if ok else
        f"calm departure {c_dep}, storm departure {s_dep}, booked {booked} - "
        f"weather did not move the decision",
    )


def gate_bounded_actors(scenario: str) -> GateResult:
    store = _fresh(scenario)
    _run(scenario, store, weather_fixture())
    claimants = {e.actor for e in store.events if e.kind in ("CLAIMED", "CLAIM_REAFFIRMED")}
    reports = {e.actor: sorted(e.payload) for e in store.events
               if e.kind == "CONSTRAINT_REPORTED"}
    proposers = {e.actor for e in store.events if e.kind == "PLAN_PROPOSED"}
    distinct_scopes = len({tuple(v) for v in reports.values()})
    ok = len(claimants) >= 2 and distinct_scopes == len(reports) and len(reports) >= 2
    return GateResult(
        "2. At least two bounded actors with different scopes",
        ok,
        f"{len(claimants)} claimants {sorted(claimants)}; {len(reports)} constraint "
        f"reports with {distinct_scopes} distinct scopes; proposers {sorted(proposers)}",
    )


def gate_false_proposal_is_rejected(scenario: str) -> GateResult:
    """Inject a plan that asserts success while proposing something infeasible."""
    from app.core.verify import verify_and_commit
    store = _fresh(scenario)
    if scenario == "stormslot":
        verifier = stormslot.make_verifier(store, weather_fixture().hourly("PORT_A"),
                                           route_fixture())
        actions = [{"type": "dispatch_truck", "hour": 16}]
    else:
        verifier = harborwindow.make_verifier(store, weather_fixture().hourly("HARBOR_A"),
                                              weather_fixture().hourly("ISLAND_B"))
        actions = [{"type": "depart", "hour": 12}]
    liar = CandidatePlan(id="gate-liar", scenario=scenario, created_by="hostile-agent",
                         actions=actions,
                         metrics={"weather_conflict": 0.0, "confidence": 0.99,
                                  "self_reported_status": 1.0})
    store.add_plan(liar, UNFENCED)
    committed = verify_and_commit(store, "gate-liar", verifier, fence=UNFENCED)
    plan = store.get_plan("gate-liar")
    ok = committed is False and store.state.committed_plan_id is None
    return GateResult(
        "3. A false or infeasible proposal is deterministically rejected",
        ok,
        f"confident proposal refused: {plan.rejection_reason!r}" if ok
        else "a plan asserting its own success reached commit",
    )


def gate_commit_requires_verification(scenario: str) -> GateResult:
    from app.core.verify import check_trace_integrity
    store = _fresh(scenario)
    snap = _run(scenario, store, weather_fixture())
    kinds = [e.kind for e in store.events]
    displayed = check_trace_integrity(store.trace())
    # every commit must be immediately preceded by a verification of that plan
    ordered = True
    for i, e in enumerate(store.events):
        if e.kind == "PLAN_COMMITTED":
            prior = [x for x in store.events[:i]
                     if x.kind == "PLAN_VERIFIED"
                     and x.payload["plan_id"] == e.payload["plan_id"]]
            ordered = ordered and bool(prior)
    # and a direct commit of an unverified plan must be refused
    store.add_plan(CandidatePlan(id="gate-bypass", scenario=scenario,
                                 created_by="agent", actions=[], metrics={}),
                   UNFENCED)
    bypassed = store.commit_plan("gate-bypass", "agent", UNFENCED)
    ok = (ordered and bypassed is False and not displayed
          and snap["committed_plan_id"] != "gate-bypass")
    return GateResult(
        "4. Authoritative state changes only after verification",
        ok,
        f"{kinds.count('PLAN_COMMITTED')} commit(s), each preceded by PLAN_VERIFIED; "
        f"direct commit of an unverified plan refused; displayed trace order "
        f"self-consistent" if ok else
        f"ordering violations in the displayed trace: {displayed or 'bypass succeeded'}",
    )


def gate_demo_legibility(scenario: str) -> GateResult:
    store = _fresh(scenario)
    _run(scenario, store, weather_fixture())
    events = len(store.events)
    rejections = sum(1 for e in store.events if e.kind == "PLAN_REJECTED")
    return GateResult(
        "5. Understandable without >30s narration",
        None,
        f"{events} events, {rejections} visible rejection(s), one committed plan. "
        f"Judge this from the dashboard, not from the count.",
    )


def gate_deterministic_replay(scenario: str) -> GateResult:
    runs = []
    for _ in range(2):
        store = _fresh(scenario)
        snap = _run(scenario, store, weather_fixture())
        _disrupt(scenario, store, disrupted_weather_fixture())
        runs.append((store.state.committed_plan_id,
                     [e.kind for e in store.events],
                     [(e.kind, json.dumps(e.payload, sort_keys=True)) for e in store.events]))
    ok = runs[0] == runs[1]
    return GateResult(
        "6. Deterministic replay from the seed, live API down",
        ok,
        f"two seeded runs incl. disruption produced identical traces "
        f"({len(runs[0][1])} events, committed {runs[0][0]})" if ok
        else "seeded runs diverged",
    )


GATES: List[Callable[[str], GateResult]] = [
    gate_weather_changes_a_decision,
    gate_bounded_actors,
    gate_false_proposal_is_rejected,
    gate_commit_requires_verification,
    gate_demo_legibility,
    gate_deterministic_replay,
]


def evaluate(scenario: str) -> Dict[str, Any]:
    results = [g(scenario) for g in GATES]
    mechanical = [r for r in results if r.passed is not None]
    return {
        "scenario": scenario,
        "survives": all(r.passed for r in mechanical),
        "mechanical_passed": sum(1 for r in mechanical if r.passed),
        "mechanical_total": len(mechanical),
        "manual_gates": [r.gate for r in results if r.passed is None],
        "results": [asdict(r) for r in results],
    }


def evaluate_all() -> Dict[str, Any]:
    return {s: evaluate(s) for s in SCENARIOS}


def render(report: Dict[str, Any]) -> str:
    lines = []
    for scenario, r in report.items():
        head = (f"{scenario}  —  {r['mechanical_passed']}/{r['mechanical_total']} "
                f"mechanical gates, {'SURVIVES' if r['survives'] else 'BLOCKED'}")
        lines += ["", head, "-" * len(head)]
        for item in r["results"]:
            lines.append(f"  [{item['passed'] and 'PASS' or (item['passed'] is None and 'manual' or 'FAIL'):>6}]  "
                         f"{item['gate']}")
            lines.append(f"          {item['evidence']}")
    lines += ["",
              "Hard gates only. HarborWindow is the submission flagship;",
              "StormSlot remains transfer evidence for the shared operational",
              "substrate.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Evaluate the decision-gate hard gates.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("scenario", nargs="?", choices=SCENARIOS)
    a = ap.parse_args()
    report = {a.scenario: evaluate(a.scenario)} if a.scenario else evaluate_all()
    print(json.dumps(report, indent=2) if a.json else render(report))
