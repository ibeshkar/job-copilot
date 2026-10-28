# What: small reusable Streamlit widgets shared by several pages.
# Why: keeps the page files short and makes empty/loading/error states look the same everywhere.
#      Later phases add the fit card, claim review, job card and usage badge here.
# Used by: the pages in app/ui/pages/.

import streamlit as st


def coming_soon(phase: int, what: str) -> None:
    """Show an empty state for a page that is planned but not built yet.

    Args:
        phase: the implementation phase (PROJECT_PLAN.md §12) that builds this page.
        what: one sentence describing what the page will do.
    """
    st.info(f"{what}\n\nThis page is built in **Phase {phase}**.", icon="🚧")
