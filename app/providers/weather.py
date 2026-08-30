from __future__ import annotations
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True)
class WeatherPoint:
    hour: int
    wind_kph: float
    rain_mm: float
    thunder_prob: float = 0.0


class WeatherProvider(Protocol):
    # Which lane produced these observations. On the boundary rather than
    # bolted on afterwards: "was this run live?" is a question about the
    # provider, and a caller that cannot ask it has to infer the answer from
    # configuration it may not have been given.
    name: str

    def hourly(self, location: str) -> List[WeatherPoint]:
        ...


class MissingWeatherData(KeyError):
    """A location was asked for that this provider cannot resolve."""


class WeatherProviderError(RuntimeError):
    """The upstream weather service failed or returned something unusable."""


class MockWeatherProvider:
    """Deterministic seeded forecast. No fallback: an unseeded location is an error."""

    name = "seeded-fixture"

    def __init__(self, series: Dict[str, List[WeatherPoint]]):
        self.series = series

    def hourly(self, location: str) -> List[WeatherPoint]:
        if location not in self.series:
            raise MissingWeatherData(
                f"no seeded forecast for {location!r}; seeded: {sorted(self.series)}"
            )
        return list(self.series[location])


# Symbolic site names used by the scenarios, resolved to real coordinates.
# Override per deployment via SITES_JSON.
DEFAULT_SITES: Dict[str, Tuple[float, float]] = {
    # Port of Rotterdam / inland warehouse pair (StormSlot)
    "PORT_A": (51.9497, 4.1399),
    "WH_A": (51.9225, 4.4792),
    # Piraeus harbour / Aegina island pair (HarborWindow)
    "HARBOR_A": (37.9420, 23.6465),
    "ISLAND_B": (37.7450, 23.4290),
}


def load_sites() -> Dict[str, Tuple[float, float]]:
    raw = os.environ.get("SITES_JSON")
    if not raw:
        return dict(DEFAULT_SITES)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WeatherProviderError(f"SITES_JSON is not valid JSON: {exc}") from exc
    return {k: (float(v["lat"]), float(v["lng"])) for k, v in data.items()}


@dataclass(frozen=True)
class FetchRecord:
    """Evidence that one forecast came off the wire, not out of a fixture.

    Deliberately not an event. Weather is an external fact source, so where a
    fact came from is provenance about the input, not a transition in the
    operation -- putting HTTP status codes into the authoritative vocabulary
    would make the trace argue about its own plumbing. The live gate reads
    this from the provider and reports it alongside the run.

    Carries nothing secret: no key, no signed URL. `endpoint` is the bare path.
    """

    site: str
    latitude: float
    longitude: float
    endpoint: str
    requested_at: str
    http_status: int
    hours_requested: int
    points_returned: int
    time_zone: Optional[str] = None
    first_interval_start: Optional[str] = None
    last_interval_start: Optional[str] = None

    def as_dict(self) -> Dict[str, object]:
        return {
            "site": self.site, "latitude": self.latitude,
            "longitude": self.longitude, "endpoint": self.endpoint,
            "requested_at": self.requested_at, "http_status": self.http_status,
            "hours_requested": self.hours_requested,
            "points_returned": self.points_returned,
            "time_zone": self.time_zone,
            "first_interval_start": self.first_interval_start,
            "last_interval_start": self.last_interval_start,
        }


# One WeatherPoint per clock hour is the shape every consumer downstream is
# written against, so 24 hours is the whole domain. Asking for more cannot
# produce more points; it would silently discard the surplus in `translate`,
# and one page of the API holds exactly 24 anyway.
MAX_FORECAST_HOURS = 24


class GoogleWeatherProvider:
    """Google Weather API adapter.

    Translates `forecast/hours:lookup` into the same WeatherPoint list the
    mock produces, so scenario and verifier logic is untouched by the swap.

    Fails loudly and early: no API key, an unknown site, an HTTP error or an
    empty forecast all raise. Nothing here degrades to an empty series, because
    an empty forecast reads downstream as "no severe weather" and would silently
    approve exactly the plan this system exists to reject.

    Every successful fetch appends a `FetchRecord`. That is the only way a
    caller can prove afterwards that a run was live, so it is recorded by the
    provider itself rather than by whoever remembered to instrument the call.
    """

    name = "google-weather-live"
    ENDPOINT = "https://weather.googleapis.com/v1/forecast/hours:lookup"

    def __init__(self, api_key: Optional[str] = None, hours: int = 24,
                 sites: Optional[Dict[str, Tuple[float, float]]] = None,
                 timeout: float = 10.0):
        key = api_key or os.environ.get("GOOGLE_WEATHER_API_KEY") or os.environ.get("GOOGLE_MAPS_API_KEY")
        if not key:
            raise WeatherProviderError(
                "GOOGLE_WEATHER_API_KEY is not set. Set it, or select the seeded "
                "provider explicitly with WEATHER_PROVIDER=mock (read by "
                "app.config.Settings, which is how the CLI and the API choose a "
                "provider). Refusing to start with no weather source."
            )
        if hours < 1 or hours > MAX_FORECAST_HOURS:
            raise WeatherProviderError(
                f"hours={hours} is outside 1..{MAX_FORECAST_HOURS}. Downstream "
                f"holds one point per clock hour, so a longer horizon cannot "
                f"produce more points -- it would be truncated in translate() "
                f"and the run would quietly describe less weather than it "
                f"claimed. Narrow the horizon, or change the point model first."
            )
        self.api_key = key
        self.hours = hours
        self.sites = sites if sites is not None else load_sites()
        self.timeout = timeout
        self._cache: Dict[str, List[WeatherPoint]] = {}
        self._fetches: List[FetchRecord] = []

    # --- translation ----------------------------------------------

    @staticmethod
    def _local_hour(entry: dict) -> int:
        display = entry.get("displayDateTime")
        if isinstance(display, dict) and "hours" in display:
            return int(display["hours"])
        start = (entry.get("interval") or {}).get("startTime")
        if not start:
            raise WeatherProviderError(f"forecast hour has no timestamp: {entry}")
        return datetime.fromisoformat(start.replace("Z", "+00:00")).hour

    @staticmethod
    def _wind_kph(entry: dict) -> float:
        wind = (entry.get("wind") or {}).get("speed") or {}
        value = wind.get("value")
        if value is None:
            return 0.0
        unit = (wind.get("unit") or "KILOMETERS_PER_HOUR").upper()
        if unit.startswith("MILES"):
            return round(float(value) * 1.609344, 2)
        return float(value)

    @staticmethod
    def _rain_mm(entry: dict) -> float:
        qpf = ((entry.get("precipitation") or {}).get("qpf")) or {}
        value = qpf.get("quantity")
        if value is None:
            return 0.0
        unit = (qpf.get("unit") or "MILLIMETERS").upper()
        if unit.startswith("INCH"):
            return round(float(value) * 25.4, 2)
        return float(value)

    def translate(self, payload: dict) -> List[WeatherPoint]:
        entries = payload.get("forecastHours") or []
        points = [
            WeatherPoint(
                hour=self._local_hour(e),
                wind_kph=self._wind_kph(e),
                rain_mm=self._rain_mm(e),
                thunder_prob=float(e.get("thunderstormProbability") or 0.0),
            )
            for e in entries
        ]
        # One point per clock hour; the first occurrence wins.
        seen, deduped = set(), []
        for p in points:
            if p.hour not in seen:
                seen.add(p.hour)
                deduped.append(p)
        return deduped

    # --- transport -------------------------------------------------

    def _fetch(self, lat: float, lng: float) -> Tuple[dict, int]:
        """Return (payload, http_status).

        The status is returned rather than discarded because it is half the
        provenance: a caller proving a run was live needs to say what the wire
        actually answered, and reconstructing that afterwards is guesswork.

        The key is a query parameter, so the URL is a secret. It is never put
        into an exception message, a log line or a FetchRecord -- the failure
        text names the coordinates, which are not.
        """
        params = {
            "key": self.api_key,
            "location.latitude": f"{lat}",
            "location.longitude": f"{lng}",
            "hours": str(self.hours),
            "pageSize": str(min(self.hours, MAX_FORECAST_HOURS)),
        }
        url = f"{self.ENDPOINT}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                return json.loads(resp.read().decode("utf-8")), int(status)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise WeatherProviderError(
                f"Google Weather API returned {exc.code} for ({lat},{lng}): {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise WeatherProviderError(
                f"Google Weather API unreachable for ({lat},{lng}): {exc.reason}"
            ) from exc

    @staticmethod
    def _interval_bounds(payload: dict) -> Tuple[Optional[str], Optional[str]]:
        starts = [(e.get("interval") or {}).get("startTime")
                  for e in (payload.get("forecastHours") or [])]
        starts = [s for s in starts if s]
        return (starts[0], starts[-1]) if starts else (None, None)

    def hourly(self, location: str) -> List[WeatherPoint]:
        if location in self._cache:
            return list(self._cache[location])
        if location not in self.sites:
            raise MissingWeatherData(
                f"no coordinates for site {location!r}; known sites: {sorted(self.sites)}. "
                f"Add it to SITES_JSON."
            )
        lat, lng = self.sites[location]
        requested_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload, status = self._fetch(lat, lng)
        points = self.translate(payload)
        if not points:
            raise WeatherProviderError(
                f"Google Weather API returned an empty forecast for {location!r}. "
                f"Refusing to treat an empty forecast as fair weather."
            )
        first, last = self._interval_bounds(payload)
        self._fetches.append(FetchRecord(
            site=location, latitude=lat, longitude=lng, endpoint=self.ENDPOINT,
            requested_at=requested_at, http_status=status,
            hours_requested=self.hours, points_returned=len(points),
            time_zone=(payload.get("timeZone") or {}).get("id"),
            first_interval_start=first, last_interval_start=last,
        ))
        self._cache[location] = points
        return list(points)

    # --- provenance ------------------------------------------------

    def provenance(self) -> List[Dict[str, object]]:
        """Every fetch this provider actually made, in order.

        Empty means no request left the process. A live claim resting on an
        empty provenance list is a claim about nothing, and callers are
        expected to treat it that way.
        """
        return [f.as_dict() for f in self._fetches]
