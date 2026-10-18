"""Client for CHMI (ČHMÚ) regional text forecasts from opendata.chmi.cz."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
import json
import logging
import math
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import CHMI_FORECAST_URL, REGIONS, TIMEZONE

_LOGGER = logging.getLogger(__name__)

_TZ = ZoneInfo(TIMEZONE)
_REGIONS_FILE = Path(__file__).parent / "regions.json"

# Products: CK0 = today, CK1 = tomorrow, CKn = night.
PRODUCT_DAY = ("CK0", "CK1")
PRODUCT_NIGHT = ("CKn",)
MAX_FETCHES = 4
NIGHT_START_SLACK = timedelta(hours=6)

_LISTING_RE = re.compile(
    r'href="(?P<file>web_p(?P<product>[A-Za-z0-9]+)tx_(?P<region>RP[A-Z]+)_'
    r'(?P<issued>\d{6})(?:_(?P<correction>CC[A-Z]))?\.json)">[^<]*</a>\s+'
    r"(?P<mtime>\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2})"
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}

# Point-in-polygon fallback: max distance (degrees) to the nearest region vertex.
_NEAREST_MAX_DEG = 0.15

_regions_cache: dict[str, list[list[list[float]]]] | None = None


class ChmiError(Exception):
    """Error fetching or parsing CHMI data."""


@dataclass(slots=True)
class ListingEntry:
    """One forecast file in the CHMI directory listing."""

    filename: str
    product: str
    region: str
    correction: str
    modified: datetime


@dataclass(slots=True)
class TextForecast:
    """A parsed CHMI text forecast for one period."""

    product: str
    sent: datetime | None
    valid_from: datetime | None
    valid_to: datetime | None
    blocks: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "vydano": _iso(self.sent),
            "platnost_od": _iso(self.valid_from),
            "platnost_do": _iso(self.valid_to),
            **self.blocks,
        }


@dataclass(slots=True)
class RegionForecast:
    """Day and night text forecast for one region."""

    region: str
    day: TextForecast | None
    night: TextForecast | None

    @property
    def region_name(self) -> str:
        """Human readable region name."""
        return REGIONS.get(self.region, self.region)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(_TZ).isoformat(timespec="minutes") if value else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_mtime(value: str) -> datetime:
    """Parse listing time like '18-Oct-2026 08:15' (UTC)."""
    day, month, rest = value.split("-", 2)
    year, hm = rest.split(" ")
    hour, minute = hm.split(":")
    return datetime(
        int(year), _MONTHS[month], int(day), int(hour), int(minute), tzinfo=UTC
    )


def parse_listing(html: str) -> list[ListingEntry]:
    """Parse the nginx directory index of forecast files."""
    entries = []
    for match in _LISTING_RE.finditer(html):
        try:
            modified = _parse_mtime(match["mtime"])
        except (KeyError, ValueError):
            continue
        entries.append(
            ListingEntry(
                filename=match["file"],
                product=match["product"],
                region=match["region"],
                correction=match["correction"] or "",
                modified=modified,
            )
        )
    return entries


def parse_forecast(payload: dict[str, Any], product: str = "") -> TextForecast:
    """Parse a CHMI forecast JSON document."""
    try:
        return _parse_forecast(payload, product)
    except (KeyError, IndexError, TypeError, AttributeError) as err:
        raise ChmiError(f"Unexpected forecast format: {err!r}") from err


def _parse_forecast(payload: dict[str, Any], product: str) -> TextForecast:
    props = payload["data"]["features"][0]["properties"]
    blocks: dict[str, str] = {}
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    for block in sorted(props.get("data") or [], key=lambda b: b.get("displayOrder") or 0):
        name = block.get("name")
        text = block.get("displayText")
        if not name or not isinstance(text, str) or not text.strip():
            continue
        blocks[name] = _clean(text)
        start, end = _parse_dt(block.get("startTime")), _parse_dt(block.get("endTime"))
        if start and (valid_from is None or start < valid_from):
            valid_from = start
        if end and (valid_to is None or end > valid_to):
            valid_to = end

    return TextForecast(
        product=product,
        sent=_parse_dt(props.get("sent")),
        valid_from=valid_from,
        valid_to=valid_to,
        blocks=blocks,
    )


def _candidates(
    entries: list[ListingEntry], region: str, products: tuple[str, ...], now: datetime
) -> list[ListingEntry]:
    today = now.astimezone(_TZ).date()
    found = [
        e
        for e in entries
        if e.region == region
        and e.product in products
        and e.modified <= now
        and (today - e.modified.astimezone(_TZ).date()).days <= 1
    ]
    # Newest first; corrections (CCA, CCB...) and today's product (CK0) win ties.
    return sorted(
        found,
        key=lambda e: (e.modified, e.correction, e.product == "CK0"),
        reverse=True,
    )


class ChmiClient:
    """Fetch CHMI regional text forecasts."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialize the client."""
        self._session = session

    async def _get(self, path: str) -> str:
        try:
            async with self._session.get(
                CHMI_FORECAST_URL + path, timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                resp.raise_for_status()
                return await resp.text(encoding="utf-8")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise ChmiError(f"Cannot fetch {CHMI_FORECAST_URL}{path}: {err}") from err

    async def _pick(
        self, candidates: list[ListingEntry], today: date, night: bool = False
    ) -> TextForecast | None:
        """Return the newest forecast for today (or tonight)."""
        for entry in candidates[:MAX_FETCHES]:
            try:
                forecast = parse_forecast(
                    json.loads(await self._get(entry.filename)), entry.product
                )
            except (ChmiError, ValueError) as err:
                _LOGGER.debug("Skipping %s: %s", entry.filename, err)
                continue
            if not forecast.valid_from:
                continue
            start = forecast.valid_from.astimezone(_TZ)
            # Tonight's forecast may start after midnight (winter time).
            if night:
                start -= NIGHT_START_SLACK
            if start.date() == today:
                return forecast
        return None

    async def async_get_forecast(self, region: str, now: datetime) -> RegionForecast:
        """Return today's day and night forecast for a region."""
        today = now.astimezone(_TZ).date()
        entries = parse_listing(await self._get(""))
        if not entries:
            raise ChmiError("CHMI forecast listing is empty or has unknown format")

        day = await self._pick(_candidates(entries, region, PRODUCT_DAY, now), today)
        night = await self._pick(
            _candidates(entries, region, PRODUCT_NIGHT, now), today, night=True
        )
        if day is None and night is None:
            raise ChmiError(f"No current forecast found for region {region}")
        return RegionForecast(region=region, day=day, night=night)


def _load_regions() -> dict[str, list[list[list[float]]]]:
    global _regions_cache  # noqa: PLW0603
    if _regions_cache is None:
        _regions_cache = json.loads(_REGIONS_FILE.read_text(encoding="utf-8"))
    return _regions_cache


def _in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    j = len(ring) - 1
    for i, (xi, yi) in enumerate(ring):
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def find_region(latitude: float, longitude: float) -> str | None:
    """Return the CHMI region code for coordinates, or None outside Czechia.

    Blocking (reads a file on first call); run in an executor.
    """
    regions = _load_regions()
    # Prague lies inside the Central Bohemian polygon, so check it first.
    order = sorted(regions, key=lambda code: code != "RPPH")
    for code in order:
        if any(_in_ring(longitude, latitude, ring) for ring in regions[code]):
            return code

    # Simplified polygons may miss points right at the border.
    best: tuple[float, str | None] = (math.inf, None)
    for code, rings in regions.items():
        for ring in rings:
            for x, y in ring:
                dist = math.hypot(x - longitude, y - latitude)
                if dist < best[0]:
                    best = (dist, code)
    return best[1] if best[0] <= _NEAREST_MAX_DEG else None
