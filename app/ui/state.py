# What: small helpers for "which job is open", kept in the page URL (?job=3).
# Why: Streamlit forgets st.session_state when the browser is refreshed, but the URL survives.
#      Keeping the job id in the URL means a refresh reopens the same job, and the link can be
#      bookmarked.
# Used by: app/ui/pages/1_board.py, app/ui/pages/2_job_workspace.py.

import streamlit as st

JOB_PARAM = "job"
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
