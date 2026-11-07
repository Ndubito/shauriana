"""GeminiLLM model fallback, with a fake SDK client (no network)."""
from types import SimpleNamespace

import pytest
from google.genai import errors

from app import llm as llm_mod
from app.agents.negotiator import Move
from app.llm import GeminiLLM, LLMError

GOOD = Move(action="counter", qty=500, unit_price=0.11, delivery_date="2026-11-10", rationale="ok")


def busy(code=503):
    return errors.APIError(code, {"error": {"code": code, "status": "UNAVAILABLE", "message": "busy"}})


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        result = self.behaviour[model].pop(0)
        if isinstance(result, Exception):
            raise result
        usage = SimpleNamespace(prompt_token_count=10, candidates_token_count=5, thoughts_token_count=0)
        return SimpleNamespace(parsed=result, text="{}", usage_metadata=usage)


def make(behaviour, models):
    g = GeminiLLM.__new__(GeminiLLM)  # skip __init__: no real SDK client
    g.models, g.seed, g.usage = models, 7, llm_mod.Usage()
    g._client = SimpleNamespace(models=FakeModels(behaviour))
    return g


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)


def test_retries_once_then_falls_back_to_next_model():
    g = make({"primary": [busy(), busy()], "backup": [GOOD]}, ["primary", "backup"])
    assert g.generate("sys", "prompt", Move) == GOOD
    assert g._client.models.calls == ["primary", "primary", "backup"]
    assert g.usage.calls[0].model == "backup"


def test_transient_error_then_success_on_same_model():
    g = make({"primary": [busy(429), GOOD]}, ["primary", "backup"])
    assert g.generate("sys", "prompt", Move) == GOOD
    assert g._client.models.calls == ["primary", "primary"]


def test_all_models_busy_raises_llm_error():
    g = make({"a": [busy(), busy()], "b": [busy(), busy()]}, ["a", "b"])
    with pytest.raises(LLMError, match="all models unavailable"):
        g.generate("sys", "prompt", Move)


def test_non_retryable_error_raises_immediately():
    g = make({"a": [busy(400)], "b": [GOOD]}, ["a", "b"])
    with pytest.raises(LLMError):
        g.generate("sys", "prompt", Move)
    assert g._client.models.calls == ["a"]


def test_reply_not_matching_schema_raises():
    g = make({"a": [None]}, ["a"])
    with pytest.raises(LLMError, match="did not match the schema"):
        g.generate("sys", "prompt", Move)


def test_from_env_builds_model_list(monkeypatch):
    monkeypatch.setattr(GeminiLLM, "__init__", lambda self, key, models, seed=7: setattr(self, "models", models))
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("LLM_MODEL", "m1")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "m2, m1 ,m3")
    assert GeminiLLM.from_env().models == ["m1", "m2", "m3"]
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_FALLBACK_MODELS")
    assert GeminiLLM.from_env().models == [llm_mod.DEFAULT_MODEL, llm_mod.DEFAULT_FALLBACKS]
