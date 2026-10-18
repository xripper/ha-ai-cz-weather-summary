"""Tests for the config and options flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

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


async def test_full_flow(hass) -> None:
    """User enters key, then model and two locations."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch(MODELS, return_value=["gpt-4o-mini", "gpt-5-mini"]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk-test"}
        )
    assert result["step_id"] == "locations"

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_MODEL: "gpt-5-mini",
                CONF_UPDATE_TIME: "06:30:00",
                "name_1": "Doma",
                "location_1": {"latitude": 50.088, "longitude": 14.42},
                "name_2": "Chata",
                "location_2": {"latitude": 49.195, "longitude": 16.608},
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: "sk-test"}
    options = result["options"]
    assert options[CONF_MODEL] == "gpt-5-mini"
    assert options[CONF_PROMPT] == DEFAULT_PROMPT
    assert [loc["name"] for loc in options[CONF_LOCATIONS]] == ["Doma", "Chata"]
    assert options[CONF_LOCATIONS][0]["region"] == "auto"


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


async def test_location_missing_location(hass) -> None:
    """A named location without coordinates is rejected and input is kept."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(MODELS, return_value=["gpt-4o-mini"]):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_API_KEY: "sk"}
        )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_MODEL: "gpt-4o-mini",
            CONF_UPDATE_TIME: "06:00:00",
            "name_1": "Doma",
            "location_1": {"latitude": 50.0, "longitude": 14.0},
            "name_2": "Chata",
        },
    )
    assert result["errors"] == {"location_2": "location_required"}
    name_2 = next(k for k in result["data_schema"].schema if k == "name_2")
    assert name_2.description == {"suggested_value": "Chata"}


async def test_options_flow(hass) -> None:
    """Options can change prompt, region and remove a location."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "sk"},
        options={
            CONF_MODEL: "gpt-4o-mini",
            CONF_UPDATE_TIME: "06:00:00",
            CONF_PROMPT: DEFAULT_PROMPT,
            CONF_LOCATIONS: [
                {"name": "Doma", "latitude": 50.0, "longitude": 14.4, "region": "auto"},
                {"name": "Chata", "latitude": 49.2, "longitude": 16.6, "region": "auto"},
            ],
        },
    )
    entry.add_to_hass(hass)
    with patch(MODELS, return_value=["gpt-4o-mini"]):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"

    with patch(SETUP, return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_MODEL: "gpt-4o-mini",
                CONF_UPDATE_TIME: "07:00:00",
                CONF_PROMPT: "Piš vtipně.",
                "name_1": "Doma",
                "location_1": {"latitude": 50.0, "longitude": 14.4},
                "region_1": "RPSC",
                # The map can't be cleared; an empty name removes the slot.
                "name_2": "",
                "location_2": {"latitude": 49.2, "longitude": 16.6},
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PROMPT] == "Piš vtipně."
    assert entry.options[CONF_LOCATIONS] == [
        {"name": "Doma", "latitude": 50.0, "longitude": 14.4, "region": "RPSC"}
    ]


async def test_options_empty_prompt_restores_default(hass) -> None:
    """Clearing the prompt falls back to the default prompt."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "sk"},
        options={
            CONF_MODEL: "gpt-4o-mini",
            CONF_UPDATE_TIME: "06:00:00",
            CONF_PROMPT: "custom",
            CONF_LOCATIONS: [
                {"name": "Doma", "latitude": 50.0, "longitude": 14.4, "region": "auto"}
            ],
        },
    )
    entry.add_to_hass(hass)
    with patch(MODELS, return_value=["gpt-4o-mini"]):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(SETUP, return_value=True):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_MODEL: "gpt-4o-mini",
                CONF_UPDATE_TIME: "06:00:00",
                "name_1": "Doma",
                "location_1": {"latitude": 50.0, "longitude": 14.4},
            },
        )
    assert entry.options[CONF_PROMPT] == DEFAULT_PROMPT


async def test_reauth(hass) -> None:
    """Reauth stores the new key."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_API_KEY: "old"},
        options={CONF_MODEL: "gpt-4o-mini", CONF_UPDATE_TIME: "06:00:00", CONF_LOCATIONS: []},
    )
    entry.add_to_hass(hass)
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
