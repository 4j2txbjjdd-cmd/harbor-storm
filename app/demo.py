from __future__ import annotations
import argparse
import json

from app.core.store import InMemoryStateStore
from app.providers.weather import MockWeatherProvider, WeatherPoint
from app.providers.routes import MockRouteProvider, RouteEstimate
from app.scenarios import stormslot, harborwindow

SCENARIOS = ("stormslot", "harborwindow")


def weather_fixture() -> MockWeatherProvider:
    """Seeded baseline forecast. Storm sits over the port at 16:00-17:00."""
    return MockWeatherProvider({
        "PORT_A": [
            WeatherPoint(13, wind_kph=15, rain_mm=2),
            WeatherPoint(14, wind_kph=18, rain_mm=3),
            WeatherPoint(15, wind_kph=22, rain_mm=8),
            WeatherPoint(16, wind_kph=38, rain_mm=30),
            WeatherPoint(17, wind_kph=44, rain_mm=38),
            WeatherPoint(18, wind_kph=28, rain_mm=12),
        ],
        "HARBOR_A": [
            WeatherPoint(12, wind_kph=42, rain_mm=18),
            WeatherPoint(13, wind_kph=38, rain_mm=12),
            WeatherPoint(14, wind_kph=29, rain_mm=8),
            WeatherPoint(15, wind_kph=31, rain_mm=10),
            WeatherPoint(16, wind_kph=39, rain_mm=24),
            WeatherPoint(17, wind_kph=45, rain_mm=30),
        ],
        "ISLAND_B": [
            WeatherPoint(12, wind_kph=40, rain_mm=14),
            WeatherPoint(13, wind_kph=37, rain_mm=10),
            WeatherPoint(14, wind_kph=30, rain_mm=6),
            WeatherPoint(15, wind_kph=33, rain_mm=9),
            WeatherPoint(16, wind_kph=41, rain_mm=25),
            WeatherPoint(17, wind_kph=48, rain_mm=32),
        ],
    })


def disrupted_weather_fixture() -> MockWeatherProvider:
    """The storm arrives two hours early, invalidating whatever was committed."""
    return MockWeatherProvider({
        "PORT_A": [
            WeatherPoint(13, wind_kph=20, rain_mm=6),
            WeatherPoint(14, wind_kph=41, rain_mm=33),
            WeatherPoint(15, wind_kph=46, rain_mm=40),
            WeatherPoint(16, wind_kph=39, rain_mm=31),
            WeatherPoint(17, wind_kph=30, rain_mm=14),
            WeatherPoint(18, wind_kph=24, rain_mm=8),
        ],
        "HARBOR_A": [
            WeatherPoint(12, wind_kph=42, rain_mm=18),
            WeatherPoint(13, wind_kph=40, rain_mm=15),
            WeatherPoint(14, wind_kph=44, rain_mm=26),
            WeatherPoint(15, wind_kph=36, rain_mm=19),
            WeatherPoint(16, wind_kph=27, rain_mm=7),
            WeatherPoint(17, wind_kph=25, rain_mm=5),
        ],
        "ISLAND_B": [
            WeatherPoint(12, wind_kph=40, rain_mm=14),
            WeatherPoint(13, wind_kph=39, rain_mm=13),
            WeatherPoint(14, wind_kph=43, rain_mm=28),
            WeatherPoint(15, wind_kph=34, rain_mm=16),
            WeatherPoint(16, wind_kph=26, rain_mm=6),
            WeatherPoint(17, wind_kph=24, rain_mm=4),
        ],
    })


def route_fixture() -> MockRouteProvider:
    """Traffic-aware truck estimates, seeded for every port handover hour."""
    return MockRouteProvider({
        ("PORT_A", "WH_A", 13): RouteEstimate(minutes=85, distance_km=61.0),
        ("PORT_A", "WH_A", 14): RouteEstimate(minutes=88, distance_km=61.0),
        ("PORT_A", "WH_A", 15): RouteEstimate(minutes=95, distance_km=61.0),
        ("PORT_A", "WH_A", 16): RouteEstimate(minutes=130, distance_km=61.0),
        ("PORT_A", "WH_A", 17): RouteEstimate(minutes=110, distance_km=61.0),
    })


def new_store(name: str) -> InMemoryStateStore:
    if name == "stormslot":
        return InMemoryStateStore(stormslot.build_state())
    if name == "harborwindow":
        return InMemoryStateStore(harborwindow.build_state())
    raise SystemExit(f"unknown scenario {name!r}; expected one of {SCENARIOS}")


def run_scenario(name: str, store: InMemoryStateStore, weather) -> dict:
    if name == "stormslot":
        return stormslot.run(store, weather, route_fixture())
    return harborwindow.run(store, weather)


def disrupt_scenario(name: str, store: InMemoryStateStore, weather) -> dict:
    if name == "stormslot":
        return stormslot.disrupt(store, weather, route_fixture())
    return harborwindow.disrupt(store, weather)


def run_one(name: str, disrupt: bool = False, pretty: bool = False) -> dict:
    store = new_store(name)
    snap = run_scenario(name, store, weather_fixture())
    if disrupt:
        snap = disrupt_scenario(name, store, disrupted_weather_fixture())

    committed = snap["committed_plan_id"]
    result = {
        "scenario": name,
        "committed_plan": committed,
        "plan": snap["plans"].get(committed) if committed else None,
        "event_trace": store.trace(),
    }
    if pretty:
        print(f"\n=== {name}{' + disruption' if disrupt else ''} ===")
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
    ap = argparse.ArgumentParser(description="Run a seeded scenario end to end.")
    ap.add_argument("scenario", nargs="?", default="stormslot", choices=SCENARIOS)
    ap.add_argument("--disrupt", action="store_true",
                    help="apply a mid-flight forecast change after the first commit")
    ap.add_argument("--pretty", action="store_true", help="human-readable event trace")
    a = ap.parse_args()
    run_one(a.scenario, disrupt=a.disrupt, pretty=a.pretty)
