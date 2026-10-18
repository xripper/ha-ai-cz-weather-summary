"""Weather summary sensor."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import CzWeatherConfigEntry, CzWeatherCoordinator

MAX_STATE_LENGTH = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CzWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the summary sensor."""
    async_add_entities([WeatherSummarySensor(entry.runtime_data)])


class WeatherSummarySensor(CoordinatorEntity[CzWeatherCoordinator], SensorEntity):
    """Short summary as state, full text and details as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "summary"
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: CzWeatherCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_summary"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
            manufacturer="ČHMÚ + OpenAI",
        )

    @property
    def available(self) -> bool:
        """Available whenever a summary exists, even if the last update failed."""
        return bool(self.coordinator.data)

    @property
    def native_value(self) -> str | None:
        """Return the short summary."""
        if not (data := self.coordinator.data):
            return None
        short = data.get("short") or data.get("summary") or ""
        if len(short) > MAX_STATE_LENGTH:
            short = short[: MAX_STATE_LENGTH - 1].rstrip() + "…"
        return short

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full summary and details."""
        if not (data := self.coordinator.data):
            return None
        return {
            "summary": data.get("summary"),
            "clothing": data.get("clothing"),
            "generated_at": data.get("generated_at"),
            "model": data.get("model"),
            "locations": data.get("locations", []),
            "last_error": data.get("last_error"),
        }
