"""Weather summary sensors: short summary, full summary and clothing advice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import CzWeatherConfigEntry, CzWeatherCoordinator

# Home Assistant limits a state to 255 characters; the full text is in the "text" attribute.
MAX_STATE_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class SummarySensorDescription(SensorEntityDescription):
    """Describes a summary sensor."""

    data_key: str
    details: bool = False


SENSORS = (
    SummarySensorDescription(key="short", translation_key="short", data_key="short", details=True),
    SummarySensorDescription(key="summary", translation_key="summary", data_key="summary"),
    SummarySensorDescription(key="clothing", translation_key="clothing", data_key="clothing"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CzWeatherConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the summary sensors."""
    async_add_entities(WeatherSummarySensor(entry.runtime_data, desc) for desc in SENSORS)


def _truncate(text: str) -> str:
    if len(text) <= MAX_STATE_LENGTH:
        return text
    return text[: MAX_STATE_LENGTH - 1].rstrip() + "…"


class WeatherSummarySensor(CoordinatorEntity[CzWeatherCoordinator], SensorEntity):
    """One part of the generated summary."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION
    entity_description: SummarySensorDescription

    def __init__(
        self, coordinator: CzWeatherCoordinator, description: SummarySensorDescription
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
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
    def _text(self) -> str:
        data = self.coordinator.data or {}
        return data.get(self.entity_description.data_key) or ""

    @property
    def native_value(self) -> str | None:
        """Return the text, truncated to the state length limit."""
        if not self.coordinator.data:
            return None
        return _truncate(self._text)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the full text and, on the short sensor, the details."""
        if not (data := self.coordinator.data):
            return None
        attrs: dict[str, Any] = {
            "text": self._text,
            "generated_at": data.get("generated_at"),
        }
        if self.entity_description.details:
            attrs |= {
                "summary": data.get("summary"),
                "clothing": data.get("clothing"),
                "model": data.get("model"),
                "locations": data.get("locations", []),
                "last_error": data.get("last_error"),
            }
        return attrs
