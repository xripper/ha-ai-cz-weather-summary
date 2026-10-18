"""Tests for the CHMI client."""

from __future__ import annotations

from datetime import datetime
import re
from zoneinfo import ZoneInfo

import pytest

from custom_components.cz_weather_summary.chmi import (
    PRODUCT_DAY,
    ChmiClient,
    ChmiError,
    _candidates,
    find_region,
    parse_forecast,
    parse_listing,
)
from custom_components.cz_weather_summary.const import CHMI_FORECAST_URL

from .conftest import load_json, load_text

TZ = ZoneInfo("Europe/Prague")


@pytest.mark.parametrize(
    ("lat", "lon", "region"),
    [
        (50.088, 14.42, "RPPH"),  # Praha
        (49.99, 14.65, "RPSC"),  # Říčany
        (49.195, 16.608, "RPJM"),  # Brno
        (49.82, 18.26, "RPMS"),  # Ostrava
        (48.975, 14.48, "RPCB"),  # České Budějovice
        (49.396, 15.59, "RPVY"),  # Jihlava
        (50.767, 15.056, "RPLB"),  # Liberec
        (50.224, 12.195, "RPKV"),  # Aš
        (48.2, 16.37, None),  # Vídeň
    ],
)
def test_find_region(lat: float, lon: float, region: str | None) -> None:
    """Coordinates map to the right CHMI region."""
    assert find_region(lat, lon) == region


def test_parse_listing() -> None:
    """Directory listing is parsed including corrections."""
    entries = parse_listing(load_text("listing.html"))
    assert len(entries) > 400
    cca = [e for e in entries if e.filename == "web_pCK0tx_RPJM_180500_CCA.json"]
    assert cca and cca[0].correction == "CCA" and cca[0].product == "CK0"
    assert cca[0].modified.tzinfo is not None


def test_candidates_order() -> None:
    """Newest files first, older than yesterday dropped."""
    entries = parse_listing(load_text("listing.html"))
    now = datetime(2026, 10, 18, 14, 0, tzinfo=TZ)
    names = [e.filename for e in _candidates(entries, "RPJM", PRODUCT_DAY, now)]
    assert names[0] == "web_pCK0tx_RPJM_181000.json"
    assert names.index("web_pCK0tx_RPJM_180500_CCA.json") < names.index(
        "web_pCK0tx_RPJM_180500.json"
    )
    assert not any("_16" in n for n in names)


def test_candidates_skip_future_files() -> None:
    """Files modified after 'now' (UTC listing times) are ignored."""
    entries = parse_listing(load_text("listing.html"))
    # 05:20 local = 03:20 UTC, before the 05:00 issue was uploaded at 03:32 UTC.
    now = datetime(2026, 10, 18, 5, 20, tzinfo=TZ)
    names = [e.filename for e in _candidates(entries, "RPPH", PRODUCT_DAY, now)]
    assert names[0] == "web_pCK1tx_RPPH_171700.json"


def test_parse_forecast() -> None:
    """Blocks are extracted and non-breaking spaces normalized."""
    forecast = parse_forecast(load_json("web_pCK0tx_RPPH_180500.json"), "CK0")
    assert forecast.blocks["textMaximumTemperature"] == "Nejvyšší teploty 14 až 16 °C."
    assert "\xa0" not in "".join(forecast.blocks.values())
    assert forecast.valid_from.astimezone(TZ).hour == 6
    assert forecast.as_dict()["platnost_do"] == "2026-10-18T22:00+02:00"


def test_parse_forecast_invalid() -> None:
    """Unknown format raises ChmiError."""
    with pytest.raises(ChmiError):
        parse_forecast({"data": {}})


def _mock_files(aioclient_mock, files: list[str]) -> None:
    aioclient_mock.get(CHMI_FORECAST_URL, text=load_text("listing.html"))
    listing = load_text("listing.html")
    for name in re.findall(r'href="(web_[^"]+\.json)"', listing):
        if name in files:
            aioclient_mock.get(CHMI_FORECAST_URL + name, text=load_text(name))
        else:
            aioclient_mock.get(CHMI_FORECAST_URL + name, status=404)


async def test_get_forecast_morning_uses_tomorrow_product(hass, aioclient_mock) -> None:
    """At 4:00, yesterday's 'tomorrow' forecast is today's best source."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    _mock_files(aioclient_mock, ["web_pCK1tx_RPPH_171700.json"])
    client = ChmiClient(async_get_clientsession(hass))
    result = await client.async_get_forecast("RPPH", datetime(2026, 10, 18, 4, 0, tzinfo=TZ))
    assert result.day is not None and result.day.product == "CK1"
    assert result.night is None
    assert result.region_name == "Praha"


async def test_get_forecast_prefers_latest_today(hass, aioclient_mock) -> None:
    """The 10:00 issue wins over the 05:00 one."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    _mock_files(
        aioclient_mock,
        [
            "web_pCK0tx_RPPH_181000.json",
            "web_pCK0tx_RPPH_180500.json",
            "web_pCKntx_RPPH_181000.json",
        ],
    )
    client = ChmiClient(async_get_clientsession(hass))
    result = await client.async_get_forecast("RPPH", datetime(2026, 10, 18, 12, 0, tzinfo=TZ))
    assert result.day.product == "CK0"
    assert result.day.valid_from.astimezone(TZ).hour == 12
    assert result.night is not None and result.night.product == "CKn"


async def test_get_forecast_nothing(hass, aioclient_mock) -> None:
    """No usable file raises."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    _mock_files(aioclient_mock, [])
    client = ChmiClient(async_get_clientsession(hass))
    with pytest.raises(ChmiError):
        await client.async_get_forecast("RPPH", datetime(2026, 10, 18, 12, 0, tzinfo=TZ))


async def test_winter_night_starting_after_midnight(hass, aioclient_mock) -> None:
    """A night forecast starting at 00:00 local still counts as tonight."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    night = load_json("web_pCKntx_RPPH_181000.json")
    for block in night["data"]["features"][0]["properties"]["data"]:
        block["startTime"] = "2026-10-18T22:00:00.000Z"  # 00:00 local on the 19th
    aioclient_mock.get(CHMI_FORECAST_URL, text=load_text("listing.html"))
    aioclient_mock.get(CHMI_FORECAST_URL + "web_pCKntx_RPPH_181000.json", json=night)
    aioclient_mock.get(CHMI_FORECAST_URL + "web_pCK0tx_RPPH_181000.json", text=load_text("web_pCK0tx_RPPH_181000.json"))
    client = ChmiClient(async_get_clientsession(hass))
    result = await client.async_get_forecast("RPPH", datetime(2026, 10, 18, 12, 0, tzinfo=TZ))
    assert result.night is not None


def test_parse_forecast_bad_blocks() -> None:
    """Malformed blocks raise ChmiError instead of crashing."""
    payload = load_json("web_pCK0tx_RPPH_180500.json")
    payload["data"]["features"][0]["properties"]["data"] = ["oops"]
    with pytest.raises(ChmiError):
        parse_forecast(payload)
