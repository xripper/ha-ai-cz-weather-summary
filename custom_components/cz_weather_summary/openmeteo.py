"""CHMI ALADIN point forecast via the Open-Meteo API."""

from __future__ import annotations

from collections import Counter
from typing import Any

import aiohttp

from .const import OPEN_METEO_URL, TIMEZONE

HOURLY = (
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "uv_index",
)
DAILY = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "sunrise",
    "sunset",
)

# Parts of the day: (name, first hour, last hour exclusive).
DAY_PARTS = (
    ("rano", 6, 9),
    ("dopoledne", 9, 12),
    ("odpoledne", 12, 18),
    ("vecer", 18, 22),
)

WMO_CODES = {
    0: "jasno",
    1: "skoro jasno",
    2: "polojasno",
    3: "zataženo",
    45: "mlha",
    48: "mlha s námrazou",
    51: "slabé mrholení",
    53: "mrholení",
    55: "silné mrholení",
    56: "mrznoucí mrholení",
    57: "silné mrznoucí mrholení",
    61: "slabý déšť",
    63: "déšť",
    65: "silný déšť",
    66: "mrznoucí déšť",
    67: "silný mrznoucí déšť",
    71: "slabé sněžení",
    73: "sněžení",
    75: "silné sněžení",
    77: "sněhová zrna",
    80: "slabé přeháňky",
    81: "přeháňky",
    82: "silné přeháňky",
    85: "sněhové přeháňky",
    86: "silné sněhové přeháňky",
    95: "bouřka",
    96: "bouřka s kroupami",
    99: "silná bouřka s kroupami",
}


class OpenMeteoError(Exception):
    """Error fetching Open-Meteo data."""


def _values(series: list[Any] | None, idx: list[int]) -> list[float]:
    if not series:
        return []
    return [series[i] for i in idx if i < len(series) and series[i] is not None]


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 1)


def _part(hourly: dict[str, list[Any]], idx: list[int]) -> dict[str, Any]:
    temp = _values(hourly.get("temperature_2m"), idx)
    feels = _values(hourly.get("apparent_temperature"), idx)
    precip = _values(hourly.get("precipitation"), idx)
    prob = _values(hourly.get("precipitation_probability"), idx)
    clouds = _values(hourly.get("cloud_cover"), idx)
    wind = _values(hourly.get("wind_speed_10m"), idx)
    gusts = _values(hourly.get("wind_gusts_10m"), idx)
    codes = [int(c) for c in _values(hourly.get("weather_code"), idx)]

    part: dict[str, Any] = {
        "teplota_min": _round(min(temp)) if temp else None,
        "teplota_max": _round(max(temp)) if temp else None,
        "pocitova_min": _round(min(feels)) if feels else None,
        "srazky_mm": _round(sum(precip)) if precip else None,
        "pravdepodobnost_srazek_max": max(prob) if prob else None,
        "oblacnost_prumer": round(sum(clouds) / len(clouds)) if clouds else None,
        "vitr_prumer_ms": _round(sum(wind) / len(wind)) if wind else None,
        "narazy_max_ms": _round(max(gusts)) if gusts else None,
    }
    if codes:
        # Most significant weather wins: higher WMO codes are more severe.
        worst = max(codes)
        common = Counter(codes).most_common(1)[0][0]
        part["pocasi"] = WMO_CODES.get(common, str(common))
        if worst != common:
            part["nejhorsi_pocasi"] = WMO_CODES.get(worst, str(worst))
    return {k: v for k, v in part.items() if v is not None}


def summarize(payload: dict[str, Any], day: str) -> dict[str, Any]:
    """Aggregate an Open-Meteo response into parts of the given day (YYYY-MM-DD)."""
    hourly = payload.get("hourly") or {}
    times: list[str] = hourly.get("time") or []

    parts: dict[str, Any] = {}
    for name, start, end in DAY_PARTS:
        idx = [
            i
            for i, t in enumerate(times)
            if t.startswith(day) and start <= int(t[11:13]) < end
        ]
        if idx and (part := _part(hourly, idx)):
            parts[name] = part

    result: dict[str, Any] = {"nadmorska_vyska_m": payload.get("elevation")}
    daily = payload.get("daily") or {}
    try:
        di = (daily.get("time") or []).index(day)
    except ValueError:
        di = None
    if di is not None:
        mapping = {
            "teplota_max": "temperature_2m_max",
            "teplota_min": "temperature_2m_min",
            "srazky_mm": "precipitation_sum",
            "vychod_slunce": "sunrise",
            "zapad_slunce": "sunset",
        }
        for key, src in mapping.items():
            series = daily.get(src) or []
            if di < len(series) and series[di] is not None:
                value = series[di]
                result[key] = value[11:16] if src in ("sunrise", "sunset") else value

    day_idx = [i for i, t in enumerate(times) if t.startswith(day)]
    if uv := _values(hourly.get("uv_index"), day_idx):
        result["uv_index_max"] = _round(max(uv))
    result["casti_dne"] = parts
    return {k: v for k, v in result.items() if v is not None}


async def async_get_point_forecast(
    session: aiohttp.ClientSession, latitude: float, longitude: float
) -> dict[str, Any]:
    """Fetch today's hourly ALADIN forecast for coordinates."""
    params = {
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "models": "chmi_aladin_seamless",
        "hourly": ",".join(HOURLY),
        "daily": ",".join(DAILY),
        "timezone": TIMEZONE,
        "forecast_days": "1",
        "wind_speed_unit": "ms",
    }
    try:
        async with session.get(
            OPEN_METEO_URL, params=params, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            if resp.status != 200:
                raise OpenMeteoError(
                    f"Open-Meteo returned HTTP {resp.status}: {(await resp.text())[:200]}"
                )
            return await resp.json()
    except ValueError as err:
        raise OpenMeteoError(f"Invalid JSON from Open-Meteo: {err}") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise OpenMeteoError(f"Cannot fetch Open-Meteo forecast: {err}") from err
