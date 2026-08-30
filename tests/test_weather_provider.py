"""GoogleWeatherProvider translation and failure behaviour, exercised offline."""
import json

import pytest

from app.providers.weather import (GoogleWeatherProvider, MissingWeatherData,
                                   WeatherProviderError)

# Shape of a Google Weather API forecast/hours:lookup response.
SAMPLE = {
    "forecastHours": [
        {
            "interval": {"startTime": "2026-08-27T13:00:00Z"},
            "displayDateTime": {"hours": 15, "utcOffset": "7200s"},
            "wind": {"speed": {"value": 22.0, "unit": "KILOMETERS_PER_HOUR"}},
            "precipitation": {"qpf": {"quantity": 8.0, "unit": "MILLIMETERS"}},
            "thunderstormProbability": 10,
        },
        {
            "interval": {"startTime": "2026-08-27T14:00:00Z"},
            "displayDateTime": {"hours": 16, "utcOffset": "7200s"},
            "wind": {"speed": {"value": 24.0, "unit": "MILES_PER_HOUR"}},
            "precipitation": {"qpf": {"quantity": 1.2, "unit": "INCHES"}},
            "thunderstormProbability": 70,
        },
        # duplicate clock hour, must not produce a second point
        {
            "interval": {"startTime": "2026-08-28T14:00:00Z"},
            "displayDateTime": {"hours": 16, "utcOffset": "7200s"},
            "wind": {"speed": {"value": 3.0, "unit": "KILOMETERS_PER_HOUR"}},
            "precipitation": {"qpf": {"quantity": 0.0, "unit": "MILLIMETERS"}},
        },
    ]
}


def provider(monkeypatch, **kw):
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "test-key")
    return GoogleWeatherProvider(**kw)


def test_missing_api_key_refuses_to_construct(monkeypatch):
    monkeypatch.delenv("GOOGLE_WEATHER_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)
    with pytest.raises(WeatherProviderError, match="GOOGLE_WEATHER_API_KEY"):
        GoogleWeatherProvider()


def test_translation_normalises_units_and_clock_hours(monkeypatch):
    points = provider(monkeypatch).translate(SAMPLE)
    assert [p.hour for p in points] == [15, 16]
    assert points[0].wind_kph == 22.0 and points[0].rain_mm == 8.0
    # 24 mph -> kph, 1.2 in -> mm
    assert points[1].wind_kph == pytest.approx(38.62, abs=0.01)
    assert points[1].rain_mm == pytest.approx(30.48, abs=0.01)
    assert points[1].thunder_prob == 70.0


def test_severe_hours_survive_the_swap(monkeypatch):
    """The translated series must drive the same verifier decision as the mock."""
    from app.scenarios.stormslot import severe_hours
    points = provider(monkeypatch).translate(SAMPLE)
    assert severe_hours(points, 25.0) == [16]


def test_unknown_site_raises_with_the_known_list(monkeypatch):
    p = provider(monkeypatch)
    with pytest.raises(MissingWeatherData, match="SITES_JSON"):
        p.hourly("ATLANTIS")


def test_empty_forecast_is_an_error_not_fair_weather(monkeypatch):
    p = provider(monkeypatch)
    monkeypatch.setattr(p, "_fetch", lambda lat, lng: ({"forecastHours": []}, 200))
    with pytest.raises(WeatherProviderError, match="empty forecast"):
        p.hourly("PORT_A")


def test_http_failure_propagates(monkeypatch):
    p = provider(monkeypatch)

    def boom(lat, lng):
        raise WeatherProviderError("Google Weather API returned 403")

    monkeypatch.setattr(p, "_fetch", boom)
    with pytest.raises(WeatherProviderError, match="403"):
        p.hourly("PORT_A")


def test_sites_json_override(monkeypatch):
    monkeypatch.setenv("SITES_JSON", '{"PORT_X": {"lat": 1.5, "lng": 2.5}}')
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "test-key")
    p = GoogleWeatherProvider()
    assert p.sites == {"PORT_X": (1.5, 2.5)}


# --- provenance ------------------------------------------------------
#
# These are offline. They prove the provider *records* what it did; they do not
# prove it did anything live. That is `app.live_gate --provider live`, which
# needs a network and a key and is run by hand.

def test_provenance_is_empty_until_something_is_fetched(monkeypatch):
    assert provider(monkeypatch).provenance() == []


def test_a_fetch_records_status_site_and_shape(monkeypatch):
    p = provider(monkeypatch)
    monkeypatch.setattr(p, "_fetch", lambda lat, lng: (SAMPLE, 200))
    p.hourly("PORT_A")
    (rec,) = p.provenance()
    assert rec["site"] == "PORT_A"
    assert (rec["latitude"], rec["longitude"]) == (51.9497, 4.1399)
    assert rec["http_status"] == 200
    assert rec["points_returned"] == 2
    assert rec["first_interval_start"] == "2026-08-27T13:00:00Z"
    assert rec["last_interval_start"] == "2026-08-28T14:00:00Z"
    assert rec["requested_at"].endswith("+00:00")


def test_provenance_never_carries_the_key(monkeypatch):
    """The key is a query parameter, so the URL is a secret. Nothing that gets
    printed, serialised or pasted into a PR may contain it."""
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "super-secret-key-value")
    p = GoogleWeatherProvider()
    monkeypatch.setattr(p, "_fetch", lambda lat, lng: (SAMPLE, 200))
    p.hourly("PORT_A")
    blob = json.dumps(p.provenance())
    assert "super-secret-key-value" not in blob
    assert "key=" not in blob


def test_a_failed_fetch_records_no_provenance(monkeypatch):
    """A blocked live run must not leave evidence that looks like a live run."""
    p = provider(monkeypatch)

    def boom(lat, lng):
        raise WeatherProviderError("Google Weather API returned 400")

    monkeypatch.setattr(p, "_fetch", boom)
    with pytest.raises(WeatherProviderError):
        p.hourly("PORT_A")
    assert p.provenance() == []


# --- the clock-hour horizon ------------------------------------------

@pytest.mark.parametrize("hours", [0, -1, 25, 48, 240])
def test_a_horizon_that_cannot_be_represented_is_refused(monkeypatch, hours):
    """One point per clock hour means 24 hours is the whole domain. Asking for
    more would be silently truncated in translate(), and the run would describe
    less weather than it claimed."""
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "test-key")
    with pytest.raises(WeatherProviderError, match="outside 1..24"):
        GoogleWeatherProvider(hours=hours)


@pytest.mark.parametrize("hours", [1, 12, 24])
def test_a_representable_horizon_is_accepted(monkeypatch, hours):
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "test-key")
    assert GoogleWeatherProvider(hours=hours).hours == hours


# --- lane identity ----------------------------------------------------

def test_each_provider_names_its_lane(monkeypatch):
    from app.providers.weather import MockWeatherProvider
    assert MockWeatherProvider({}).name == "seeded-fixture"
    assert provider(monkeypatch).name == "google-weather-live"
