# What: the Streamlit entry point. Run it with `streamlit run app/main.py`.
# Why: it sets up the things every page needs (logging, database tables, LangSmith tracing,
#      the sidebar with the usage badge and the copilot's tool toggles, a banner if the API key
#      is missing or rejected) and then hands control to the page the user picked. A last
#      safety net turns any unexpected error into a friendly message (no stack traces, §10).
# Used by: Streamlit only. Pages live in app/ui/pages/ and are registered below.

import streamlit as st

from app.config import get_settings
from app.db.repo import init_db
from app.memory.store import get_profile
from app.services import usage
from app.services.observability import KeyStatus, check_llm_key, configure_tracing
from app.tools.registry import TOOL_REGISTRY, default_toggles, missing_keys
from app.ui.state import session_started_at, tool_toggle_key
from app.utils.errors import friendly_message
from app.utils.logging import configure_logging, get_logger, log_exception

st.set_page_config(page_title="Job Application Copilot", page_icon="🧭", layout="wide")


@st.cache_resource
def _startup() -> None:
    """One-time setup per server process (Streamlit reruns this file on every click)."""
    configure_logging()
    init_db()
    tracing = configure_tracing()
    get_logger(__name__).info("App started, database ready, LangSmith tracing %s", tracing)


# Checked at most every 5 minutes, not on every click: it's a network call.
@st.cache_data(ttl=300, show_spinner=False)
def _key_status() -> KeyStatus:
    return check_llm_key()


_startup()
settings = get_settings()
profile = get_profile()
# New users land on the wizard; once a profile exists the board is the home page.
onboarded = profile is not None

# Paths are relative to this file.
pages = {
    "Start": [
        st.Page("ui/pages/0_onboarding.py", title="Get started", icon="👋", default=not onboarded),
    ],
    "Applications": [
        st.Page("ui/pages/1_board.py", title="Board", icon="🗂️", default=onboarded),
        st.Page("ui/pages/2_job_workspace.py", title="Job workspace", icon="🧭"),
        st.Page("ui/pages/3_copilot_chat.py", title="Copilot chat", icon="💬"),
    ],
    "You": [
        st.Page("ui/pages/4_memory.py", title="What I know about you", icon="🧠"),
        st.Page("ui/pages/5_help.py", title="Help", icon="❓"),
        st.Page("ui/pages/9_dev_settings.py", title="Developer settings", icon="🛠️"),
    ],
}
nav = st.navigation(pages)

with st.sidebar:
    st.caption("Job Application Copilot")
    if profile:
        st.markdown(f"👤 **{profile.name}**")
    st.caption(f"Model: `{settings.default_model}`")

    # Usage badge (§11). Filled in after the page has run, so it includes this run's actions.
    usage_box = st.container()

    # Tool toggles (S2): what the copilot chat may use. Read by app/ui/state.enabled_toggles().
    with st.expander("🔧 Copilot tools"):
        defaults = default_toggles()
        for name, spec in TOOL_REGISTRY.items():
            missing = missing_keys(spec)
            st.toggle(
                spec.label,
                value=name in defaults,
                key=tool_toggle_key(name),
                disabled=bool(missing),
                help=spec.description,
            )
            if missing:
                st.caption(f"Off: set {' and '.join(missing)} in `.env` to use it.")

# The app stays usable for browsing without a working key; only AI features need it (§10).
key_status = _key_status()
if not key_status.ok:
    st.warning(key_status.message, icon="🔑")


def _usage_badge() -> None:
    session_usage = usage.usage_since(session_started_at())
    with usage_box:
        st.markdown(f"💰 This session: **{session_usage.label()}**")
        if last := usage.last_action():
            action, last_usage = last
            st.caption(f"Last AI action: {action} · {last_usage.label()}")
        if warning := usage.budget_warning(session_usage, settings.session_token_budget):
            st.warning(warning, icon="💸")


try:
    nav.run()
except Exception as exc:  # st.rerun / st.switch_page are BaseExceptions and pass through
    log_exception(get_logger(__name__), "Page crashed", page=nav.title)
    st.error(f"Sorry, this page ran into a problem. {friendly_message(exc)}", icon="😵")
    st.button("🔄 Reload the page")
_usage_badge()
