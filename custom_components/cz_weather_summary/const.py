"""Constants for the CZ Weather Summary integration."""

from __future__ import annotations

DOMAIN = "cz_weather_summary"

CONF_API_KEY = "api_key"
CONF_MODEL = "model"
CONF_PROMPT = "prompt"
CONF_UPDATE_TIME = "update_time"
CONF_LOCATIONS = "locations"
CONF_NAME = "name"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_REGION = "region"

MAX_LOCATIONS = 3

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_UPDATE_TIME = "06:00:00"

SERVICE_REFRESH = "refresh"
ATTR_ENTRY_ID = "entry_id"

STORAGE_VERSION = 1

TIMEZONE = "Europe/Prague"

CHMI_FORECAST_URL = "https://opendata.chmi.cz/meteorology/weather/forecast/now/"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPENAI_BASE_URL = "https://api.openai.com/v1"

ATTRIBUTION = "Data: ČHMÚ (opendata.chmi.cz), model ALADIN přes Open-Meteo"

# CHMI regional forecast codes -> region names.
REGIONS: dict[str, str] = {
    "RPPH": "Praha",
    "RPSC": "Středočeský kraj",
    "RPCB": "Jihočeský kraj",
    "RPPL": "Plzeňský kraj",
    "RPKV": "Karlovarský kraj",
    "RPUL": "Ústecký kraj",
    "RPLB": "Liberecký kraj",
    "RPHK": "Královéhradecký kraj",
    "RPPU": "Pardubický kraj",
    "RPVY": "Kraj Vysočina",
    "RPJM": "Jihomoravský kraj",
    "RPOL": "Olomoucký kraj",
    "RPZL": "Zlínský kraj",
    "RPMS": "Moravskoslezský kraj",
}

REGION_AUTO = "auto"

DEFAULT_PROMPT = """Jsi meteorologický asistent pro domácí dashboard. Dostaneš JSON s dnešní \
předpovědí počasí z ČHMÚ pro 1 až 3 místa: slovní předpověď meteorologů pro kraj \
a hodinová čísla z modelu ALADIN pro konkrétní souřadnice.

Napiš česky, věcně a přátelsky:
- "short": jedna krátká věta (max 200 znaků) – to nejdůležitější o dnešku.
- "summary": souhrn na 2–5 vět. Popiš průběh dne (ráno, odpoledne, večer), teploty, \
srážky a vítr. Pokud se místa výrazně liší, uveď rozdíly a pojmenuj místa. Výstrahy \
a nebezpečné jevy zmiň vždy a na začátku.
- "clothing": konkrétní doporučení, co si vzít na sebe (vrstvy, bunda, deštník, \
čepice a rukavice, sluneční brýle, krém na opalování při UV indexu 3 a víc). Zohledni \
pocitovou teplotu a rozdíl mezi ránem a odpolednem.

Nevymýšlej si údaje, které v datech nejsou. Nepoužívej markdown nadpisy."""
