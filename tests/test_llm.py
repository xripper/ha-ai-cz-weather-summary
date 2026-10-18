"""Tests for the OpenAI client."""

from __future__ import annotations

import json

import pytest

from custom_components.cz_weather_summary.const import OPENAI_BASE_URL
from custom_components.cz_weather_summary.llm import (
    OpenAIAuthError,
    OpenAIClient,
    OpenAIError,
    chat_models,
    default_model,
    parse_summary,
)

COMPLETION = {
    "choices": [
        {
            "message": {
                "content": json.dumps(
                    {"short": "Polojasno, 15 °C.", "summary": "Souhrn.", "clothing": "Bunda."}
                )
            }
        }
    ]
}


def test_chat_models_filter() -> None:
    """Non-chat models are dropped."""
    ids = [
        "gpt-4o-mini",
        "gpt-4o-mini-tts",
        "text-embedding-3-small",
        "o4-mini",
        "dall-e-3",
        "gpt-5-mini",
        "gpt-5-pro",
        "o3-pro-2025-06-10",
    ]
    assert chat_models(ids) == ["gpt-4o-mini", "gpt-5-mini", "o4-mini"]
    assert default_model(chat_models(ids), "x") == "gpt-5-mini"
    assert default_model(["gpt-9", "gpt-9-mini"], "x") == "gpt-9-mini"
    assert default_model([], "fallback") == "fallback"


def test_parse_summary_errors() -> None:
    """Bad responses raise OpenAIError."""
    with pytest.raises(OpenAIError):
        parse_summary({"choices": [{"message": {"content": "not json"}}]})
    with pytest.raises(OpenAIError):
        parse_summary({"choices": [{"message": {"refusal": "no", "content": None}}]})
    with pytest.raises(OpenAIError):
        parse_summary({})
    empty = json.dumps({"short": "", "summary": "", "clothing": ""})
    with pytest.raises(OpenAIError):
        parse_summary({"choices": [{"message": {"content": empty}}]})


async def test_summarize(hass, aioclient_mock) -> None:
    """Summary is requested with a JSON schema and parsed."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(f"{OPENAI_BASE_URL}/chat/completions", json=COMPLETION)
    client = OpenAIClient(async_get_clientsession(hass), "sk-test")
    result = await client.async_summarize("gpt-5-mini", "prompt", {"mista": []})
    assert result == {"short": "Polojasno, 15 °C.", "summary": "Souhrn.", "clothing": "Bunda."}
    _, _, body, headers = aioclient_mock.mock_calls[0]
    assert body["response_format"]["type"] == "json_schema"
    assert "temperature" not in body
    assert body["reasoning_effort"] == "low"
    assert headers["Authorization"] == "Bearer sk-test"


async def test_auth_error(hass, aioclient_mock) -> None:
    """401 raises OpenAIAuthError."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.get(f"{OPENAI_BASE_URL}/models", status=401)
    client = OpenAIClient(async_get_clientsession(hass), "bad")
    with pytest.raises(OpenAIAuthError):
        await client.async_list_models()


async def test_json_object_fallback(hass, aioclient_mock) -> None:
    """Models without json_schema support get plain JSON mode."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    aioclient_mock.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        status=400,
        json={"error": {"message": "Invalid parameter: 'response_format' of type 'json_schema'"}},
    )
    client = OpenAIClient(async_get_clientsession(hass), "sk")
    # Both attempts hit the same mocked 400; we only check what was sent.
    with pytest.raises(OpenAIError):
        await client.async_summarize("gpt-4o-mini", "p", {})
    calls = aioclient_mock.mock_calls
    assert [c[2]["response_format"]["type"] for c in calls] == ["json_schema", "json_object"]
    assert "reasoning_effort" not in calls[0][2]
