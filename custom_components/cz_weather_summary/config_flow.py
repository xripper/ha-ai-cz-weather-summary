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

CONF_LOCATION = "location"
CONF_INDEX = "index"

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


def _location_schema(
    name: str | None, latitude: float, longitude: float, region: str | None = None
) -> vol.Schema:
    """Form for a single location; region only in the options flow."""
    fields: dict[vol.Marker, Any] = {
        vol.Required(CONF_NAME, description={"suggested_value": name} if name else None): (
            TextSelector()
        ),
        vol.Required(
            CONF_LOCATION, default={CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude}
        ): LocationSelector(LocationSelectorConfig(radius=False)),
    }
    if region is not None:
        fields[vol.Required(CONF_REGION, default=region)] = _region_selector()
    return vol.Schema(fields)


def _location_from_input(user_input: dict[str, Any], region: str = REGION_AUTO) -> dict[str, Any]:
    location = user_input[CONF_LOCATION]
    return {
        CONF_NAME: user_input[CONF_NAME].strip(),
        CONF_LATITUDE: round(float(location[CONF_LATITUDE]), 5),
        CONF_LONGITUDE: round(float(location[CONF_LONGITUDE]), 5),
        CONF_REGION: user_input.get(CONF_REGION, region),
    }


def _index_schema(locations: list[dict[str, Any]]) -> vol.Schema:
    options = [
        SelectOptionDict(value=str(i), label=loc[CONF_NAME]) for i, loc in enumerate(locations)
    ]
    return vol.Schema(
        {
            vol.Required(CONF_INDEX, default="0"): SelectSelector(
                SelectSelectorConfig(options=options, mode=SelectSelectorMode.LIST)
            )
        }
    )


class CzWeatherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up: API key, model and time, then locations one by one."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api_key: str = ""
        self._models: list[str] = []
        self._settings: dict[str, Any] = {}
        self._locations: list[dict[str, Any]] = []

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
                return await self.async_step_settings()
        return self.async_show_form(step_id="user", data_schema=API_KEY_SCHEMA, errors=errors)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the model and the daily generation time."""
        if user_input is not None:
            self._settings = user_input
            return await self.async_step_location()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODEL, default=default_model(self._models, DEFAULT_MODEL)
                ): _model_selector(self._models),
                vol.Required(CONF_UPDATE_TIME, default=DEFAULT_UPDATE_TIME): TimeSelector(),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add one location."""
        if user_input is not None:
            self._locations.append(_location_from_input(user_input))
            if len(self._locations) >= MAX_LOCATIONS:
                return await self.async_step_finish()
            return await self.async_step_more()

        first = not self._locations
        return self.async_show_form(
            step_id="location",
            data_schema=_location_schema(
                "Doma" if first else None, self.hass.config.latitude, self.hass.config.longitude
            ),
            description_placeholders={
                "number": str(len(self._locations) + 1),
                "max": str(MAX_LOCATIONS),
            },
        )

    async def async_step_more(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask whether to add another location."""
        return self.async_show_menu(step_id="more", menu_options=["location", "finish"])

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry."""
        return self.async_create_entry(
            title="Souhrn počasí",
            data={CONF_API_KEY: self._api_key},
            options={
                CONF_MODEL: self._settings[CONF_MODEL],
                CONF_UPDATE_TIME: self._settings[CONF_UPDATE_TIME],
                CONF_PROMPT: DEFAULT_PROMPT,
                CONF_LOCATIONS: self._locations,
            },
        )

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
    """Menu to change settings and add, edit or remove locations; reloads on save."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._index = 0

    @property
    def _locations(self) -> list[dict[str, Any]]:
        return list(self.config_entry.options.get(CONF_LOCATIONS, []))

    def _save(self, **changes: Any) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the menu."""
        count = len(self._locations)
        menu = ["settings", "edit_location"]
        if count < MAX_LOCATIONS:
            menu.append("add_location")
        if count > 1:
            menu.append("remove_location")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Model, generation time and prompt."""
        options = self.config_entry.options
        if user_input is not None:
            return self._save(
                **{
                    CONF_MODEL: user_input[CONF_MODEL],
                    CONF_UPDATE_TIME: user_input[CONF_UPDATE_TIME],
                    CONF_PROMPT: (user_input.get(CONF_PROMPT) or "").strip() or DEFAULT_PROMPT,
                }
            )

        models, _ = await _validate_key(self.hass, self.config_entry.data[CONF_API_KEY])
        current_model = options.get(CONF_MODEL, DEFAULT_MODEL)
        if current_model not in models:
            models = [*models, current_model]
        schema = vol.Schema(
            {
                vol.Required(CONF_MODEL, default=current_model): _model_selector(models),
                vol.Required(
                    CONF_UPDATE_TIME, default=options.get(CONF_UPDATE_TIME, DEFAULT_UPDATE_TIME)
                ): TimeSelector(),
                # suggested_value so clearing the prompt restores the default.
                vol.Optional(
                    CONF_PROMPT,
                    description={"suggested_value": options.get(CONF_PROMPT, DEFAULT_PROMPT)},
                ): TextSelector(TextSelectorConfig(multiline=True)),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_add_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a location."""
        locations = self._locations
        if len(locations) >= MAX_LOCATIONS:
            return self.async_abort(reason="max_locations")
        if user_input is not None:
            return self._save(**{CONF_LOCATIONS: [*locations, _location_from_input(user_input)]})
        return self.async_show_form(
            step_id="add_location",
            data_schema=_location_schema(
                None, self.hass.config.latitude, self.hass.config.longitude, REGION_AUTO
            ),
        )

    async def async_step_edit_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the location to edit."""
        locations = self._locations
        if len(locations) == 1:
            return await self.async_step_edit_location_form()
        if user_input is not None:
            self._index = int(user_input[CONF_INDEX])
            return await self.async_step_edit_location_form()
        return self.async_show_form(step_id="edit_location", data_schema=_index_schema(locations))

    async def async_step_edit_location_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the chosen location."""
        locations = self._locations
        if user_input is not None:
            locations[self._index] = _location_from_input(user_input)
            return self._save(**{CONF_LOCATIONS: locations})
        current = locations[self._index]
        return self.async_show_form(
            step_id="edit_location_form",
            data_schema=_location_schema(
                current[CONF_NAME],
                current[CONF_LATITUDE],
                current[CONF_LONGITUDE],
                current.get(CONF_REGION, REGION_AUTO),
            ),
        )

    async def async_step_remove_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove a location (at least one must stay)."""
        locations = self._locations
        if len(locations) <= 1:
            return self.async_abort(reason="last_location")
        if user_input is not None:
            del locations[int(user_input[CONF_INDEX])]
            return self._save(**{CONF_LOCATIONS: locations})
        return self.async_show_form(
            step_id="remove_location", data_schema=_index_schema(locations)
        )
