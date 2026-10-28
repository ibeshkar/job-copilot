# What: the "Board" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 4 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("🗂️ Board")
coming_soon(
    phase=4,
    what=(
        "Your applications as a board: Saved, Applied, Interview, Offer and Rejected. Add"
        " a job by pasting a URL or the job text."
    ),
)
