# What: tests for LangSmith wiring, run metadata and the startup key check
#       (app/services/observability.py).
# Why: tracing must only switch on with a key, every run must carry `prompt_version` and
#      `action`, and a missing, rejected or unreachable key must each give a clear banner.
# Used by: pytest.

import os

import httpx
import langsmith
import pytest
from pydantic import SecretStr

from app.agent.prompts import PROMPT_VERSION
from app.config import Settings
from app.services import observability
from app.services.observability import check_llm_key, configure_tracing, track_run


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_tracing_needs_flag_and_key(monkeypatch) -> None:
    # Registered with monkeypatch, so the test restores them afterwards.
    for name in ("TRACING", "API_KEY", "PROJECT", "HIDE_INPUTS", "HIDE_OUTPUTS"):
        monkeypatch.setenv(f"LANGSMITH_{name}", "")

    assert configure_tracing(_settings(langsmith_tracing=True)) is False
    assert os.environ["LANGSMITH_TRACING"] == "false"

    on = _settings(
        langsmith_tracing=True,
        langsmith_api_key=SecretStr("lsv2-test"),
        langsmith_project="demo",
        langsmith_hide_inputs=True,
    )
    assert configure_tracing(on) is True
    assert os.environ["LANGSMITH_PROJECT"] == "demo"
    assert os.environ["LANGSMITH_HIDE_INPUTS"] == "true"
    configure_tracing(_settings(langsmith_tracing=False))
    assert os.environ["LANGSMITH_TRACING"] == "false"


def test_runs_carry_prompt_version_and_action(app_db) -> None:
    """Inside track_run, LangSmith's tracing context holds our metadata and tag.

    LangChain copies this context onto every LangSmith trace started inside the block
    (graph runs, LLM calls, tools), so traces can be filtered by action or prompt version.
    """
    with track_run("draft", job_id=3):
        context = langsmith.get_tracing_context()

    assert context["metadata"] == {
        "prompt_version": PROMPT_VERSION,
        "action": "draft",
        "job_id": 3,
    }
    assert context["tags"] == ["draft"]
    assert not langsmith.get_tracing_context()["metadata"]  # cleared after the block


def _fake_get(status: int | None):
    def get(url, headers, timeout):
        if status is None:
            raise httpx.ConnectError("offline")
        assert url.endswith("/key") and headers["Authorization"].startswith("Bearer ")
        return httpx.Response(status, request=httpx.Request("GET", url))

    return get


@pytest.mark.parametrize(
    ("status", "ok", "words"),
    [(200, True, ""), (401, False, "rejected"), (None, False, "can't reach")],
)
def test_key_check(monkeypatch, status, ok, words) -> None:
    monkeypatch.setattr(observability.httpx, "get", _fake_get(status))
    settings = _settings(openrouter_api_key=SecretStr("sk-or-x"), startup_key_check=True)

    result = check_llm_key(settings)

    assert result.ok is ok
    assert words in result.message


def test_key_check_without_key_needs_no_network(monkeypatch) -> None:
    monkeypatch.setattr(observability.httpx, "get", _fake_get(None))

    result = check_llm_key(_settings(openrouter_api_key=None))

    assert not result.ok and "No OpenRouter API key" in result.message
