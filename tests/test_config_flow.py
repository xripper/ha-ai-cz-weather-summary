"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cz_weather_summary.const import (
    CONF_API_KEY,
    CONF_LOCATIONS,
    CONF_MODEL,
    CONF_PROMPT,
    CONF_UPDATE_TIME,
    DEFAULT_PROMPT,
    DOMAIN,
)
from custom_components.cz_weather_summary.llm import OpenAIAuthError

MODELS = "custom_components.cz_weather_summary.llm.OpenAIClient.async_list_models"
SETUP = "custom_components.cz_weather_summary.async_setup_entry"

HOME = {"name": "Doma", "location": {"latitude": 50.088, "longitude": 14.42}}
COTTAGE = {"name": "Chata", "location": {"latitude": 49.195, "longitude": 16.608}}
WORK = {"name": "Práce", "location": {"latitude": 49.82, "longitude": 18.26}}


async def _start(hass) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(MODELS, return_value=["gpt-4o-mini", "gpt-5-mini"]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk-test"}
        )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODEL: "gpt-5-mini", CONF_UPDATE_TIME: "06:30:00"}
    )
    assert result["step_id"] == "location"
    return result


async def test_single_location(hass) -> None:
    """One location is enough."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], HOME)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["location", "finish"]

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "finish"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: "sk-test"}
    options = result["options"]
    assert options[CONF_MODEL] == "gpt-5-mini"
    assert options[CONF_UPDATE_TIME] == "06:30:00"
    assert options[CONF_PROMPT] == DEFAULT_PROMPT
    assert options[CONF_LOCATIONS] == [
        {"name": "Doma", "latitude": 50.088, "longitude": 14.42, "region": "auto"}
    ]


async def test_three_locations_finish_automatically(hass) -> None:
    """After the third location the entry is created without asking."""
    result = await _start(hass)
    for location in (HOME, COTTAGE):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], location)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "location"}
        )
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], WORK)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert [loc["name"] for loc in result["options"][CONF_LOCATIONS]] == [
        "Doma",
        "Chata",
        "Práce",
    ]


async def test_invalid_key(hass) -> None:
    """Invalid key shows an error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(MODELS, side_effect=OpenAIAuthError):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "bad"}
        )
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.fixture
def entry(hass) -> MockConfigEntry:
    """Entry with two locations."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "sk"},
        options={
            CONF_MODEL: "gpt-4o-mini",
            CONF_UPDATE_TIME: "06:00:00",
            CONF_PROMPT: "custom",
            CONF_LOCATIONS: [
                {"name": "Doma", "latitude": 50.0, "longitude": 14.4, "region": "auto"},
                {"name": "Chata", "latitude": 49.2, "longitude": 16.6, "region": "auto"},
            ],
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _menu(hass, entry, step: str) -> dict:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    with patch(MODELS, return_value=["gpt-4o-mini"]):
        return await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )


async def test_options_menu(hass, entry) -> None:
    """Menu offers add and remove depending on the location count."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "settings",
        "edit_location",
        "add_location",
        "remove_location",
    ]


async def test_options_settings(hass, entry) -> None:
    """Settings change; clearing the prompt restores the default."""
    result = await _menu(hass, entry, "settings")
    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MODEL: "gpt-4o-mini", CONF_UPDATE_TIME: "07:00:00"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_UPDATE_TIME] == "07:00:00"
    assert entry.options[CONF_PROMPT] == DEFAULT_PROMPT
    assert len(entry.options[CONF_LOCATIONS]) == 2


async def test_options_add_location(hass, entry) -> None:
    """A third location can be added."""
    result = await _menu(hass, entry, "add_location")
    with patch(SETUP, return_value=True):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {**WORK, "region": "RPMS"}
        )
    assert entry.options[CONF_LOCATIONS][2] == {
        "name": "Práce",
        "latitude": 49.82,
        "longitude": 18.26,
        "region": "RPMS",
    }
    assert entry.options[CONF_PROMPT] == "custom"


async def test_options_edit_location(hass, entry) -> None:
    """The chosen location is edited, including the region override."""
    result = await _menu(hass, entry, "edit_location")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"index": "1"})
    assert result["step_id"] == "edit_location_form"
    with patch(SETUP, return_value=True):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"name": "Chalupa", "location": {"latitude": 49.3, "longitude": 16.7}, "region": "RPJM"},
        )
    assert entry.options[CONF_LOCATIONS][1] == {
        "name": "Chalupa",
        "latitude": 49.3,
        "longitude": 16.7,
        "region": "RPJM",
    }


async def test_options_remove_location(hass, entry) -> None:
    """A location can be removed, but not the last one."""
    result = await _menu(hass, entry, "remove_location")
    with patch(SETUP, return_value=True):
        await hass.config_entries.options.async_configure(result["flow_id"], {"index": "0"})
    assert [loc["name"] for loc in entry.options[CONF_LOCATIONS]] == ["Chata"]
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["settings", "edit_location", "add_location"]


async def test_reauth(hass, entry) -> None:
    """Reauth stores the new key."""
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    with patch(MODELS, side_effect=OpenAIAuthError):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "still-bad"}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    with patch(MODELS, return_value=["gpt-4o-mini"]), patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new"
