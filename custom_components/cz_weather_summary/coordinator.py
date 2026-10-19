"""Coordinator that gathers forecasts and asks the LLM for a summary."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime, time, timedelta
import hashlib
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .chmi import ChmiClient, ChmiError, RegionForecast, find_region
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
    REGION_AUTO,
    REGIONS,
    STORAGE_VERSION,
    TIMEZONE,
)
from .llm import OpenAIAuthError, OpenAIClient, OpenAIError
from .openmeteo import OpenMeteoError, async_get_point_forecast, summarize

_LOGGER = logging.getLogger(__name__)

RETRY_DELAY = timedelta(minutes=30)
MAX_RETRIES = 6

WEEKDAYS = ("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")

type CzWeatherConfigEntry = ConfigEntry[CzWeatherCoordinator]


def parse_time(value: str | None) -> time:
    """Parse HH:MM[:SS] into a time, falling back to the default."""
    for candidate in (value, DEFAULT_UPDATE_TIME):
        try:
            return time.fromisoformat(candidate or "")
        except ValueError:
            continue
    return time(6, 0)


def options_hash(options: Mapping[str, Any]) -> str:
    """Fingerprint of the options that affect the generated text."""
    relevant = {key: options.get(key) for key in (CONF_MODEL, CONF_PROMPT, CONF_LOCATIONS)}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:16]


class CzWeatherCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch weather for configured locations and summarize it."""

    config_entry: CzWeatherConfigEntry

    def __init__(self, hass: HomeAssistant, entry: CzWeatherConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        session = async_get_clientsession(hass)
        self._session = session
        self._chmi = ChmiClient(session)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._retries = 0
        self._cancel_retry: CALLBACK_TYPE | None = None

    @property
    def update_time(self) -> time:
        """Configured daily generation time."""
        return parse_time(self.config_entry.options.get(CONF_UPDATE_TIME))

    async def async_load_stored(self) -> None:
        """Restore the last generated summary."""
        if stored := await self._store.async_load():
            self.async_set_updated_data(stored)

    def needs_refresh(self, now: datetime | None = None) -> bool:
        """Return True if the summary predates the last scheduled run or the options."""
        if not self.data or self.data.get("options_hash") != options_hash(
            self.config_entry.options
        ):
            return True
        if not (generated := dt_util.parse_datetime(self.data.get("generated_at") or "")):
            return True
        now = dt_util.as_local(now or dt_util.now())
        scheduled = now.replace(
            hour=self.update_time.hour,
            minute=self.update_time.minute,
            second=self.update_time.second,
            microsecond=0,
        )
        if scheduled > now:
            scheduled -= timedelta(days=1)
        return generated < scheduled

    @callback
    def async_cancel_retry(self) -> None:
        """Cancel a pending retry."""
        if self._cancel_retry:
            self._cancel_retry()
            self._cancel_retry = None

    async def async_fresh_refresh(self) -> None:
        """Run a scheduled or manual update with a full retry budget."""
        self._retries = 0
        self.async_cancel_retry()
        await self.async_refresh()

    @callback
    def _schedule_retry(self) -> None:
        """Retry a failed run later, a limited number of times."""
        self.async_cancel_retry()
        if self._retries >= MAX_RETRIES:
            return
        self._retries += 1

        @callback
        def _retry(_now: datetime) -> None:
            self._cancel_retry = None
            if self.needs_refresh():
                self.config_entry.async_create_background_task(
                    self.hass, self.async_refresh(), f"{DOMAIN}_retry"
                )

        self._cancel_retry = async_call_later(self.hass, RETRY_DELAY, _retry)

    async def _async_location(
        self,
        location: dict[str, Any],
        now: datetime,
        regions: dict[str, asyncio.Future[RegionForecast]],
    ) -> dict[str, Any]:
        name = location[CONF_NAME]
        lat, lon = location[CONF_LATITUDE], location[CONF_LONGITUDE]
        region = location.get(CONF_REGION) or REGION_AUTO
        if region == REGION_AUTO:
            region = await self.hass.async_add_executor_job(find_region, lat, lon)

        tasks: list[Any] = [async_get_point_forecast(self._session, lat, lon)]
        if region:
            # Locations in the same region share one CHMI download.
            if region not in regions:
                regions[region] = asyncio.ensure_future(
                    self._chmi.async_get_forecast(region, now)
                )
            tasks.append(regions[region])
        results = await asyncio.gather(*tasks, return_exceptions=True)

        info: dict[str, Any] = {"name": name, "region": region, "errors": []}
        point = results[0]
        if isinstance(point, BaseException):
            _log_source_error(point)
            info["errors"].append(f"{name}: {point}")
        else:
            try:
                info["aladin"] = summarize(point, now.date().isoformat())
            except (KeyError, TypeError, ValueError, AttributeError) as err:
                _LOGGER.warning("Unexpected Open-Meteo data: %s", err)
                info["errors"].append(f"{name}: unexpected Open-Meteo data")

        if region:
            text = results[1]
            if isinstance(text, BaseException):
                _log_source_error(text)
                info["errors"].append(f"{name}: {text}")
            else:
                info["chmi"] = text
        return info

    async def _async_update_data(self) -> dict[str, Any]:
        options = self.config_entry.options
        # CHMI and Open-Meteo data are in Prague local time.
        now = dt_util.now(dt_util.get_time_zone(TIMEZONE))
        regions: dict[str, asyncio.Future[RegionForecast]] = {}
        locations = await asyncio.gather(
            *(self._async_location(loc, now, regions) for loc in options.get(CONF_LOCATIONS, []))
        )
        errors = [err for loc in locations for err in loc["errors"]]
        if not any("aladin" in loc or "chmi" in loc for loc in locations):
            return self._failed("; ".join(errors) or "No weather data")

        model = options.get(CONF_MODEL) or DEFAULT_MODEL
        client = OpenAIClient(self._session, self.config_entry.data[CONF_API_KEY])
        try:
            summary = await client.async_summarize(
                model, options.get(CONF_PROMPT) or DEFAULT_PROMPT, build_llm_input(locations, now)
            )
        except OpenAIAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except OpenAIError as err:
            return self._failed(str(err))

        self._retries = 0
        self.async_cancel_retry()
        data = {
            **summary,
            "generated_at": now.isoformat(timespec="seconds"),
            "model": model,
            "locations": [location_attributes(loc) for loc in locations],
            "last_error": "; ".join(errors) or None,
            "options_hash": options_hash(options),
        }
        await self._store.async_save(data)
        return data

    def _failed(self, error: str) -> dict[str, Any]:
        """Keep the previous summary but report the error, or fail if there is none."""
        _LOGGER.warning("Weather summary update failed: %s", error)
        self._schedule_retry()
        if not self.data:
            raise UpdateFailed(error)
        return {**self.data, "last_error": error}


def _log_source_error(err: BaseException) -> None:
    if not isinstance(err, (ChmiError, OpenMeteoError)):
        _LOGGER.error("Unexpected error fetching weather data", exc_info=err)


def _forecast_dict(forecast: RegionForecast) -> dict[str, Any]:
    result: dict[str, Any] = {"kraj": forecast.region_name}
    if forecast.day:
        result["den"] = forecast.day.as_dict()
    if forecast.night:
        result["noc"] = forecast.night.as_dict()
    return result


def build_llm_input(locations: list[dict[str, Any]], now: datetime) -> dict[str, Any]:
    """Build the structured weather payload sent to the LLM."""
    places = []
    for loc in locations:
        place: dict[str, Any] = {"nazev": loc["name"]}
        if chmi := loc.get("chmi"):
            place["predpoved_chmu"] = _forecast_dict(chmi)
        if aladin := loc.get("aladin"):
            place["model_aladin"] = aladin
        places.append(place)
    return {
        "dnes": f"{WEEKDAYS[now.weekday()]} {now.day}. {now.month}. {now.year}",
        "aktualni_cas": now.strftime("%H:%M"),
        "mista": places,
    }


def location_attributes(loc: dict[str, Any]) -> dict[str, Any]:
    """Compact per-location data for sensor attributes."""
    attrs: dict[str, Any] = {
        "name": loc["name"],
        "region": REGIONS.get(loc["region"]) if loc.get("region") else None,
    }
    if aladin := loc.get("aladin"):
        attrs |= {
            "temp_min": aladin.get("teplota_min"),
            "temp_max": aladin.get("teplota_max"),
            "precipitation": aladin.get("srazky_mm"),
        }
    chmi: RegionForecast | None = loc.get("chmi")
    if chmi and chmi.day:
        attrs["forecast"] = chmi.day.blocks.get("textIntro")
        attrs["wind"] = chmi.day.blocks.get("textWind")
    if chmi:
        attrs["warnings"] = [
            part.blocks["dangerousPhenomena"]
            for part in (chmi.day, chmi.night)
            if part and "dangerousPhenomena" in part.blocks
        ]
    return {k: v for k, v in attrs.items() if v is not None}
