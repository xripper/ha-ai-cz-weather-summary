"""Minimal OpenAI client for generating the weather summary."""

from __future__ import annotations

import json
import re
from typing import Any

import aiohttp

from .const import OPENAI_BASE_URL

TIMEOUT = aiohttp.ClientTimeout(total=90)

# Model families that can't do chat text generation.
_EXCLUDE = re.compile(
    r"audio|realtime|transcribe|tts|image|search|embedding|moderation|instruct|codex|dall-e|whisper"
    r"|-pro\b|deep-research|computer-use"
)
# Reasoning models: keep latency and cost low.
_REASONING = re.compile(r"^(gpt-5|o\d)")
_INCLUDE = re.compile(r"^(gpt-|o\d|chatgpt-)")
PREFERRED_MODELS = ("gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini")

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "short": {"type": "string", "description": "Jedna krátká věta, max 200 znaků."},
        "summary": {"type": "string", "description": "Souhrn počasí na dnešek."},
        "clothing": {"type": "string", "description": "Doporučení, co si vzít na sebe."},
    },
    "required": ["short", "summary", "clothing"],
    "additionalProperties": False,
}


class OpenAIError(Exception):
    """Generic OpenAI API error."""


class OpenAIAuthError(OpenAIError):
    """Invalid API key."""


def chat_models(model_ids: list[str]) -> list[str]:
    """Filter model ids to those usable for chat completions."""
    return sorted(m for m in model_ids if _INCLUDE.match(m) and not _EXCLUDE.search(m))


def default_model(models: list[str], fallback: str) -> str:
    """Pick a sensible default model from the available ones."""
    for model in PREFERRED_MODELS:
        if model in models:
            return model
    return next((m for m in models if "mini" in m), fallback)


class OpenAIClient:
    """Tiny async client for the OpenAI REST API."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        """Initialize the client."""
        self._session = session
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                OPENAI_BASE_URL + path,
                headers=self._headers,
                json=payload,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise OpenAIAuthError("Invalid OpenAI API key")
                body = await resp.text()
                if resp.status >= 400:
                    raise OpenAIError(f"OpenAI HTTP {resp.status}: {_error_message(body)}")
                return json.loads(body)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise OpenAIError(f"Cannot connect to OpenAI: {err}") from err
        except ValueError as err:
            raise OpenAIError("Invalid JSON from OpenAI") from err

    async def async_list_models(self) -> list[str]:
        """Return available chat model ids (also validates the API key)."""
        data = await self._request("GET", "/models")
        return chat_models([m["id"] for m in data.get("data", []) if "id" in m])

    async def async_summarize(
        self, model: str, prompt: str, weather: dict[str, Any]
    ) -> dict[str, str]:
        """Generate the summary; returns dict with short, summary and clothing."""
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(weather, ensure_ascii=False)},
        ]
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            **({"reasoning_effort": "low"} if _REASONING.match(model) else {}),
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "weather_summary",
                    "strict": True,
                    "schema": SUMMARY_SCHEMA,
                },
            },
        }
        try:
            data = await self._request("POST", "/chat/completions", request)
        except OpenAIAuthError:
            raise
        except OpenAIError as err:
            if "response_format" not in str(err) and "json_schema" not in str(err):
                raise
            # Older models only support plain JSON mode.
            request = {
                **request,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": prompt
                        + '\n\nOdpověz pouze JSON objektem s klíči "short", "summary" a "clothing".',
                    },
                    messages[1],
                ],
            }
            data = await self._request("POST", "/chat/completions", request)
        return parse_summary(data)


def _error_message(body: str) -> str:
    try:
        return json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return body[:300]


def parse_summary(data: dict[str, Any]) -> dict[str, str]:
    """Extract the summary JSON from a chat completion response."""
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as err:
        raise OpenAIError("Unexpected OpenAI response format") from err
    if message.get("refusal"):
        raise OpenAIError(f"Model refused: {message['refusal']}")
    content = message.get("content") or ""
    try:
        result = json.loads(content)
    except ValueError as err:
        raise OpenAIError(f"Model did not return JSON: {content[:200]}") from err
    if not isinstance(result, dict):
        raise OpenAIError("Model returned unexpected JSON")
    summary = {key: str(result.get(key) or "").strip() for key in ("short", "summary", "clothing")}
    if not summary["short"] and not summary["summary"]:
        raise OpenAIError("Model returned an empty summary")
    return summary
