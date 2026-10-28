# What: the Streamlit entry point. Run it with `streamlit run app/main.py`.
# Why: it sets up the things every page needs (logging, database tables, the sidebar, a warning
#      if the API key is missing) and then hands control to the page the user picked.
# Used by: Streamlit only. Pages live in app/ui/pages/ and are registered below.

import streamlit as st

from app.config import get_settings
from app.db.repo import init_db
from app.memory.store import get_profile
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

# The app stays usable for browsing without a key; only AI features need it (PROJECT_PLAN.md §10).
if not settings.has_llm_key:
    st.warning(
        "**No OpenRouter API key found.** You can look around, but AI features won't work yet. "
        "Set `OPENROUTER_API_KEY` in the `.env` file and restart the app.",
        icon="🔑",
    )

nav.run()
