# What: the "What I know about you" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 7 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("🧠 What I know about you")
coming_soon(
    phase=7,
    what=(
        "Your profile, preferences, confirmed facts and learned style rules. You can edit"
        " or delete each one."
    ),
)
