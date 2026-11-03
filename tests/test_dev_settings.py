# What: tests for app/services/dev_settings.py: the password check, model overrides that win
#       over .env and rebuild the graphs, the prompt list and the raw graph state viewer.
# Why: the developer settings page changes how every AI step runs, so saving, resetting and
#      bad values must behave predictably.
# Used by: pytest.

import pytest
from pydantic import SecretStr

from app.config import DEV_OVERRIDES_FILE, get_settings
from app.services import applications, dev_settings
from app.utils.errors import InvalidInputError
from tests.graph_fakes import POSTING


def test_password_check(app_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "app_dev_password", SecretStr("s3cret"))

    assert dev_settings.check_password("s3cret")
    assert not dev_settings.check_password("S3cret")
    assert not dev_settings.check_password("")
    assert not dev_settings.uses_default_password()


def test_overrides_win_over_env_and_rebuild_the_graphs(app_db, monkeypatch) -> None:
    from app.agent.copilot_graph import get_copilot_graph

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    before = get_copilot_graph()

    dev_settings.save_llm_overrides("openai/gpt-4.1-nano", 0.7, 900)

    settings = get_settings()
    assert (settings.default_model, settings.temperature, settings.max_tokens) == (
        "openai/gpt-4.1-nano",
        0.7,
        900,
    )
    assert dev_settings.has_overrides()
    assert get_copilot_graph() is not before  # rebuilt, so its LLM uses the new model

    dev_settings.reset_llm_overrides()
    assert get_settings().default_model != "openai/gpt-4.1-nano"
    assert not dev_settings.has_overrides()


def test_invalid_overrides_are_rejected(app_db) -> None:
    with pytest.raises(InvalidInputError) as caught:
        dev_settings.save_llm_overrides("openai/gpt-4o-mini", 5.0, 900)
    assert set(caught.value.errors) == {"temperature"}
    assert not dev_settings.has_overrides()


def test_broken_overrides_file_falls_back_to_env(app_db) -> None:
    (app_db / DEV_OVERRIDES_FILE).write_text("{not json")
    get_settings.cache_clear()

    assert get_settings().default_model  # the app still starts, with the .env value


def test_prompt_viewer_lists_every_prompt() -> None:
    version, prompts = dev_settings.prompts()

    assert version
    assert {"COPILOT_SYSTEM", "TOPIC_GUARD_SYSTEM", "DRAFT_WRITER_SYSTEM"} <= set(prompts)
    assert "TOOL_DESCRIPTIONS[update_tracker]" in prompts


def test_raw_graph_state_is_plain_json(fake_app) -> None:
    job_id = applications.add_job(POSTING).job_id

    state = dev_settings.raw_graph_state(job_id)

    assert state["next"] == ["fit_review"]
    assert state["interrupts"][0]["type"] == "fit_review"
    assert state["values"]["fit"]["score"] == 86
