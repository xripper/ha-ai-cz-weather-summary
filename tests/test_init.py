"""Tests for setup, coordinator and sensor."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.cz_weather_summary.chmi import ChmiError, RegionForecast, parse_forecast
from custom_components.cz_weather_summary.const import (
    CONF_API_KEY,
    CONF_LOCATIONS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_UPDATE_TIME,
    DEFAULT_PROMPT,
    DOMAIN,
)
from custom_components.cz_weather_summary.coordinator import build_llm_input, options_hash
from custom_components.cz_weather_summary.llm import OpenAIAuthError, OpenAIError
from custom_components.cz_weather_summary.openmeteo import OpenMeteoError

from .conftest import load_json

TZ = ZoneInfo("Europe/Prague")
BASE = "custom_components.cz_weather_summary.coordinator"
SUMMARY = {"short": "Zataženo, odpoledne 15 °C.", "summary": "Celý souhrn.", "clothing": "Mikina."}


def _region_forecast() -> RegionForecast:
    return RegionForecast(
        region="RPPH",
        day=parse_forecast(load_json("web_pCK0tx_RPPH_180500.json"), "CK0"),
        night=parse_forecast(load_json("web_pCKntx_RPPH_181000.json"), "CKn"),
    )


@pytest.fixture
async def entry(hass) -> MockConfigEntry:
    """A configured entry with two locations."""
    await hass.config.async_set_time_zone("Europe/Prague")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Souhrn počasí",
        data={CONF_API_KEY: "sk"},
        options={
            CONF_MODEL: "gpt-5-mini",
            CONF_UPDATE_TIME: "06:00:00",
            CONF_PROMPT: DEFAULT_PROMPT,
            CONF_LOCATIONS: [
                {"name": "Doma", "latitude": 50.088, "longitude": 14.42, "region": "auto"},
                {"name": "Vídeň", "latitude": 48.2, "longitude": 16.37, "region": "auto"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def sources():
    """Patch weather sources and the LLM."""
    with (
        patch(
            f"{BASE}.async_get_point_forecast",
            AsyncMock(return_value=load_json("openmeteo_praha.json")),
        ) as point,
        patch(
            f"{BASE}.ChmiClient.async_get_forecast",
            AsyncMock(return_value=_region_forecast()),
        ) as chmi,
        patch(f"{BASE}.OpenAIClient.async_summarize", AsyncMock(return_value=SUMMARY)) as llm,
    ):
        yield point, chmi, llm


async def _setup(hass, entry) -> str:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(hass.states.async_entity_ids("sensor")) == 3
    return _entity_id(hass, entry, "short")


def _entity_id(hass, entry, suffix: str) -> str:
    """Entity id of a sensor by its key."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert entity_id
    return entity_id


async def test_three_sensors(hass, entry, sources, freezer) -> None:
    """Short summary, full summary and clothing advice are separate sensors."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    await _setup(hass, entry)
    full = hass.states.get(_entity_id(hass, entry, "summary"))
    clothing = hass.states.get(_entity_id(hass, entry, "clothing"))
    assert full.state == "Celý souhrn."
    assert full.attributes["text"] == "Celý souhrn."
    assert clothing.state == "Mikina."
    assert "locations" not in clothing.attributes


async def test_long_text_truncated(hass, entry, sources, freezer) -> None:
    """States are capped at 255 characters; the full text stays in the attribute."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    _, _, llm = sources
    long_text = "Slunečno. " * 60
    llm.return_value = {**SUMMARY, "summary": long_text}
    await _setup(hass, entry)
    full = hass.states.get(_entity_id(hass, entry, "summary"))
    assert len(full.state) == 255
    assert full.attributes["text"] == long_text


async def test_setup_generates_summary(hass, entry, sources, freezer) -> None:
    """First setup generates a summary and exposes it on the sensor."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    point, chmi, llm = sources
    entity_id = await _setup(hass, entry)

    state = hass.states.get(entity_id)
    assert state.state == SUMMARY["short"]
    assert state.attributes["summary"] == "Celý souhrn."
    assert state.attributes["clothing"] == "Mikina."
    assert state.attributes["last_error"] is None
    locations = state.attributes["locations"]
    assert locations[0]["region"] == "Praha"
    assert locations[0]["temp_max"] == 15.3
    assert locations[0]["forecast"] == "Zataženo, později ubývání oblačnosti"
    assert "region" not in locations[1]  # outside Czechia, ALADIN only
    assert chmi.await_count == 1  # only for the Czech location
    assert point.await_count == 2

    payload = llm.await_args.args[2]
    assert payload["dnes"] == "neděle 18. 10. 2026"
    assert payload["mista"][0]["predpoved_chmu"]["kraj"] == "Praha"
    assert "predpoved_chmu" not in payload["mista"][1]


async def test_restart_reuses_stored_summary(hass, entry, sources, freezer, hass_storage) -> None:
    """A summary generated after today's run time is not regenerated on startup."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {
            **SUMMARY,
            "generated_at": "2026-10-18T06:00:05+02:00",
            "locations": [],
            "options_hash": options_hash(entry.options),
        },
    }
    _, _, llm = sources
    entity_id = await _setup(hass, entry)
    assert llm.await_count == 0
    assert hass.states.get(entity_id).state == SUMMARY["short"]


async def test_scheduled_refresh(hass, entry, sources, freezer, hass_storage) -> None:
    """The summary is regenerated at the configured time."""
    freezer.move_to(datetime(2026, 10, 18, 5, 0, tzinfo=TZ))
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {
            **SUMMARY,
            "generated_at": "2026-10-17T06:00:05+02:00",
            "locations": [],
            "options_hash": options_hash(entry.options),
        },
    }
    _, _, llm = sources
    await _setup(hass, entry)
    assert llm.await_count == 0

    freezer.move_to(datetime(2026, 10, 18, 6, 0, 0, tzinfo=TZ))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert llm.await_count == 1


async def test_failure_keeps_previous(hass, entry, sources, freezer) -> None:
    """LLM failure keeps the old summary and reports last_error."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    _, _, llm = sources
    entity_id = await _setup(hass, entry)

    llm.side_effect = OpenAIError("boom")
    with pytest.raises(HomeAssistantError, match="boom"):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)
    state = hass.states.get(entity_id)
    assert state.state == SUMMARY["short"]
    assert state.attributes["last_error"] == "boom"



async def test_failed_scheduled_run_is_retried(hass, entry, sources, freezer, hass_storage) -> None:
    """A failed scheduled run is retried later until it succeeds."""
    freezer.move_to(datetime(2026, 10, 18, 5, 59, 0, tzinfo=TZ))
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {
            **SUMMARY,
            "generated_at": "2026-10-17T06:00:05+02:00",
            "locations": [],
            "options_hash": options_hash(entry.options),
        },
    }
    _, _, llm = sources
    entity_id = await _setup(hass, entry)
    llm.side_effect = OpenAIError("rate limit")

    freezer.move_to(datetime(2026, 10, 18, 6, 0, 0, tzinfo=TZ))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert llm.await_count == 1
    assert hass.states.get(entity_id).attributes["last_error"] == "rate limit"

    llm.side_effect = None
    freezer.tick(timedelta(minutes=31))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert llm.await_count == 2
    assert hass.states.get(entity_id).attributes["last_error"] is None


async def test_options_change_regenerates(hass, entry, sources, freezer, hass_storage) -> None:
    """A stored summary made with different options is regenerated on setup."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "data": {
            **SUMMARY,
            "generated_at": "2026-10-18T06:00:05+02:00",
            "locations": [],
            "options_hash": "something-else",
        },
    }
    _, _, llm = sources
    await _setup(hass, entry)
    assert llm.await_count == 1


async def test_same_region_fetched_once(hass, sources, freezer) -> None:
    """Two locations in one region share the CHMI download."""
    await hass.config.async_set_time_zone("Europe/Prague")
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "sk"},
        options={
            CONF_MODEL: "gpt-5-mini",
            CONF_UPDATE_TIME: "06:00:00",
            CONF_LOCATIONS: [
                {"name": "A", "latitude": 50.088, "longitude": 14.42, "region": "auto"},
                {"name": "B", "latitude": 50.05, "longitude": 14.45, "region": "RPPH"},
            ],
        },
    )
    entry.add_to_hass(hass)
    _, chmi, llm = sources
    await _setup(hass, entry)
    assert chmi.await_count == 1
    assert llm.await_count == 1


async def test_partial_source_failure(hass, entry, sources, freezer) -> None:
    """CHMI failure still produces a summary from ALADIN data."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    _, chmi, _ = sources
    chmi.side_effect = ChmiError("down")
    entity_id = await _setup(hass, entry)
    state = hass.states.get(entity_id)
    assert state.state == SUMMARY["short"]
    assert "down" in state.attributes["last_error"]


async def test_no_data_first_run_fails(hass, entry, sources, freezer) -> None:
    """Without any data and no previous summary the service raises."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    point, chmi, _ = sources
    point.side_effect = OpenMeteoError("x")
    chmi.side_effect = ChmiError("y")
    entity_id = await _setup(hass, entry)
    assert hass.states.get(entity_id).state == "unavailable"
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "refresh", {}, blocking=True)


async def test_auth_failure_starts_reauth(hass, entry, sources, freezer) -> None:
    """401 from OpenAI starts a reauth flow."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    _, _, llm = sources
    llm.side_effect = OpenAIAuthError("bad key")
    await _setup(hass, entry)
    flows = hass.config_entries.flow.async_progress()
    assert any(flow["context"]["source"] == "reauth" for flow in flows)


async def test_unload(hass, entry, sources, freezer) -> None:
    """Entry unloads cleanly."""
    freezer.move_to(datetime(2026, 10, 18, 8, 0, tzinfo=TZ))
    await _setup(hass, entry)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


def test_build_llm_input() -> None:
    """LLM payload contains Czech date and region text."""
    now = datetime(2026, 10, 19, 6, 0, tzinfo=TZ)
    payload = build_llm_input(
        [{"name": "Doma", "chmi": _region_forecast(), "aladin": {"teplota_max": 15}}], now
    )
    assert payload["dnes"] == "pondělí 19. 10. 2026"
    day = payload["mista"][0]["predpoved_chmu"]["den"]
    assert day["textMaximumTemperature"].startswith("Nejvyšší")
    assert payload["mista"][0]["model_aladin"] == {"teplota_max": 15}
