# What: the work behind the developer settings page: check the password, save or reset the
#       model / temperature / max-token overrides, list the prompts, and read the raw graph
#       state of a job.
# Why: the page only draws. Saving overrides must also rebuild the cached graphs, because each
#      graph creates its LLM once when it is built.
# Used by: app/ui/pages/9_dev_settings.py, tests/test_dev_settings.py.

import hmac
import json

from app.agent.prompts import PROMPT_VERSION, all_prompts
from app.config import DEV_OVERRIDES_FILE, LLMOverrides, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_PASSWORD = "change-me"  # the value in .env.example


def check_password(attempt: str) -> bool:
    """True if `attempt` matches APP_DEV_PASSWORD (constant-time compare: no timing attacks)."""
    expected = get_settings().app_dev_password.get_secret_value()
    return hmac.compare_digest(attempt.encode(), expected.encode())


def uses_default_password() -> bool:
    """True while APP_DEV_PASSWORD is still the public default from .env.example."""
    return get_settings().app_dev_password.get_secret_value() == DEFAULT_PASSWORD


def _reload() -> None:
    """Forget the cached settings and graphs, so the next run builds them with the new model."""
    from app.agent.application_graph import get_application_graph
    from app.agent.copilot_graph import get_copilot_graph
    from app.agent.interview_graph import get_interview_graph

    for cached in (get_settings, get_application_graph, get_copilot_graph, get_interview_graph):
        cached.cache_clear()


def save_llm_overrides(model: str, temperature: float, max_tokens: int) -> LLMOverrides:
    """Save the developer's model choice; it applies to every run from now on (all sessions).

    Raises:
        pydantic.ValidationError: a value is out of range (e.g. temperature 3).
    """
    overrides = LLMOverrides(
        default_model=model.strip(), temperature=temperature, max_tokens=max_tokens
    )
    path = get_settings().runtime_dir / DEV_OVERRIDES_FILE
    path.write_text(overrides.model_dump_json(indent=2))
    logger.info("Developer settings saved: %s", overrides.model_dump())
    _reload()
    return overrides


def reset_llm_overrides() -> None:
    """Go back to the values from `.env`."""
    (get_settings().runtime_dir / DEV_OVERRIDES_FILE).unlink(missing_ok=True)
    logger.info("Developer settings reset to .env values")
    _reload()


def has_overrides() -> bool:
    """True if the developer settings currently override `.env`."""
    return (get_settings().runtime_dir / DEV_OVERRIDES_FILE).exists()


def prompts() -> tuple[str, dict[str, str]]:
    """(PROMPT_VERSION, {prompt name: text}) for the read-only prompt viewer."""
    return PROMPT_VERSION, all_prompts()


def raw_graph_state(job_id: int) -> dict:
    """A job's saved workflow state as plain JSON-safe data (for debugging).

    Returns:
        {"next": [...], "interrupts": [...], "values": {...}}; Pydantic objects become dicts.
    """
    from app.services.applications import get_graph, job_config

    state = get_graph().get_state(job_config(job_id))

    def plain(value: object) -> object:
        return json.loads(json.dumps(value, default=_to_jsonable))

    return {
        "next": list(state.next),
        "interrupts": [plain(i.value) for task in state.tasks for i in task.interrupts],
        "values": plain(state.values or {}),
    }


def _to_jsonable(value: object) -> object:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)
