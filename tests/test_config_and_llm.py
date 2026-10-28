# What: tests for settings loading and the LLM factory.
# Why: a missing API key must give a friendly ConfigError, and the factory must pass the
#      model, temperature and max tokens from settings. No network calls are made.
# Used by: pytest.

import pytest
from pydantic import SecretStr

from app.agent.llm import get_llm
from app.config import MODEL_PRICING, Settings
from app.utils.errors import ConfigError, friendly_message


def _settings(**overrides: object) -> Settings:
    # _env_file=None: ignore the developer's real .env so tests are repeatable.
    return Settings(_env_file=None, **overrides)


def test_get_llm_without_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(ConfigError) as exc_info:
        get_llm(_settings())

    assert "API key" in friendly_message(exc_info.value)


def test_get_llm_uses_settings_and_overrides() -> None:
    settings = _settings(
        openrouter_api_key=SecretStr("sk-or-test"),
        default_model="openai/gpt-4o-mini",
        temperature=0.3,
    )

    llm = get_llm(settings)
    assert (llm.model_name, llm.temperature) == ("openai/gpt-4o-mini", 0.3)
    assert llm.openai_api_base == "https://openrouter.ai/api/v1"

    llm = get_llm(settings, model="openai/gpt-4.1-mini", temperature=0.0, max_tokens=200)
    assert (llm.model_name, llm.temperature, llm.max_tokens) == ("openai/gpt-4.1-mini", 0.0, 200)


def test_default_model_has_a_price() -> None:
    assert _settings().default_model in MODEL_PRICING


def test_friendly_message_hides_unexpected_errors() -> None:
    assert "Traceback" not in friendly_message(KeyError("internal detail"))
