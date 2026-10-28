# What: the "Copilot chat" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 6 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("💬 Copilot chat")
coming_soon(
    phase=6,
    what=(
        "Ask questions about your job search. The copilot can search your experience, "
        "research companies and update your tracker."
    ),
)
