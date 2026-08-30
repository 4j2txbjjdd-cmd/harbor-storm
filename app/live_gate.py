"""Live-weather gate: prove both scenarios ran on real Google Weather output.

    WEATHER_PROVIDER=google GOOGLE_WEATHER_API_KEY=... \
      .venv/bin/python -m app.live_gate --provider live
    .venv/bin/python -m app.live_gate --provider live --scenario stormslot --json

This is a second lane, not a replacement. `app.gate` stays seeded, offline and
deterministic; it must never learn what the weather is today. This module is the
only place in the repo that requires the network, and it exists to answer one
question a deterministic gate cannot: did Harbor actually consume external truth?

`--provider live` is an assertion, not a switch. The lane is chosen once, by
`app.config.Settings` reading `WEATHER_PROVIDER`; this flag says what the caller
believes that produced, and the gate refuses to run if the two disagree. A
single place to set the lane and a separate place to assert it is what keeps a
failed live run from being reported as a passing seeded one.

What each check is worth:

  provider lane      the object handing out observations is the live adapter
  request reached    provenance shows real HTTP 200s with real timestamps
  normalized         those responses became WeatherPoints the scenario can read
  scenario consumed  the severe hours ON THE TRACE equal the severe hours
                     recomputed from the live series -- not merely that a
                     provider existed, but that its output drove the record
  outcome consistent whatever Harbor decided is explained by those observations

The last one is the reason this gate does not assert weather values. Live
weather changes; on a calm day the booked slot holds and on a rough one it does
not, and both are correct. What must hold either way is that the decision and
the observations agree.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import ConfigError, Settings, make_routes, make_weather
from app.core.store import InMemoryStateStore
from app.providers.weather import WeatherPoint, WeatherProviderError
from app.scenarios import harborwindow, stormslot

SCENARIOS = ("harborwindow", "stormslot")
LIVE_PROVIDER_NAME = "google-weather-live"


class LiveLaneError(RuntimeError):
    """The run is not the live run it was asked to be.

    Raised rather than reported, because every check below is only meaningful
    if the lane is what it claims. A structural check that passes against a
    fixture is worse than no check at all.
    """


@dataclass
class Check:
    name: str
    passed: bool
    evidence: str

    @property
    def mark(self) -> str:
        return "PASS" if self.passed else "FAIL"


# --- the two scenarios read weather differently, and that is preserved -------
#
# StormSlot asks one question of one site: is the rain over the storm threshold
# on the road. HarborWindow asks two questions of two sites: is wind or rain
# over the marine limit at either end of the crossing. Flattening them into one
# "severe hours" helper would make the gate agree with itself rather than with
# the scenarios, so each lens below calls the scenario's own code.


@dataclass
class ScenarioLens:
    scenario: str
    sites: Callable[[dict], List[str]]
    measured_event: str
    weather_rule: str
    run: Callable[[Any, Any, Any], dict]
    severe: Callable[[Any, dict], List[int]]
    exposure: Callable[[int, dict, Any], List[int]]
    summarise: Callable[[Any, dict], Dict[str, Any]]
    departure_metric: str


def _stormslot_severe(weather, facts) -> List[int]:
    series = weather.hourly(facts["port"])
    return stormslot.severe_hours(series, facts["storm_threshold_rain_mm"])


def _stormslot_exposure(depart: int, facts: dict, routes) -> List[int]:
    est = routes.estimate(facts["port"], facts["warehouse"], depart)
    return stormslot.transit_hours(depart, est.minutes)


def _harborwindow_severe(weather, facts) -> List[int]:
    by_h = {p.hour: p for p in weather.hourly(facts["harbor"])}
    by_i = {p.hour: p for p in weather.hourly(facts["island"])}
    return [h for h in sorted(set(by_h) & set(by_i))
            if harborwindow._unsafe_reason(h, by_h, by_i, facts)]


def _harborwindow_exposure(depart: int, facts: dict, _routes) -> List[int]:
    return harborwindow.crossing_hours(depart, facts["crossing_hours"])


# On a calm day "severe hours = []" is the right answer and a weak sentence: an
# empty list equals an empty list whatever produced it. These summaries put the
# actual numbers next to the actual limits, so a reader can see that real
# observations were compared against a real threshold and how much room there
# was -- which stays informative in weather that decides nothing.

def _stormslot_summary(weather, facts) -> Dict[str, Any]:
    series = weather.hourly(facts["port"])
    return {"site": facts["port"],
            "max_rain_mm": max(p.rain_mm for p in series),
            "limit_rain_mm": facts["storm_threshold_rain_mm"],
            "max_wind_kph_observed": max(p.wind_kph for p in series)}


def _harborwindow_summary(weather, facts) -> Dict[str, Any]:
    out: Dict[str, Any] = {"limit_wind_kph": facts["max_wind_kph"],
                           "limit_rain_mm": facts["max_rain_mm"]}
    for key in ("harbor", "island"):
        series = weather.hourly(facts[key])
        out[facts[key]] = {"max_wind_kph": max(p.wind_kph for p in series),
                           "max_rain_mm": max(p.rain_mm for p in series)}
    return out


LENSES: Dict[str, ScenarioLens] = {
    "stormslot": ScenarioLens(
        scenario="stormslot",
        sites=lambda f: [f["port"]],
        measured_event="WEATHER_MEASURED",
        weather_rule="rain at or above storm_threshold_rain_mm on the road",
        run=lambda store, weather, routes: stormslot.run(store, weather, routes),
        severe=_stormslot_severe,
        exposure=_stormslot_exposure,
        summarise=_stormslot_summary,
        departure_metric="departure_hour",
    ),
    "harborwindow": ScenarioLens(
        scenario="harborwindow",
        sites=lambda f: [f["harbor"], f["island"]],
        measured_event="MARINE_WEATHER_MEASURED",
        weather_rule="wind over max_wind_kph or rain over max_rain_mm at either end",
        run=lambda store, weather, _routes: harborwindow.run(store, weather),
        severe=_harborwindow_severe,
        exposure=_harborwindow_exposure,
        summarise=_harborwindow_summary,
        departure_metric="departure_hour",
    ),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _require_live_lane(settings: Settings, asserted: str) -> None:
    if asserted != "live":
        raise LiveLaneError(f"unknown provider lane {asserted!r}; this gate runs 'live'")
    if not settings.is_live:
        raise LiveLaneError(
            f"--provider live was asserted but WEATHER_PROVIDER resolves to "
            f"{settings.weather_provider!r}. The lane is chosen by configuration "
            f"and only asserted here; set WEATHER_PROVIDER=google, or run the "
            f"deterministic lane with `python -m app.gate`.")


def check_scenario(scenario: str, settings: Settings) -> Dict[str, Any]:
    """Run one scenario on live weather and report what that proves."""
    lens = LENSES[scenario]
    build = (stormslot.build_state if scenario == "stormslot"
             else harborwindow.build_state)
    store = InMemoryStateStore(build())
    facts = store.state.facts
    routes = make_routes(settings)

    weather = make_weather(settings)
    if getattr(weather, "name", None) != LIVE_PROVIDER_NAME:
        raise LiveLaneError(
            f"configuration produced provider {getattr(weather, 'name', '?')!r}, "
            f"not {LIVE_PROVIDER_NAME!r}. Refusing to report a seeded run as live.")

    started = _now()
    snap = lens.run(store, weather, routes)
    provenance = weather.provenance()

    checks: List[Check] = []

    # 1. the object that answered was the live adapter
    checks.append(Check(
        "provider lane is live", True,
        f"{weather.name}; routes are the seeded fixture "
        f"({make_routes(settings).__class__.__name__}) -- this gate proves live "
        f"weather, not live routing"))

    # 2. requests actually left the process
    wanted = lens.sites(facts)
    got = [r["site"] for r in provenance]
    ok_http = all(r["http_status"] == 200 and r["points_returned"] > 0
                  for r in provenance)
    checks.append(Check(
        "request reached the Google Weather API",
        bool(provenance) and ok_http and set(wanted) <= set(got),
        f"{len(provenance)} fetch(es) for {got}, all HTTP 200 with points; "
        f"scenario needed {wanted}"))

    # 3. responses normalised into the shape the scenario reads
    coverage: Dict[str, List[int]] = {}
    for site in wanted:
        coverage[site] = sorted(p.hour for p in weather.hourly(site))
    normalised = all(len(v) > 0 for v in coverage.values())
    checks.append(Check(
        "normalised into WeatherPoint hours", normalised,
        "; ".join(f"{s}: {len(h)} hours {h[:3]}..{h[-1:]}" for s, h in coverage.items())))

    # 4. the trace was driven by those observations, not merely alongside them
    measured = [e for e in store.trace() if e["kind"] == lens.measured_event]
    on_trace = sorted((measured[0]["payload"] or {}).get("severe_hours", [])) if measured else None
    recomputed = sorted(lens.severe(weather, facts))
    summary = lens.summarise(weather, facts)
    checks.append(Check(
        "scenario consumed the live observations",
        bool(measured) and on_trace == recomputed,
        f"{lens.measured_event} severe_hours={on_trace} equals recomputation "
        f"from the live series {recomputed} ({lens.weather_rule}); observed "
        f"{json.dumps(summary, default=str)}"))

    # 5. whatever Harbor decided is explained by what it observed
    committed_id = snap["committed_plan_id"]
    rejections = [p["rejection_reason"] for p in snap["plans"].values()
                  if p["rejection_reason"]]
    if committed_id:
        plan = snap["plans"][committed_id]
        depart = int(plan["metrics"][lens.departure_metric])
        exposure = lens.exposure(depart, facts, routes)
        clash = sorted(set(exposure) & set(recomputed))
        uncovered = [h for h in exposure
                     if any(h not in coverage[s] for s in wanted)]
        checks.append(Check(
            "outcome is consistent with the observations",
            not clash and not uncovered,
            f"committed {committed_id} departing {depart}:00; exposed hours "
            f"{exposure} are covered by the live forecast and none is severe "
            f"{recomputed}"))
        outcome = f"committed {committed_id} at {depart}:00"
    else:
        checks.append(Check(
            "outcome is consistent with the observations",
            bool(rejections),
            f"no plan committed; every candidate carries a recorded reason: "
            f"{rejections}"))
        outcome = "no feasible plan; all candidates rejected on the record"

    return {
        "scenario": scenario,
        "started_at": started,
        "provider": weather.name,
        "weather_rule": lens.weather_rule,
        "provenance": provenance,
        "hours_covered": coverage,
        "observed": summary,
        "severe_hours_live": recomputed,
        "committed_plan_id": committed_id,
        "outcome": outcome,
        "rejections": rejections,
        "checks": [{"name": c.name, "passed": c.passed, "evidence": c.evidence}
                   for c in checks],
        "passed": all(c.passed for c in checks),
        "trace": [{"seq": e["seq"], "kind": e["kind"], "actor": e["actor"]}
                  for e in store.trace()],
    }


def evaluate(scenarios: List[str], settings: Optional[Settings] = None,
             asserted_provider: str = "live") -> Dict[str, Any]:
    settings = settings or Settings.from_env()
    _require_live_lane(settings, asserted_provider)
    results = [check_scenario(s, settings) for s in scenarios]
    return {
        "gate": "live-weather",
        "started_at": _now(),
        "api": "weather.googleapis.com",
        "provider_asserted": asserted_provider,
        "weather_provider_configured": settings.weather_provider,
        "forecast_hours": settings.forecast_hours,
        "scenarios": results,
        "passed": all(r["passed"] for r in results),
    }


def render(report: Dict[str, Any]) -> str:
    out = [f"live weather gate  —  {report['started_at']}",
           f"  api      {report['api']}",
           f"  lane     asserted {report['provider_asserted']}, "
           f"configured WEATHER_PROVIDER={report['weather_provider_configured']}",
           ""]
    for r in report["scenarios"]:
        out.append(f"{r['scenario']}  —  {'LIVE PASS' if r['passed'] else 'LIVE FAIL'}")
        out.append(f"  rule: {r['weather_rule']}")
        for f in r["provenance"]:
            out.append(f"  fetch {f['site']:<10} ({f['latitude']},{f['longitude']}) "
                       f"HTTP {f['http_status']} {f['points_returned']} pts "
                       f"tz={f['time_zone']} at {f['requested_at']}")
        out.append(f"  observed: {json.dumps(r['observed'], default=str)}")
        out.append(f"  severe hours (live): {r['severe_hours_live']}")
        for c in r["checks"]:
            out.append(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['name']}")
            out.append(f"         {c['evidence']}")
        out.append(f"  OUTCOME: {r['outcome']}")
        out.append("")
    out.append("LIVE PASS — both lanes agree" if report["passed"]
               else "LIVE FAIL — read the checks above")
    return "\n".join(out)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", required=True, choices=("live",),
                    help="assert the lane this run must be; the lane itself is "
                         "set by WEATHER_PROVIDER and the two must agree")
    ap.add_argument("--scenario", choices=SCENARIOS, default=None,
                    help="default: both")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="write the full evidence JSON to this path")
    a = ap.parse_args(argv)

    scenarios = [a.scenario] if a.scenario else list(SCENARIOS)
    try:
        report = evaluate(scenarios, asserted_provider=a.provider)
    except (LiveLaneError, ConfigError, WeatherProviderError) as exc:
        # Deliberately no fallback. A live gate that answers from a fixture when
        # the network is down is the exact failure this lane exists to exclude,
        # so the only thing left to do is say what broke and stop.
        print(f"LIVE GATE BLOCKED — {type(exc).__name__}: {exc}", file=sys.stderr)
        print("No seeded result was substituted. Nothing was proven.", file=sys.stderr)
        return 2

    if a.out:
        with open(a.out, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
    print(json.dumps(report, indent=2, default=str) if a.json else render(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
