"""Coordination metrics, folded from the event trace.

The trace is the record, so the numbers a disaster coordinator would ask for
are a fold over it, not new instrumentation: how often a mission that was
already authorized lost that authority, how long reallocation took, how many
double-allocation attempts were refused, and how many redeliveries were
absorbed without effect. Works on any scenario's trace, because every
scenario shares the same membrane vocabulary.

Usage:
    .venv/bin/python -m app.metrics relieffleet --disrupt
    .venv/bin/python -m app.metrics harborwindow --disrupt
    .venv/bin/python -m app.metrics --from-json trace.json
"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from typing import Any, Dict, List

REJECTION_CLASSES = (
    ("stale", ("stale",)),
    ("hazard/weather", ("hazard", "wind", "rain", "weather", "storm",
                        "severe")),
    ("topology", ("failed edge", "route")),
    ("capacity/beds", ("capacity", "beds", "exceed")),
    ("schedule", ("window", "cutoff", "ready", "slot", "departure")),
)


def _classify(reason: str) -> str:
    low = reason.lower()
    for label, needles in REJECTION_CLASSES:
        if any(n in low for n in needles):
            return label
    return "other"


def fold(trace: List[Dict[str, Any]]) -> Dict[str, Any]:
    rejections = Counter()
    revocations = 0
    revocation_reasons: List[str] = []
    claim_refusals = 0
    duplicates = 0
    commits: List[int] = []
    reallocation_spans: List[int] = []
    pending_revoke_seq = None

    for e in trace:
        kind, seq = e["kind"], e["seq"]
        payload = e.get("payload", {})
        if kind == "PLAN_REJECTED":
            rejections[_classify(payload.get("reason", ""))] += 1
        elif kind == "COMMIT_REVOKED":
            revocations += 1
            revocation_reasons.append(payload.get("reason", ""))
            pending_revoke_seq = seq
        elif kind == "PLAN_COMMITTED":
            commits.append(seq)
            if pending_revoke_seq is not None:
                reallocation_spans.append(seq - pending_revoke_seq)
                pending_revoke_seq = None
        elif kind == "CLAIM_REFUSED":
            claim_refusals += 1
        elif kind == "DUPLICATE_EVENT_IGNORED":
            duplicates += 1

    return {
        "events": len(trace),
        "commits": len(commits),
        "rejections_by_class": dict(rejections),
        "revocations": revocations,
        "revocation_reasons": revocation_reasons,
        "unrecovered_revocations": 1 if pending_revoke_seq is not None else 0,
        # Trace distance between losing authority and the next commit: how
        # much recorded work reallocation took, in events. Wall-clock latency
        # is a property of the harness (sentinel interval, scheduler cadence),
        # not of the trace, and is deliberately not invented here.
        "reallocation_span_events": reallocation_spans,
        "double_allocation_attempts_refused": claim_refusals,
        "redeliveries_absorbed": duplicates,
    }


def _trace_for(scenario: str, disrupt: bool) -> List[Dict[str, Any]]:
    if scenario == "relieffleet":
        from app.fleet_demo import run_one
    elif scenario == "reliefrun":
        from app.relief_demo import run_one
    else:
        from app.demo import run_one as _demo_run

        def run_one(disrupt=False, pretty=False):
            return _demo_run(scenario, disrupt=disrupt, pretty=pretty)

    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_one(disrupt=disrupt)
    return result["event_trace"]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Fold coordination metrics "
                                             "from an event trace.")
    ap.add_argument("scenario", nargs="?", default=None,
                    choices=["stormslot", "harborwindow", "reliefrun",
                             "relieffleet"])
    ap.add_argument("--disrupt", action="store_true")
    ap.add_argument("--from-json", default=None,
                    help="read a trace (or a demo result containing "
                         "event_trace) from a JSON file instead of running "
                         "a scenario")
    a = ap.parse_args(argv)

    if a.from_json:
        data = json.load(open(a.from_json))
        trace = data["event_trace"] if isinstance(data, dict) else data
        label = a.from_json
    elif a.scenario:
        trace = _trace_for(a.scenario, a.disrupt)
        label = f"{a.scenario}{' + disruption' if a.disrupt else ''}"
    else:
        ap.error("give a scenario or --from-json")
        return

    print(f"=== metrics: {label} ===")
    print(json.dumps(fold(trace), indent=2))


if __name__ == "__main__":
    main()
