# What: the "Job workspace" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 4 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("🧭 Job workspace")
coming_soon(
    phase=4,
    what=(
        "Everything about one job: fit analysis with evidence, claim check, tailored "
        "documents, company research and interview prep."
    ),
)
