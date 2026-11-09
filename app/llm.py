"""Gemini client for the agents: one structured-output call per agent turn.

Uses the google-genai SDK's generate_content with a Pydantic response schema, so every
reply is JSON matching the schema (read from response.parsed). If a model is overloaded
(429/5xx) we move on to the next model in the list instead of failing the run.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

import truststore
from pydantic import BaseModel

DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_FALLBACKS = "gemini-3.5-flash"
RETRYABLE_CODES = {429, 500, 502, 503, 504}

log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


@dataclass
class CallStats:
    model: str
    seconds: float
    input_tokens: int
    output_tokens: int
    thought_tokens: int


@dataclass
class Usage:
    calls: list[CallStats] = field(default_factory=list)

    def summary(self) -> str:
        if not self.calls:
            return "no model calls"
        models = sorted({c.model for c in self.calls})
        return (f"{len(self.calls)} calls on {', '.join(models)} · "
                f"{sum(c.input_tokens for c in self.calls)} in / {sum(c.output_tokens for c in self.calls)} out / "
                f"{sum(c.thought_tokens for c in self.calls)} thinking tokens · "
                f"{sum(c.seconds for c in self.calls):.1f}s")


class GeminiLLM:
    def __init__(self, api_key: str, models: list[str], seed: int = 7):
        # Verify TLS with the OS trust store (HTTPS-scanning antivirus re-signs certs).
        truststore.inject_into_ssl()
        from google import genai  # imported here so offline tests don't need the SDK

        self._client = genai.Client(api_key=api_key)
        self.models = models
        self.seed = seed
        self.usage = Usage()

    @classmethod
    def from_env(cls) -> "GeminiLLM":
        key = os.getenv("LLM_API_KEY", "").strip()
        if not key:
            raise LLMError("LLM_API_KEY is not set (see .env.example)")
        primary = os.getenv("LLM_MODEL", "").strip() or DEFAULT_MODEL
        fallbacks = os.getenv("LLM_FALLBACK_MODELS", DEFAULT_FALLBACKS)
        models = [primary] + [m.strip() for m in fallbacks.split(",") if m.strip() and m.strip() != primary]
        return cls(key, models)

    def generate(self, system: str, prompt: str, schema: type[BaseModel]) -> BaseModel:
        from google.genai import errors, types

        last_error = None
        for model in self.models:
            for attempt in range(2):
                start = time.monotonic()
                try:
                    resp = self._client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system,
                            response_mime_type="application/json",
                            response_schema=schema,
                            temperature=0,
                            seed=self.seed,
                            http_options=types.HttpOptions(
                                timeout=60_000,
                                retry_options=types.HttpRetryOptions(attempts=1),
                            ),
                        ),
                    )
                except errors.APIError as e:
                    last_error = f"{model}: {e.code} {e.status}"
                    log.warning("%s (attempt %d, %.1fs)", last_error, attempt + 1, time.monotonic() - start)
                    if e.code in RETRYABLE_CODES:
                        if attempt == 0:
                            time.sleep(1.5)
                            continue
                        break  # try the next model
                    raise LLMError(last_error) from e
                meta = resp.usage_metadata
                stats = CallStats(
                    model, time.monotonic() - start,
                    getattr(meta, "prompt_token_count", 0) or 0,
                    getattr(meta, "candidates_token_count", 0) or 0,
                    getattr(meta, "thoughts_token_count", 0) or 0,
                )
                self.usage.calls.append(stats)
                log.info("%s answered in %.1fs (%d in / %d out / %d thinking)", model, stats.seconds,
                         stats.input_tokens, stats.output_tokens, stats.thought_tokens)
                if resp.parsed is None:
                    raise LLMError(f"{model}: reply did not match the schema: {(resp.text or '')[:200]!r}")
                return resp.parsed
        raise LLMError(f"all models unavailable ({last_error})")
