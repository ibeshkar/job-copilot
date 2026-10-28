# What: the Streamlit entry point. Run it with `streamlit run app/main.py`.
# Why: it sets up the things every page needs (logging, database tables, the sidebar with the
#      copilot's tool toggles, a warning if the API key is missing) and then hands control to
#      the page the user picked.
# Used by: Streamlit only. Pages live in app/ui/pages/ and are registered below.

import streamlit as st

from app.config import get_settings
from app.db.repo import init_db
from app.memory.store import get_profile
from app.tools.registry import TOOL_REGISTRY, default_toggles, missing_keys
from app.ui.state import tool_toggle_key
from app.utils.logging import configure_logging, get_logger

st.set_page_config(page_title="Job Application Copilot", page_icon="🧭", layout="wide")


@st.cache_resource
def _startup() -> None:
    """One-time setup per server process (Streamlit reruns this file on every click)."""
    configure_logging()
    init_db()
    get_logger(__name__).info("App started, database ready")


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

# The app stays usable for browsing without a key; only AI features need it (PROJECT_PLAN.md §10).
if not settings.has_llm_key:
    st.warning(
        "**No OpenRouter API key found.** You can look around, but AI features won't work yet. "
        "Set `OPENROUTER_API_KEY` in the `.env` file and restart the app.",
        icon="🔑",
    )

nav.run()
