# What: LangSmith tracing setup, `track_run()` (run metadata `prompt_version` + `action` for
#       LangSmith, and token/cost tracking, around one user action), plus the startup check of
#       the OpenRouter key.
# Why: LangChain reads its tracing settings from environment variables, but our settings come
#      from `.env` through pydantic-settings, which doesn't export them. `configure_tracing()`
#      copies them over once at startup. The metadata lets you filter traces in LangSmith by
#      action ("draft") or by prompt version, e.g. to compare runs before and after a change.
# Used by: app/main.py (startup, banner), app/services/applications.py, app/services/copilot.py,
#          app/services/company.py, app/ui/pages/9_dev_settings.py.

import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx
import langsmith

from app.agent.prompts import PROMPT_VERSION
from app.config import Settings, get_settings
from app.services.usage import Usage, track_usage
from app.utils.logging import get_logger

logger = get_logger(__name__)

KEY_CHECK_TIMEOUT_S = 4


# ---------------------------------------------------------------- LangSmith


def configure_tracing(settings: Settings | None = None) -> bool:
    """Switch LangSmith tracing on or off from the settings. Returns True if it is on.

    Tracing needs both `LANGSMITH_TRACING=true` and a `LANGSMITH_API_KEY`. With
    `LANGSMITH_HIDE_INPUTS=true` the traces keep their structure, timing and token counts,
    but not the texts (CVs, postings, letters).
    """
    settings = settings or get_settings()
    enabled = settings.langsmith_tracing and settings.langsmith_api_key is not None
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    if enabled:
        os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = settings.langsmith_endpoint
        hide = "true" if settings.langsmith_hide_inputs else "false"
        os.environ["LANGSMITH_HIDE_INPUTS"] = hide
        os.environ["LANGSMITH_HIDE_OUTPUTS"] = hide
        logger.info("LangSmith tracing on (project %s)", settings.langsmith_project)
    return enabled


def tracing_status(settings: Settings | None = None) -> str:
    """One line for the developer settings page."""
    settings = settings or get_settings()
    if not settings.langsmith_tracing:
        return "Off (set LANGSMITH_TRACING=true in .env to turn it on)."
    if settings.langsmith_api_key is None:
        return "Off: LANGSMITH_TRACING is true, but LANGSMITH_API_KEY is missing."
    hidden = " Texts are hidden." if settings.langsmith_hide_inputs else ""
    return f"On: traces go to the project “{settings.langsmith_project}”.{hidden}"


def run_metadata(action: str, **extra: object) -> dict:
    """The metadata attached to every LangSmith trace of one action.

    Example: run_metadata("draft", job_id=3)
        → {"prompt_version": "2026-10-29.7", "action": "draft", "job_id": 3}
    """
    return {"prompt_version": PROMPT_VERSION, "action": action, **extra}


@contextmanager
def track_run(action: str, **extra: object) -> Iterator[Usage]:
    """Wrap one user action: tag its traces and count its tokens and cost.

    Every LLM call and graph run inside the block gets the metadata and the tag `action`
    in LangSmith (LangChain reads `langsmith.tracing_context`), and its tokens are logged
    to UsageLog under `action`.

    Example:
        with track_run("analyse_fit", job_id=3) as usage:
            graph.invoke(...)
    """
    with (
        langsmith.tracing_context(metadata=run_metadata(action, **extra), tags=[action]),
        track_usage(action) as usage,
    ):
        yield usage


# ---------------------------------------------------------------- OpenRouter key check


@dataclass
class KeyStatus:
    """Result of the startup key check. `message` is empty when all is well."""

    ok: bool
    message: str = ""


def check_llm_key(settings: Settings | None = None) -> KeyStatus:
    """Ask OpenRouter whether the API key works, without spending tokens (§10).

    Uses OpenRouter's `GET /key` endpoint. A missing key, a rejected key and an unreachable
    service each get their own message; any other answer counts as OK, so a change on
    OpenRouter's side never blocks the app.
    """
    settings = settings or get_settings()
    if not settings.has_llm_key:
        return KeyStatus(
            False,
            "**No OpenRouter API key found.** You can look around, but AI features won't work "
            "yet. Set `OPENROUTER_API_KEY` in the `.env` file and restart the app.",
        )
    if not settings.startup_key_check:
        return KeyStatus(True)
    try:
        response = httpx.get(
            f"{settings.openrouter_base_url.rstrip('/')}/key",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}"},
            timeout=KEY_CHECK_TIMEOUT_S,
        )
    except httpx.HTTPError as exc:
        logger.warning("OpenRouter key check failed: %s", exc)
        return KeyStatus(
            False,
            "**I can't reach OpenRouter.** Check your internet connection. You can browse your "
            "saved jobs; AI features will work again once the connection is back.",
        )
    if response.status_code in (401, 403):
        return KeyStatus(
            False,
            "**OpenRouter rejected your API key.** Check `OPENROUTER_API_KEY` in the `.env` "
            "file and restart the app.",
        )
    return KeyStatus(True)
