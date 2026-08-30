"""Run the seeded ReliefRun scenario end to end.

Separate from app.demo on purpose: the two frozen scenarios and their demo
harness are unchanged. This is the additive third instantiation, with its
own seeded fixtures.

The disrupted fixture models the secondary-hazard shape of the August 2026
Nepal floods: after the first surge has already forced a replan, an upstream
barrier-lake alert raises the corridor hazard across the very window the
committed mission holds -- so a mission that was correct when planned is
revoked before it becomes action, and the fleet re-commits into the next
safe window.
"""
from __future__ import annotations
import argparse
import json

from app.core.store import InMemoryStateStore
from app.providers.weather import MockWeatherProvider, WeatherPoint
from app.scenarios import reliefrun


def weather_fixture() -> MockWeatherProvider:
    """Seeded post-surge baseline. Dawn hours are unsafe -- slope run-off and
    debris flow after the first surge -- and late afternoon closes again."""
    return MockWeatherProvider({
        "CORRIDOR_A": [
            WeatherPoint(6, wind_kph=22, rain_mm=24),
            WeatherPoint(7, wind_kph=20, rain_mm=18),
            WeatherPoint(8, wind_kph=18, rain_mm=12),
            WeatherPoint(9, wind_kph=15, rain_mm=8),
            WeatherPoint(10, wind_kph=14, rain_mm=6),
            WeatherPoint(11, wind_kph=16, rain_mm=9),
            WeatherPoint(12, wind_kph=15, rain_mm=5),
            WeatherPoint(13, wind_kph=17, rain_mm=7),
            WeatherPoint(14, wind_kph=19, rain_mm=10),
            WeatherPoint(15, wind_kph=21, rain_mm=12),
            WeatherPoint(16, wind_kph=24, rain_mm=21),
            WeatherPoint(17, wind_kph=20, rain_mm=14),
        ],
        "VILLAGE_X": [
            WeatherPoint(6, wind_kph=18, rain_mm=17),
            WeatherPoint(7, wind_kph=17, rain_mm=16),
            WeatherPoint(8, wind_kph=15, rain_mm=11),
            WeatherPoint(9, wind_kph=13, rain_mm=7),
            WeatherPoint(10, wind_kph=12, rain_mm=5),
            WeatherPoint(11, wind_kph=14, rain_mm=8),
            WeatherPoint(12, wind_kph=13, rain_mm=4),
            WeatherPoint(13, wind_kph=15, rain_mm=6),
            WeatherPoint(14, wind_kph=16, rain_mm=9),
            WeatherPoint(15, wind_kph=18, rain_mm=11),
            WeatherPoint(16, wind_kph=20, rain_mm=18),
            WeatherPoint(17, wind_kph=17, rain_mm=12),
        ],
    })


def calm_weather_fixture() -> MockWeatherProvider:
    """Negative control: every hour safe. Under this forecast the booked
    first-light departure must survive untouched, or the scenario is being
    steered by something other than the hazard."""
    return MockWeatherProvider({
        loc: [WeatherPoint(h, wind_kph=12, rain_mm=3) for h in range(6, 18)]
        for loc in ("CORRIDOR_A", "VILLAGE_X")
    })


def disrupted_weather_fixture() -> MockWeatherProvider:
    """Barrier-lake alert: breach risk raises the corridor hazard across the
    mid-morning window the committed mission holds. Midday clears."""
    return MockWeatherProvider({
        "CORRIDOR_A": [
            WeatherPoint(6, wind_kph=22, rain_mm=24),
            WeatherPoint(7, wind_kph=20, rain_mm=18),
            WeatherPoint(8, wind_kph=19, rain_mm=16),
            WeatherPoint(9, wind_kph=18, rain_mm=26),
            WeatherPoint(10, wind_kph=17, rain_mm=30),
            WeatherPoint(11, wind_kph=16, rain_mm=22),
            WeatherPoint(12, wind_kph=15, rain_mm=8),
            WeatherPoint(13, wind_kph=16, rain_mm=9),
            WeatherPoint(14, wind_kph=18, rain_mm=11),
            WeatherPoint(15, wind_kph=20, rain_mm=12),
            WeatherPoint(16, wind_kph=24, rain_mm=21),
            WeatherPoint(17, wind_kph=20, rain_mm=14),
        ],
        "VILLAGE_X": [
            WeatherPoint(6, wind_kph=18, rain_mm=17),
            WeatherPoint(7, wind_kph=17, rain_mm=16),
            WeatherPoint(8, wind_kph=16, rain_mm=13),
            WeatherPoint(9, wind_kph=15, rain_mm=14),
            WeatherPoint(10, wind_kph=14, rain_mm=12),
            WeatherPoint(11, wind_kph=14, rain_mm=10),
            WeatherPoint(12, wind_kph=13, rain_mm=5),
            WeatherPoint(13, wind_kph=15, rain_mm=6),
            WeatherPoint(14, wind_kph=16, rain_mm=9),
            WeatherPoint(15, wind_kph=18, rain_mm=11),
            WeatherPoint(16, wind_kph=20, rain_mm=18),
            WeatherPoint(17, wind_kph=17, rain_mm=12),
        ],
    })


def new_store() -> InMemoryStateStore:
    return InMemoryStateStore(reliefrun.build_state())


def run_one(disrupt: bool = False, pretty: bool = False) -> dict:
    store = new_store()
    snap = reliefrun.run(store, weather_fixture())
    if disrupt:
        snap = reliefrun.disrupt(store, disrupted_weather_fixture())

    committed = snap["committed_plan_id"]
    result = {
        "scenario": "reliefrun",
        "committed_plan": committed,
        "plan": snap["plans"].get(committed) if committed else None,
        "event_trace": store.trace(),
    }
    if pretty:
        print(f"\n=== reliefrun{' + barrier-lake alert' if disrupt else ''} ===")
        for e in result["event_trace"]:
            detail = {k: v for k, v in e["payload"].items()
                      if k in ("severe_hours", "plan_id", "reason", "from_hour",
                               "to_hour", "work_id", "current_claimant")}
            print(f"{e['seq']:>3}  {e['kind']:<24} {e['actor']:<20} "
                  f"{json.dumps(detail) if detail else ''}")
        print(f"     COMMITTED -> {committed}")
    else:
        print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Run the seeded ReliefRun scenario end to end.")
    ap.add_argument("--disrupt", action="store_true",
                    help="apply the barrier-lake alert after the first commit")
    ap.add_argument("--pretty", action="store_true",
                    help="human-readable event trace")
    a = ap.parse_args()
    run_one(disrupt=a.disrupt, pretty=a.pretty)
