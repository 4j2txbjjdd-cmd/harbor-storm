"""Startup wiring: every advertised remedy must actually work.

The weather provider's error text tells the operator to set WEATHER_PROVIDER=mock.
An error message that hands out a remedy which does nothing is worse than no
message, so the remedy is tested here, not just documented.
"""
import pytest

from app.config import ConfigError, Settings, make_store, make_weather
from app.providers.weather import (GoogleWeatherProvider, MockWeatherProvider,
                                   WeatherProviderError)


def clear(monkeypatch):
    for var in ("STATE_BACKEND", "WEATHER_PROVIDER", "GOOGLE_WEATHER_API_KEY",
                "GOOGLE_MAPS_API_KEY", "GOOGLE_CLOUD_PROJECT", "GCP_PROJECT",
                "FORECAST_HOURS", "SITES_JSON"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_are_the_deterministic_path(monkeypatch):
    clear(monkeypatch)
    s = Settings.from_env()
    assert s.state_backend == "memory" and s.weather_provider == "mock"
    assert s.describe()["deterministic_replay"] is True
    assert isinstance(make_weather(s), MockWeatherProvider)


def test_explicit_mock_opt_in_works_without_a_key(monkeypatch):
    """The remedy the provider error advertises."""
    clear(monkeypatch)
    monkeypatch.setenv("WEATHER_PROVIDER", "mock")
    assert isinstance(make_weather(Settings.from_env()), MockWeatherProvider)


def test_live_weather_without_a_key_refuses_at_startup(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("WEATHER_PROVIDER", "google")
    with pytest.raises(ConfigError, match="GOOGLE_WEATHER_API_KEY"):
        Settings.from_env()


def test_live_weather_with_a_key_selects_the_google_provider(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("WEATHER_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_WEATHER_API_KEY", "test-key")
    s = Settings.from_env()
    assert s.is_live and s.describe()["deterministic_replay"] is False
    assert isinstance(make_weather(s), GoogleWeatherProvider)


def test_firestore_backend_without_a_project_refuses(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "firestore")
    with pytest.raises(ConfigError, match="GOOGLE_CLOUD_PROJECT"):
        Settings.from_env()


def test_unknown_values_are_rejected_not_coerced(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("STATE_BACKEND", "sqlite")
    with pytest.raises(ConfigError, match="memory|firestore"):
        Settings.from_env()
    monkeypatch.setenv("STATE_BACKEND", "memory")
    monkeypatch.setenv("WEATHER_PROVIDER", "openweather")
    with pytest.raises(ConfigError, match="mock|google"):
        Settings.from_env()


def test_unknown_seeded_profile_is_rejected(monkeypatch):
    clear(monkeypatch)
    with pytest.raises(ConfigError, match="baseline|disrupted"):
        make_weather(Settings.from_env(), seeded="sunny")


def test_bad_forecast_hours_is_rejected(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("FORECAST_HOURS", "lots")
    with pytest.raises(ConfigError, match="FORECAST_HOURS"):
        Settings.from_env()
