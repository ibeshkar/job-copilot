# What: the "Developer settings" page (placeholder for now).
# Why: registered in app/main.py so the navigation works from Phase 1; the real page
#      is built in Phase 7 (PROJECT_PLAN.md §12).
# Used by: app/main.py (st.navigation).

import streamlit as st

from app.ui.components import coming_soon

st.title("🛠️ Developer settings")
coming_soon(
    phase=7,
    what="Password-protected: model, temperature, max tokens, prompt viewer and cost by action.",
)
