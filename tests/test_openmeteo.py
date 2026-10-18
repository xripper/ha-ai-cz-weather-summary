"""Tests for the Open-Meteo aggregation."""

from __future__ import annotations

from custom_components.cz_weather_summary.openmeteo import summarize

from .conftest import load_json


def test_summarize() -> None:
    """Hourly data is grouped into parts of the day."""
    result = summarize(load_json("openmeteo_praha.json"), "2026-10-18")
    assert result["teplota_max"] == 15.3
    assert result["teplota_min"] == 8.9
    assert result["vychod_slunce"] == "07:28"
    assert "uv_index_max" not in result  # all null in fixture
    parts = result["casti_dne"]
    assert list(parts) == ["rano", "dopoledne", "odpoledne", "vecer"]
    assert parts["odpoledne"]["teplota_max"] == 15.3
    assert parts["rano"]["pocasi"] == "zataženo"
    assert parts["vecer"]["pocasi"] in {"zataženo", "polojasno"}


def test_summarize_wrong_day() -> None:
    """Missing day yields no parts instead of failing."""
    result = summarize(load_json("openmeteo_praha.json"), "2026-10-19")
    assert result["casti_dne"] == {}
    assert "teplota_max" not in result
