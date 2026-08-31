"""Seismic observations: a second real-world evidence stream.

A significant earthquake near a post-flood corridor is evidence about slope
and bridge integrity, the way a forecast is evidence about wind and water.
It does not decide anything: it maps to edge failures under a declared
policy, the revision advances, and the verifier decides what survives —
the same membrane as every other disruption.

Two providers, same shape as the weather seam: a seeded mock as the
deterministic reference, and the USGS FDSN event service (public, no key)
as the live upgrade. The live provider fails loudly; an empty answer from a
failed request would read downstream as "no seismicity", which is exactly
the silent pass this system exists to refuse. USGS event ids make natural
content addresses for at-most-once application.
"""
from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Protocol


class SeismicProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeismicEvent:
    event_id: str
    magnitude: float
    latitude: float
    longitude: float
    depth_km: float
    time_iso: str


class SeismicProvider(Protocol):
    def recent(self, latitude: float, longitude: float, radius_km: float,
               min_magnitude: float) -> List[SeismicEvent]: ...


class MockSeismicProvider:
    """Seeded events; the deterministic reference lane."""

    name = "seismic-mock"

    def __init__(self, events: List[SeismicEvent]):
        self.events = list(events)

    def recent(self, latitude: float, longitude: float, radius_km: float,
               min_magnitude: float) -> List[SeismicEvent]:
        return [e for e in self.events if e.magnitude >= min_magnitude]


class USGSSeismicProvider:
    """USGS FDSN event service adapter. Public, no credential."""

    name = "seismic-usgs-live"
    ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/query"

    def __init__(self, lookback_hours: int = 24, timeout: float = 10.0):
        self.lookback_hours = lookback_hours
        self.timeout = timeout

    def query_params(self, latitude: float, longitude: float,
                     radius_km: float, min_magnitude: float) -> Dict[str, str]:
        """The exact FDSN query, exposed so a test can hold the adapter to
        its advertised window without touching the network. Without an
        explicit starttime USGS applies its own default (30 days), which
        would silently widen the lookback this provider claims."""
        start = datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)
        return {
            "format": "geojson",
            "latitude": f"{latitude}",
            "longitude": f"{longitude}",
            "maxradiuskm": f"{radius_km}",
            "minmagnitude": f"{min_magnitude}",
            "starttime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "orderby": "time",
        }

    def recent(self, latitude: float, longitude: float, radius_km: float,
               min_magnitude: float) -> List[SeismicEvent]:
        params = urllib.parse.urlencode(self.query_params(
            latitude, longitude, radius_km, min_magnitude))
        url = f"{self.ENDPOINT}?{params}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise SeismicProviderError(
                f"USGS returned {exc.code} for ({latitude},{longitude})") from exc
        except urllib.error.URLError as exc:
            raise SeismicProviderError(
                f"USGS unreachable: {exc.reason}") from exc
        events = []
        for feat in payload.get("features", []):
            props = feat.get("properties") or {}
            geom = (feat.get("geometry") or {}).get("coordinates") or [0, 0, 0]
            if props.get("mag") is None:
                continue
            events.append(SeismicEvent(
                event_id=str(feat.get("id")),
                magnitude=float(props["mag"]),
                latitude=float(geom[1]),
                longitude=float(geom[0]),
                depth_km=float(geom[2]),
                time_iso=str(props.get("time")),
            ))
        return events


def edge_alerts(events: List[SeismicEvent], f: dict) -> Dict[str, str]:
    """Declared policy, not judgment: a quake at or above the scenario's
    threshold marks the quake-sensitive edges suspect. Returns
    {edge: reason}; empty when nothing crossed the threshold. The verifier,
    not this function, decides what the marks do to the committed plan."""
    threshold = f.get("quake_alert_magnitude", 4.5)
    sensitive = f.get("quake_sensitive_edges", [])
    strongest = max((e for e in events if e.magnitude >= threshold),
                    key=lambda e: e.magnitude, default=None)
    if strongest is None:
        return {}
    return {edge: (f"M{strongest.magnitude:.1f} event {strongest.event_id} "
                   f"within corridor radius")
            for edge in sensitive}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Query recent seismicity "
                                             "(USGS live feed).")
    ap.add_argument("--lat", type=float, default=28.03)
    ap.add_argument("--lng", type=float, default=85.20)
    ap.add_argument("--radius-km", type=float, default=150.0)
    ap.add_argument("--min-magnitude", type=float, default=4.0)
    a = ap.parse_args()
    provider = USGSSeismicProvider()
    for e in provider.recent(a.lat, a.lng, a.radius_km, a.min_magnitude):
        print(f"{e.time_iso}  M{e.magnitude:<4}  "
              f"({e.latitude:.2f},{e.longitude:.2f})  depth {e.depth_km} km  "
              f"{e.event_id}")
