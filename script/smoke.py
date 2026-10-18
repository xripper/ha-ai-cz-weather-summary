"""Live smoke test against real CHMI and Open-Meteo (and OpenAI if a key is set).

Usage: python -m script.smoke [lat,lon,name ...]
Set OPENAI_API_KEY (and optionally OPENAI_MODEL) to also generate the summary.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp

from custom_components.cz_weather_summary.chmi import ChmiClient, ChmiError, find_region
from custom_components.cz_weather_summary.coordinator import build_llm_input
from custom_components.cz_weather_summary.const import DEFAULT_PROMPT
from custom_components.cz_weather_summary.llm import OpenAIClient
from custom_components.cz_weather_summary.openmeteo import async_get_point_forecast, summarize
from homeassistant.util import dt as dt_util

DEFAULT_PLACES = ["50.088,14.42,Praha", "49.195,16.608,Brno"]


async def main(places: list[str]) -> None:
    now = dt_util.now(dt_util.get_time_zone("Europe/Prague"))
    async with aiohttp.ClientSession() as session:
        chmi = ChmiClient(session)
        locations = []
        for place in places:
            lat, lon, name = place.split(",", 2)
            region = find_region(float(lat), float(lon))
            loc = {"name": name, "region": region, "errors": []}
            loc["aladin"] = summarize(
                await async_get_point_forecast(session, float(lat), float(lon)),
                now.date().isoformat(),
            )
            if region:
                try:
                    loc["chmi"] = await chmi.async_get_forecast(region, now)
                except ChmiError as err:
                    loc["errors"].append(str(err))
            locations.append(loc)

        payload = build_llm_input(locations, now)
        print(json.dumps(payload, ensure_ascii=False, indent=2))

        if key := os.environ.get("OPENAI_API_KEY"):
            client = OpenAIClient(session, key)
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            result = await client.async_summarize(model, DEFAULT_PROMPT, payload)
            print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or DEFAULT_PLACES))
