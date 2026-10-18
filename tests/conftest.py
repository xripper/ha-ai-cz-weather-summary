"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_text(name: str) -> str:
    """Load a fixture file as text."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def load_json(name: str) -> Any:
    """Load a JSON fixture."""
    return json.loads(load_text(name))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return
