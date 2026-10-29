# What: small helpers for page state: which job is open (URL ?job=3), which chat thread is
#       open (URL ?chat=chat-…, with a session fallback), which copilot tools are on, and when
#       the browser session started (for the usage badge).
# Why: Streamlit forgets st.session_state when the browser is refreshed, but the URL survives.
#      Keeping ids in the URL means a refresh reopens the same job or chat, and the link can be
#      bookmarked.
# Used by: app/main.py (tool toggles), app/ui/pages/1_board.py, 2_job_workspace.py,
#          3_copilot_chat.py.

import re
from datetime import UTC, datetime

import streamlit as st

from app.tools.registry import TOOL_REGISTRY, default_toggles

JOB_PARAM = "job"
CHAT_PARAM = "chat"
CHAT_KEY = "chat_thread_id"
TOOL_KEY_PREFIX = "tool_"
SESSION_START_KEY = "session_started_at"
TONE_KEY = "sidebar_tone"
_CHAT_ID_RE = re.compile(r"^chat-[0-9a-f]{12}$")
WORKSPACE_PAGE = "ui/pages/2_job_workspace.py"  # relative to app/main.py
BOARD_PAGE = "ui/pages/1_board.py"
ONBOARDING_PAGE = "ui/pages/0_onboarding.py"


def current_job_id() -> int | None:
    """The job id from the URL, or None if there is none (or it isn't a number)."""
    value = st.query_params.get(JOB_PARAM)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def select_job(job_id: int) -> None:
    """Show another job on the current page (updates the URL)."""
    st.query_params[JOB_PARAM] = str(job_id)


def open_job(job_id: int) -> None:
    """Go to the Job workspace for this job."""
    st.switch_page(WORKSPACE_PAGE, query_params={JOB_PARAM: str(job_id)})


def current_chat_id(new_id: str) -> str:
    """The open chat thread: from the URL, else this session, else `new_id` (a fresh one).

    Args:
        new_id: the id to use if no chat is open yet (from `copilot.new_thread_id()`).
    """
    from_url = st.query_params.get(CHAT_PARAM)
    thread_id = from_url if from_url and _CHAT_ID_RE.match(from_url) else None
    thread_id = thread_id or st.session_state.get(CHAT_KEY) or new_id
    select_chat(thread_id)
    return thread_id


def select_chat(thread_id: str) -> None:
    """Open another chat thread (e.g. "New chat")."""
    st.session_state[CHAT_KEY] = thread_id
    st.query_params[CHAT_PARAM] = thread_id


def tool_toggle_key(name: str) -> str:
    """The session_state key of a tool's sidebar toggle, e.g. "tool_company"."""
    return f"{TOOL_KEY_PREFIX}{name}"


def enabled_toggles() -> list[str]:
    """The tool toggles switched on in the sidebar (defaults until the user changes one)."""
    defaults = set(default_toggles())
    return [
        name
        for name in TOOL_REGISTRY
        if st.session_state.get(tool_toggle_key(name), name in defaults)
    ]


def session_started_at() -> datetime:
    """When this browser session started (the "This session" usage badge counts from here).

    A browser refresh starts a new session, so the badge starts again from zero.
    """
    return st.session_state.setdefault(SESSION_START_KEY, datetime.now(UTC))
