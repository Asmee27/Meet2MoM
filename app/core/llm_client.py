from __future__ import annotations

"""
llm_client.py
=============
Provider-agnostic thin wrapper for LLM JSON completions.
Supports:
  - "anthropic"
  - "gemini"
  - "openai"
  - "local" (e.g. Ollama, vLLM)
  - "stub" (test fixture, no network)

Guarantees:
  - Temperature = 0
  - Strips markdown ```json fences
  - Retries once on JSONDecodeError with a repair instruction
  - Retries up to 3 times on network / 5xx / 429 errors with exponential backoff
  - Never logs or leaks API keys
  - Never raises raw provider exceptions (wrapped in LLMClientError / LLMOutputError)
"""

import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional

from app.core import config

logger = logging.getLogger(__name__)

# Gemini default when LLM_MODEL is unset or a placeholder. Override with
# LLM_MODEL (see docs/phase-4/LLM_EXTRACTION.md); never hardcode it elsewhere.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
# LLM_MODEL values that mean "not configured" (the .env.example placeholder).
PLACEHOLDER_MODELS = {"", "mock-v1"}
REAL_PROVIDERS = ("gemini", "openai", "anthropic", "local")
PROVIDERS_NEEDING_KEY = ("gemini", "openai", "anthropic")


def resolve_api_key(provider: str) -> Optional[str]:
    """LLM_API_KEY wins; otherwise the provider's own variable."""
    provider_key = {
        "gemini": config.GEMINI_API_KEY,
        "openai": getattr(config, "OPENAI_API_KEY", None),
        "anthropic": getattr(config, "ANTHROPIC_API_KEY", None),
    }.get(provider)
    return (config.LLM_API_KEY or provider_key or None) or None


def resolve_model(provider: str) -> Optional[str]:
    """
    The configured LLM_MODEL, unless it is a placeholder. Only Gemini has a
    default (the roadmap's development provider); other providers must name
    their model explicitly rather than have one guessed for them.
    """
    configured = (config.LLM_MODEL or "").strip()
    if configured and configured not in PLACEHOLDER_MODELS:
        return configured
    if provider == "gemini":
        return DEFAULT_GEMINI_MODEL
    if provider == "stub":
        return configured or "mock-v1"
    return None


def configuration_problem(provider: str, api_key: Optional[str], model: Optional[str]) -> Optional[str]:
    """Why this provider configuration cannot make a real call, or None."""
    if provider not in REAL_PROVIDERS:
        return f"LLM_PROVIDER={provider} is not a supported provider ({', '.join(REAL_PROVIDERS)})"
    if provider in PROVIDERS_NEEDING_KEY and not api_key:
        return f"LLM_PROVIDER={provider} has no API key configured"
    if not model:
        return f"LLM_PROVIDER={provider} has no LLM_MODEL configured"
    return None


def list_gemini_models(api_key: str) -> list[str]:
    """Return all Gemini model names that support generateContent."""
    if not api_key:
        raise LLMClientError("Gemini API key is missing. Set LLM_API_KEY or GEMINI_API_KEY.")

    endpoint = "https://generativelanguage.googleapis.com/v1beta/models"
    headers = {"x-goog-api-key": api_key}
    req = urllib.request.Request(endpoint, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    models: list[str] = []
    for model in data.get("models", []):
        methods = model.get("supportedGenerationMethods", [])
        name = model.get("name", "")
        if "generateContent" in methods and name:
            models.append(name.split("/")[-1])
    return models


class LLMClientError(Exception):
    """Base exception for all LLM client errors."""
    pass


class LLMOutputError(LLMClientError):
    """Raised when LLM output cannot be parsed as valid JSON after repair."""
    pass


def _strip_fences(raw_text: str) -> str:
    """Strip ```json ... ``` markdown code fences from model output."""
    text = raw_text.strip()
    # Match ```json ... ``` or ``` ... ```
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)
    match = fence_pattern.match(text)
    if match:
        return match.group(1).strip()
    # Fallback strip
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _gemini_text(data: Any) -> str:
    """Text of the first Gemini candidate, or a clear error when there is none."""
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not candidates:
        block = ((data or {}).get("promptFeedback") or {}).get("blockReason") if isinstance(data, dict) else None
        reason = f" (blocked: {block})" if block else ""
        raise LLMOutputError(f"gemini returned no candidates{reason}")
    candidate = candidates[0] or {}
    parts = ((candidate.get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    if not text.strip():
        finish = candidate.get("finishReason") or "unknown"
        raise LLMOutputError(f"gemini returned no text (finishReason: {finish})")
    return text


class LLMClient:
    """
    Provider-agnostic thin wrapper for structured LLM completions.
    """

    def __init__(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.0,
        backoff_delays: Optional[List[float]] = None,
        timeout: Optional[float] = None,
    ):
        self.provider = (provider or config.LLM_PROVIDER or "stub").lower().strip()
        self.model = model or resolve_model(self.provider)
        self.api_key = api_key or resolve_api_key(self.provider)
        self.base_url = base_url or config.LLM_BASE_URL
        self.temperature = temperature  # always sent as 0.0 for structured JSON
        self.backoff_delays = backoff_delays if backoff_delays is not None else [1.0, 2.0, 4.0]
        self.timeout = float(timeout or getattr(config, "LLM_TIMEOUT_SECONDS", 60) or 60)

        # For "stub" provider in tests
        self.stub_responses: List[Any] = []
        self.call_count: int = 0

    def set_stub_response(self, response: Any) -> None:
        """Register a fixture response for stub mode."""
        self.stub_responses.append(response)

    def _execute_provider_call(self, system: str, user: str, max_tokens: int) -> str:
        """Execute a single provider call without retry logic."""
        if self.provider == "stub":
            if "PYTEST_CURRENT_TEST" not in os.environ and not os.environ.get("TESTING"):
                raise RuntimeError(
                    "LLM_PROVIDER=stub cannot be used outside of tests. "
                    "Configure a valid provider (e.g., gemini, openai) or use EXTRACTOR=rules."
                )
            self.call_count += 1
            if self.stub_responses:
                resp = self.stub_responses.pop(0)
                if isinstance(resp, str):
                    return resp
                return json.dumps(resp)
            return json.dumps({"decisions": [], "actions": []})

        problem = configuration_problem(self.provider, self.api_key, self.model)
        if problem:
            raise LLMClientError(problem)

        if self.provider in ("openai", "local"):
            default_base = "https://api.openai.com/v1" if self.provider == "openai" else "http://localhost:11434/v1"
            endpoint = f"{(self.base_url or default_base).rstrip('/')}/chat/completions"
            headers = {
                "Content-Type": "application/json",
            }
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            payload = {
                "model": self.model,
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMOutputError(f"{self.provider} response had no message content") from exc

        if self.provider == "anthropic":
            endpoint = f"{(self.base_url or 'https://api.anthropic.com/v1').rstrip('/')}/messages"
            headers = {
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            if self.api_key:
                headers["x-api-key"] = self.api_key

            payload = {
                "model": self.model,
                "temperature": 0.0,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [
                    {"role": "user", "content": user},
                ],
            }
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_data, headers=headers, method="POST")

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            blocks = data.get("content", []) if isinstance(data, dict) else []
            text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
            if not "".join(text_parts).strip():
                raise LLMOutputError("anthropic response had no text content")
            return "".join(text_parts)

        if self.provider == "gemini":
            if not self.api_key:
                raise LLMClientError(
                    "Gemini API key is missing. Set LLM_API_KEY or GEMINI_API_KEY."
                )
            endpoint = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{self.model}:generateContent"
            )
            headers = {
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            }
            payload = {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": system + "\n\n" + user}],
                    }
                ],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            }
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=req_data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _gemini_text(data)

        raise LLMClientError(f"Unsupported LLM provider: '{self.provider}'")

    def _call_with_retries(self, system: str, user: str, max_tokens: int) -> str:
        """Retry up to 3 times on network / 5xx / 429 errors."""
        max_attempts = len(self.backoff_delays) + 1
        last_error = None

        for attempt in range(max_attempts):
            try:
                return self._execute_provider_call(system, user, max_tokens)
            except urllib.error.HTTPError as e:
                last_error = e
                # Retry on 429 and 5xx
                if e.code in (429, 500, 502, 503, 504) and attempt < len(self.backoff_delays):
                    delay = self.backoff_delays[attempt]
                    logger.warning("LLM call attempt %d failed with HTTP %d. Retrying in %ss...", attempt + 1, e.code, delay)
                    time.sleep(delay)
                    continue
                body_msg = ""
                try:
                    body_msg = e.read().decode("utf-8", "replace")
                except Exception:
                    pass
                # Provider error bodies explain the problem (bad model, quota);
                # keep them short. They never contain the request's API key.
                body_msg = " ".join(body_msg.split())[:300]
                raise LLMClientError(f"LLM provider HTTP {e.code} error: {body_msg}") from e
            except TimeoutError as e:
                last_error = e
                if attempt < len(self.backoff_delays):
                    delay = self.backoff_delays[attempt]
                    logger.warning("LLM call timed out on attempt %d. Retrying in %ss...", attempt + 1, delay)
                    time.sleep(delay)
                    continue
                raise LLMClientError(f"LLM request timed out after {self.timeout:g}s") from e
            except urllib.error.URLError as e:
                last_error = e
                if isinstance(e.reason, TimeoutError) and attempt >= len(self.backoff_delays):
                    raise LLMClientError(f"LLM request timed out after {self.timeout:g}s") from e
                if attempt < len(self.backoff_delays):
                    delay = self.backoff_delays[attempt]
                    logger.warning("LLM call network error on attempt %d. Retrying in %ss...", attempt + 1, delay)
                    time.sleep(delay)
                    continue
                raise LLMClientError(f"LLM network error: {e.reason}") from e
            except Exception as e:
                if isinstance(e, LLMClientError):
                    raise
                raise LLMClientError(f"LLM call failed: {e}") from e

        raise LLMClientError(f"LLM call failed after retries: {last_error}")

    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 2000,
    ) -> Dict[str, Any]:
        """
        Produce a validated JSON response dictionary from the LLM.
        Strips markdown fences, and retries once with repair instructions on JSONDecodeError.
        """
        raw_output = self._call_with_retries(system, user, max_tokens)
        stripped = _strip_fences(raw_output)

        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list):
                return {"items": parsed}
            return {"result": parsed}
        except json.JSONDecodeError as primary_err:
            logger.warning("Initial response was not valid JSON (%s). Retrying with repair instruction...", primary_err)

            # Retry once with repair instruction appended
            repair_user = (
                f"{user}\n\n"
                "IMPORTANT: Your previous response was not valid JSON. "
                "Output ONLY a raw, syntactically valid JSON object. Do not include markdown code fences or conversational text."
            )
            try:
                repair_raw = self._call_with_retries(system, repair_user, max_tokens)
                repair_stripped = _strip_fences(repair_raw)
                repaired = json.loads(repair_stripped)
                if isinstance(repaired, dict):
                    return repaired
                if isinstance(repaired, list):
                    return {"items": repaired}
                return {"result": repaired}
            except Exception as repair_err:
                raise LLMOutputError(
                    f"LLM output could not be parsed as valid JSON after repair: {repair_err}"
                ) from repair_err
