"""Config flow for CZ Weather Summary."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    LocationSelector,
    LocationSelectorConfig,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)

from .const import (
    CONF_API_KEY,
    CONF_LATITUDE,
    CONF_LOCATIONS,
    CONF_LONGITUDE,
    CONF_MODEL,
    CONF_NAME,
    CONF_PROMPT,
    CONF_REGION,
    CONF_UPDATE_TIME,
    DEFAULT_MODEL,
    DEFAULT_PROMPT,
    DEFAULT_UPDATE_TIME,
    DOMAIN,
    MAX_LOCATIONS,
    REGION_AUTO,
    REGIONS,
)
from .llm import OpenAIAuthError, OpenAIClient, OpenAIError, default_model

_LOGGER = logging.getLogger(__name__)

API_KEY_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


async def _validate_key(hass: HomeAssistant, api_key: str) -> tuple[list[str], str | None]:
    """Return (models, error key)."""
    try:
        models = await OpenAIClient(async_get_clientsession(hass), api_key).async_list_models()
    except OpenAIAuthError:
        return [], "invalid_auth"
    except OpenAIError as err:
        _LOGGER.warning("OpenAI validation failed: %s", err)
        return [], "cannot_connect"
    return models, None


def _model_selector(models: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=models or [DEFAULT_MODEL],
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
            sort=True,
        )
    )


def _region_selector() -> SelectSelector:
    options = [SelectOptionDict(value=REGION_AUTO, label="Automaticky podle polohy")]
    options += [SelectOptionDict(value=code, label=name) for code, name in REGIONS.items()]
    return SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))


def _suggested(value: Any) -> dict[str, Any]:
    return {"description": {"suggested_value": value}} if value is not None else {}


def _locations_schema(
    hass: HomeAssistant, locations: list[dict[str, Any]], with_region: bool
) -> dict[vol.Marker, Any]:
    """Schema fields for up to MAX_LOCATIONS locations."""
    fields: dict[vol.Marker, Any] = {}
    for i in range(1, MAX_LOCATIONS + 1):
        current = locations[i - 1] if i <= len(locations) else None
        if current:
            name_default: str | None = current[CONF_NAME]
            loc_default: dict[str, float] | None = {
                CONF_LATITUDE: current[CONF_LATITUDE],
                CONF_LONGITUDE: current[CONF_LONGITUDE],
            }
        elif i == 1:
            name_default = "Doma"
            loc_default = {
                CONF_LATITUDE: hass.config.latitude,
                CONF_LONGITUDE: hass.config.longitude,
            }
        else:
            name_default = loc_default = None

        # suggested_value (not default) so optional locations can be cleared.
        marker = vol.Required if i == 1 else vol.Optional
        fields[marker(f"{CONF_NAME}_{i}", **_suggested(name_default))] = TextSelector()
        fields[marker(f"location_{i}", **_suggested(loc_default))] = LocationSelector(
            LocationSelectorConfig(radius=False)
        )
        if with_region:
            fields[
                vol.Optional(
                    f"{CONF_REGION}_{i}",
                    default=(current or {}).get(CONF_REGION, REGION_AUTO),
                )
            ] = _region_selector()
    return fields


def _parse_locations(user_input: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Turn flat form fields into a location list."""
    locations: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for i in range(1, MAX_LOCATIONS + 1):
        name = (user_input.get(f"{CONF_NAME}_{i}") or "").strip()
        location = user_input.get(f"location_{i}")
        # An empty name means the slot is unused: the map selector can't be cleared.
        if not name:
            continue
        if not location:
            errors[f"location_{i}"] = "location_required"
            continue
        locations.append(
            {
                CONF_NAME: name,
                CONF_LATITUDE: round(float(location[CONF_LATITUDE]), 5),
                CONF_LONGITUDE: round(float(location[CONF_LONGITUDE]), 5),
                CONF_REGION: user_input.get(f"{CONF_REGION}_{i}", REGION_AUTO),
            }
        )
    if not locations and not errors:
        errors["base"] = "no_locations"
    return locations, errors


class CzWeatherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key: str = ""
        self._models: list[str] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> CzWeatherOptionsFlow:
        """Return the options flow."""
        return CzWeatherOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the OpenAI API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._models, error = await _validate_key(self.hass, user_input[CONF_API_KEY])
            if error:
                errors["base"] = error
            else:
                self._api_key = user_input[CONF_API_KEY]
                return await self.async_step_locations()
        return self.async_show_form(step_id="user", data_schema=API_KEY_SCHEMA, errors=errors)

    async def async_step_locations(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Configure model and locations."""
        errors: dict[str, str] = {}
        if user_input is not None:
            locations, errors = _parse_locations(user_input)
            if not errors:
                return self.async_create_entry(
                    title="Souhrn počasí",
                    data={CONF_API_KEY: self._api_key},
                    options={
                        CONF_MODEL: user_input[CONF_MODEL],
                        CONF_UPDATE_TIME: user_input[CONF_UPDATE_TIME],
                        CONF_PROMPT: DEFAULT_PROMPT,
                        CONF_LOCATIONS: locations,
                    },
                )
        schema = {
            vol.Required(
                CONF_MODEL, default=default_model(self._models, DEFAULT_MODEL)
            ): _model_selector(self._models),
            vol.Required(CONF_UPDATE_TIME, default=DEFAULT_UPDATE_TIME): TimeSelector(),
            **_locations_schema(self.hass, [], with_region=False),
        }
        data_schema = vol.Schema(schema)
        if user_input:
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)
        return self.async_show_form(step_id="locations", data_schema=data_schema, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Handle an invalid API key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            _, error = await _validate_key(self.hass, user_input[CONF_API_KEY])
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_API_KEY: user_input[CONF_API_KEY]}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=API_KEY_SCHEMA, errors=errors
        )


class CzWeatherOptionsFlow(OptionsFlowWithReload):
    """Edit model, prompt, schedule and locations; the entry reloads on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        errors: dict[str, str] = {}
        options = self.config_entry.options
        if user_input is not None:
            locations, errors = _parse_locations(user_input)
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_MODEL: user_input[CONF_MODEL],
                        CONF_UPDATE_TIME: user_input[CONF_UPDATE_TIME],
                        CONF_PROMPT: (user_input.get(CONF_PROMPT) or "").strip() or DEFAULT_PROMPT,
                        CONF_LOCATIONS: locations,
                    }
                )

        models, _ = await _validate_key(self.hass, self.config_entry.data[CONF_API_KEY])
        current_model = options.get(CONF_MODEL, DEFAULT_MODEL)
        if current_model not in models:
            models = [*models, current_model]
        schema = {
            vol.Required(CONF_MODEL, default=current_model): _model_selector(models),
            vol.Required(
                CONF_UPDATE_TIME, default=options.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME)
            ): TimeSelector(),
            # suggested_value so clearing the prompt restores the default.
            vol.Optional(
                CONF_PROMPT,
                description={"suggested_value": options.get(CONF_PROMPT, DEFAULT_PROMPT)},
            ): TextSelector(TextSelectorConfig(multiline=True)),
            **_locations_schema(self.hass, options.get(CONF_LOCATIONS, []), with_region=True),
        }
        data_schema = vol.Schema(schema)
        if user_input:
            data_schema = self.add_suggested_values_to_schema(data_schema, user_input)
        return self.async_show_form(step_id="init", data_schema=data_schema, errors=errors)
