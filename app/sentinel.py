"""Wall-clock re-observation: the long-horizon harness over the membrane.

The sentinel owns no authority and writes nothing to the trace itself. On an
interval it re-reads the forecast, and when the observation changes it hands
the new truth to the same disruption path the seeded demos use: advance the
revision, re-verify the committed plan through the deterministic verifier,
replan if the commitment broke. Every authoritative consequence --
COMMIT_REVOKED, PLAN_REJECTED, PLAN_COMMITTED -- is attributed to the
verifier, because it is the same code path; the sentinel is a clock, not a
decider.

Observations are content-addressed: the disruption event id is derived from
the forecast itself, so a repeated or crash-replayed observation applies at
most once on any backend. On a durable backend (STATE_BACKEND=firestore) the
sentinel can be killed and restarted against the same run id; it re-observes
on startup and reconciles, because it cannot know what moved while nothing
was watching.
"""
from __future__ import annotations
import argparse
import hashlib
import itertools
import json
import time
from datetime import datetime, timezone
from typing import List, Optional

from app.config import SCENARIOS, ConfigError, Settings, build_state, \
    make_routes, make_weather, make_store
from app.core.store import Store
from app.providers.routes import RouteProvider
from app.providers.weather import WeatherProvider
from app.scenarios import stormslot, harborwindow


def _forecast_locations(scenario: str, facts: dict) -> List[str]:
    if scenario == "stormslot":
        return [facts["port"]]
    return [facts["harbor"], facts["island"]]


def observation_fingerprint(scenario: str, facts: dict,
                            weather: WeatherProvider) -> str:
    """Deterministic digest of everything the verifier would read from this
    forecast. Two observations with the same digest are the same observation."""
    payload = {
        loc: [[p.hour, p.wind_kph, p.rain_mm]
              for p in sorted(weather.hourly(loc), key=lambda p: p.hour)]
        for loc in _forecast_locations(scenario, facts)
    }
    blob = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


class Sentinel:
    """Polls the forecast and routes a changed observation into the scenario's
    disruption path. Holds no verify or commit tool.

    ``baseline_fingerprint`` is the observation the current plan was computed
    from. Pass it when the caller has just planned against this provider, so
    an unchanged forecast is a pure no-op. Pass None when attaching to an
    existing run: the first tick then reconciles unconditionally, and the
    content-addressed event id makes re-applying an already-seen observation
    leave no mark.
    """

    def __init__(self, store: Store, weather: WeatherProvider, scenario: str,
                 routes: Optional[RouteProvider] = None,
                 baseline_fingerprint: Optional[str] = None):
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        if scenario == "stormslot" and routes is None:
            raise ValueError("stormslot requires a route provider")
        self.store = store
        self.weather = weather
        self.scenario = scenario
        self.routes = routes
        self.last_fingerprint = baseline_fingerprint

    def _apply(self, fingerprint: str) -> None:
        event_id = f"forecast-{fingerprint}"
        if self.scenario == "stormslot":
            stormslot.disrupt(self.store, self.weather, self.routes,
                              event_id=event_id)
        else:
            harborwindow.disrupt(self.store, self.weather, event_id=event_id)

    def tick(self) -> dict:
        """One observation. Applies the disruption path only when the forecast
        differs from the last one this sentinel saw."""
        before_events = len(self.store.trace())
        before_revision = self.store.state.revision
        fingerprint = observation_fingerprint(
            self.scenario, self.store.state.facts, self.weather)
        changed = fingerprint != self.last_fingerprint
        if changed:
            self._apply(fingerprint)
        self.last_fingerprint = fingerprint

        snap = self.store.snapshot()
        return {
            "fingerprint": fingerprint,
            "changed": changed,
            # A changed observation that was already applied (crash replay,
            # redelivery) is deduplicated inside advance_revision and moves
            # nothing; report what actually happened, not what was attempted.
            "revision_advanced": snap["revision"] != before_revision,
            "revision": snap["revision"],
            "committed_plan_id": snap["committed_plan_id"],
            "new_events": self.store.trace()[before_events:],
        }


def _print_tick(n: int, result: dict) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    if result["revision_advanced"]:
        status = "CHANGED -> applied"
    elif result["changed"]:
        status = "seen -> already applied"
    else:
        status = "unchanged"
    print(f"[{stamp}] tick {n:<3} forecast={result['fingerprint']}  "
          f"{status}  revision={result['revision']} "
          f"committed={result['committed_plan_id']}", flush=True)
    for e in result["new_events"]:
        detail = {k: v for k, v in e["payload"].items()
                  if k in ("severe_hours", "plan_id", "reason",
                           "from_hour", "to_hour")}
        print(f"        {e['seq']:>3}  {e['kind']:<24} {e['actor']:<20} "
              f"{json.dumps(detail) if detail else ''}", flush=True)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(
        description="Watch the forecast on a wall-clock interval and keep "
                    "the committed plan honest.")
    ap.add_argument("scenario", nargs="?", default="harborwindow",
                    choices=SCENARIOS)
    ap.add_argument("--interval", type=float, default=60.0,
                    help="seconds between observations (default 60)")
    ap.add_argument("--ticks", type=int, default=None,
                    help="stop after N ticks (default: run until interrupted)")
    ap.add_argument("--run-id", default=None,
                    help="attach to an existing run instead of seeding a new "
                         "one (STATE_BACKEND=firestore only)")
    ap.add_argument("--disrupt-at-tick", type=int, default=None,
                    help="seeded lane only: swap to the disrupted fixture at "
                         "tick N, for a deterministic wall-clock demo")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if args.disrupt_at_tick is not None and settings.is_live:
        raise ConfigError(
            "--disrupt-at-tick stages a seeded forecast change and is "
            "meaningless against a live provider; on the live lane the real "
            "forecast is the disruption.")
    if args.run_id and settings.state_backend != "firestore":
        raise ConfigError(
            "--run-id attaches to a durable run and requires "
            "STATE_BACKEND=firestore; an in-memory run does not outlive "
            "its process.")

    weather = make_weather(settings)
    routes = make_routes(settings) if args.scenario == "stormslot" else None

    if args.run_id:
        store = make_store(settings, args.run_id, args.scenario)
        baseline = None  # attach: reconcile on the first tick
        print(f"attached to run {args.run_id!r}; reconciling on first tick",
              flush=True)
    else:
        run_id = f"sentinel-{args.scenario}-{int(time.time())}"
        baseline = observation_fingerprint(
            args.scenario, build_state(args.scenario).facts, weather)
        store = make_store(settings, run_id, args.scenario)
        if args.scenario == "stormslot":
            stormslot.run(store, weather, routes)
        else:
            harborwindow.run(store, weather)
        snap = store.snapshot()
        print(f"run {run_id!r} seeded and planned: "
              f"committed={snap['committed_plan_id']} "
              f"revision={snap['revision']}", flush=True)

    sentinel = Sentinel(store, weather, args.scenario, routes,
                        baseline_fingerprint=baseline)

    for n in itertools.count(1):
        if args.disrupt_at_tick is not None and n == args.disrupt_at_tick:
            sentinel.weather = make_weather(settings, seeded="disrupted")
        _print_tick(n, sentinel.tick())
        if args.ticks is not None and n >= args.ticks:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
