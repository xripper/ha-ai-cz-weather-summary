"""CZ Weather Summary: CHMI forecast summarized by an OpenAI model."""

from __future__ import annotations

from datetime import datetime

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import ATTR_ENTRY_ID, DOMAIN, SERVICE_REFRESH, STORAGE_VERSION
from .coordinator import CzWeatherConfigEntry, CzWeatherCoordinator

PLATFORMS = [Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

REFRESH_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the refresh service."""

    async def _refresh(call: ServiceCall) -> None:
        entry_id = call.data.get(ATTR_ENTRY_ID)
        entries = [
            entry
            for entry in hass.config_entries.async_loaded_entries(DOMAIN)
            if entry_id is None or entry.entry_id == entry_id
        ]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="entry_not_found"
            )
        errors: list[str] = []
        for entry in entries:
            coordinator: CzWeatherCoordinator = entry.runtime_data
            previous = (coordinator.data or {}).get("generated_at")
            await coordinator.async_fresh_refresh()
            data = coordinator.data or {}
            if not coordinator.last_update_success:
                errors.append(str(coordinator.last_exception))
            elif data.get("generated_at") == previous and data.get("last_error"):
                # The previous summary was kept because the update failed.
                errors.append(data["last_error"])
        if errors:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="refresh_failed",
                translation_placeholders={"error": "; ".join(errors)},
            )

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh, schema=REFRESH_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: CzWeatherConfigEntry) -> bool:
    """Set up from a config entry."""
    coordinator = CzWeatherCoordinator(hass, entry)
    await coordinator.async_load_stored()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    run_at = coordinator.update_time

    @callback
    def _scheduled(_now: datetime) -> None:
        entry.async_create_background_task(
            hass, coordinator.async_fresh_refresh(), f"{DOMAIN}_scheduled_refresh"
        )

    entry.async_on_unload(
        async_track_time_change(
            hass, _scheduled, hour=run_at.hour, minute=run_at.minute, second=run_at.second
        )
    )
    entry.async_on_unload(coordinator.async_cancel_retry)

    # Catch up if HA was off at the scheduled time or the options changed
    # (options flow reloads the entry); don't block startup on the LLM.
    if coordinator.needs_refresh():
        entry.async_create_background_task(
            hass, coordinator.async_refresh(), f"{DOMAIN}_initial_refresh"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CzWeatherConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: CzWeatherConfigEntry) -> None:
    """Delete stored data when the entry is removed."""
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
