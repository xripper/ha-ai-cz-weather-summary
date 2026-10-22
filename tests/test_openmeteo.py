"""Tests for the Open-Meteo aggregation."""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.cz_weather_summary import openmeteo
from custom_components.cz_weather_summary.const import OPEN_METEO_URL
from custom_components.cz_weather_summary.openmeteo import (
    OpenMeteoError,
    async_get_point_forecast,
    summarize,
)

from .conftest import load_json


def test_summarize() -> None:
    """Hourly data is grouped into parts of the day."""
    result = summarize(load_json("openmeteo_praha.json"), "2026-10-18")
    assert result["teplota_max"] == 15.3
    assert result["teplota_min"] == 8.9
    assert result["vychod_slunce"] == "07:28"
    assert "uv_index_max" not in result  # all null in fixture
    parts = result["casti_dne"]
    assert list(parts) == ["rano", "dopoledne", "odpoledne", "vecer"]
    assert parts["odpoledne"]["teplota_max"] == 15.3
    assert parts["rano"]["pocasi"] == "zataženo"
    assert parts["vecer"]["pocasi"] in {"zataženo", "polojasno"}


def test_summarize_wrong_day() -> None:
    """Missing day yields no parts instead of failing."""
    result = summarize(load_json("openmeteo_praha.json"), "2026-10-19")
    assert result["casti_dne"] == {}
    assert "teplota_max" not in result


@pytest.fixture
def no_delay(monkeypatch) -> None:
    """Retry Open-Meteo immediately."""
    monkeypatch.setattr(openmeteo, "RETRY_DELAYS", (0, 0))


def _responses(aioclient_mock, *statuses: int) -> None:
    """Answer successive Open-Meteo requests with the given statuses."""
    remaining = list(statuses)

    async def _next(method, url, data):
        status = remaining.pop(0)
        return AiohttpClientMockResponse(
            method,
            url,
            status=status,
            json=load_json("openmeteo_praha.json") if status == 200 else {"reason": "overloaded"},
        )

    aioclient_mock.get(OPEN_METEO_URL, side_effect=_next)


async def test_overloaded_is_retried(hass, aioclient_mock, no_delay) -> None:
    """A 503 on the hour is retried until Open-Meteo answers."""
    _responses(aioclient_mock, 503, 503, 200)
    data = await async_get_point_forecast(async_get_clientsession(hass), 50.0, 14.4)
    assert data == load_json("openmeteo_praha.json")
    assert aioclient_mock.call_count == 3


async def test_retries_exhausted(hass, aioclient_mock, no_delay) -> None:
    """Persistent overload fails after the last retry."""
    _responses(aioclient_mock, 503, 429, 503)
    with pytest.raises(OpenMeteoError, match="HTTP 503"):
        await async_get_point_forecast(async_get_clientsession(hass), 50.0, 14.4)
    assert aioclient_mock.call_count == 3


async def test_client_error_not_retried(hass, aioclient_mock, no_delay) -> None:
    """A bad request fails at once."""
    _responses(aioclient_mock, 400)
    with pytest.raises(OpenMeteoError, match="HTTP 400"):
        await async_get_point_forecast(async_get_clientsession(hass), 50.0, 14.4)
    assert aioclient_mock.call_count == 1
