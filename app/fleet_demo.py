"""Run the seeded ReliefFleet scenario end to end.

Additive demo harness, separate from app.demo and app.relief_demo. The
disrupted fixture models the second-surge shape: a barrier-lake pulse closes
the morning windows for trucks while leaving the helicopter's wind limits
clear, and a bridge fails — so one village's relief goes airborne in the same
window while the road missions wait out the surge until noon. The committed
fleet assignment is revoked on a named reason and reallocated, exactly like
every other plan on this substrate.
"""
from __future__ import annotations
import argparse
import json

from app.core.store import InMemoryStateStore
from app.providers.weather import MockWeatherProvider, WeatherPoint
from app.scenarios import relieffleet


def weather_fixture() -> MockWeatherProvider:
    """Post-surge baseline: dawn is unsafe for everything, mid-morning
    clears, late afternoon closes again."""
    return MockWeatherProvider({
        "CORRIDOR_A": [
            WeatherPoint(6, wind_kph=32, rain_mm=24),
            WeatherPoint(7, wind_kph=28, rain_mm=18),
            WeatherPoint(8, wind_kph=26, rain_mm=12),
            WeatherPoint(9, wind_kph=18, rain_mm=8),
            WeatherPoint(10, wind_kph=16, rain_mm=6),
            WeatherPoint(11, wind_kph=20, rain_mm=9),
            WeatherPoint(12, wind_kph=15, rain_mm=5),
            WeatherPoint(13, wind_kph=17, rain_mm=7),
            WeatherPoint(14, wind_kph=22, rain_mm=10),
            WeatherPoint(15, wind_kph=24, rain_mm=12),
            WeatherPoint(16, wind_kph=34, rain_mm=21),
            WeatherPoint(17, wind_kph=20, rain_mm=14),
        ],
    })


def calm_weather_fixture() -> MockWeatherProvider:
    """Negative control: every hour safe for every vehicle kind."""
    return MockWeatherProvider({
        "CORRIDOR_A": [WeatherPoint(h, wind_kph=12, rain_mm=3)
                       for h in range(6, 18)],
    })


def disrupted_weather_fixture() -> MockWeatherProvider:
    """Barrier-lake pulse: water hazard floods the truck windows 9-11 while
    wind stays inside the helicopter's limit."""
    return MockWeatherProvider({
        "CORRIDOR_A": [
            WeatherPoint(6, wind_kph=32, rain_mm=24),
            WeatherPoint(7, wind_kph=28, rain_mm=18),
            WeatherPoint(8, wind_kph=27, rain_mm=16),
            WeatherPoint(9, wind_kph=26, rain_mm=26),
            WeatherPoint(10, wind_kph=24, rain_mm=30),
            WeatherPoint(11, wind_kph=22, rain_mm=22),
            WeatherPoint(12, wind_kph=15, rain_mm=8),
            WeatherPoint(13, wind_kph=17, rain_mm=7),
            WeatherPoint(14, wind_kph=22, rain_mm=10),
            WeatherPoint(15, wind_kph=24, rain_mm=12),
            WeatherPoint(16, wind_kph=34, rain_mm=21),
            WeatherPoint(17, wind_kph=20, rain_mm=14),
        ],
    })


def new_store() -> InMemoryStateStore:
    return InMemoryStateStore(relieffleet.build_state())


def run_one(disrupt: bool = False, pretty: bool = False) -> dict:
    store = new_store()
    snap = relieffleet.run(store, weather_fixture())
    if disrupt:
        snap = relieffleet.disrupt(store, disrupted_weather_fixture(),
                                   failed_edges=["BR1"])

    committed = snap["committed_plan_id"]
    result = {
        "scenario": "relieffleet",
        "committed_plan": committed,
        "plan": snap["plans"].get(committed) if committed else None,
        "event_trace": store.trace(),
    }
    if pretty:
        print(f"\n=== relieffleet{' + second surge' if disrupt else ''} ===")
        for e in result["event_trace"]:
            detail = {k: v for k, v in e["payload"].items()
                      if k in ("truck_unsafe_hours", "heli_unsafe_hours",
                               "plan_id", "reason", "edge", "assignments",
                               "work_id", "current_claimant")}
            print(f"{e['seq']:>3}  {e['kind']:<24} {e['actor']:<20} "
                  f"{json.dumps(detail) if detail else ''}")
        print(f"     COMMITTED -> {committed}")
    else:
        print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Run the seeded ReliefFleet scenario end to end.")
    ap.add_argument("--disrupt", action="store_true",
                    help="apply the second surge: barrier-lake pulse plus a "
                         "failed bridge, after the first commit")
    ap.add_argument("--pretty", action="store_true",
                    help="human-readable event trace")
    a = ap.parse_args()
    run_one(disrupt=a.disrupt, pretty=a.pretty)
