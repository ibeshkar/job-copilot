# What: the Job workspace: one job's details in tabs. Phase 4 builds the Fit tab, which renders
#       whatever the paused workflow is waiting for and sends the user's decision back.
# Why: this is the interrupt-rendering pattern from PROJECT_PLAN.md §7. On every rerun the page
#      reads the saved graph state, so refreshing the browser or restarting the app shows the
#      same step again.
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py.

import streamlit as st

from app.db.models import JobStatus
from app.services import applications
from app.services.applications import RunResult, WorkflowView
from app.ui.components import STATUS_LABELS, coming_soon, fit_card
from app.ui.state import BOARD_PAGE, current_job_id, select_job

STATUSES = [s.value for s in JobStatus]


def _run_with_status(label: str, action) -> RunResult:
    """Run a service call inside an st.status box that lists each finished step."""
    with st.status(label, expanded=True) as status:
        result = action(status.write)
        status.update(
            label="Done ✔" if result.ok else "Something went wrong",
            state="complete" if result.ok else "error",
        )
    return result


def _after(result: RunResult) -> None:
    if result.ok:
        st.rerun()
    st.error(result.message)


def _fit_review_actions(job_id: int) -> None:
    st.divider()
    st.markdown("##### What do you want to do?")
    col_continue, col_stop = st.columns(2)
    if col_continue.button("Continue to application ▶", type="primary", use_container_width=True):
        _after(
            _run_with_status(
                "Saving your decision…",
                lambda p: applications.submit_fit_review(job_id, "continue", on_progress=p),
            )
        )
    if col_stop.button("🚫 Not a fit", use_container_width=True):
        _after(
            _run_with_status(
                "Saving your decision…",
                lambda p: applications.submit_fit_review(job_id, "stop", on_progress=p),
            )
        )

    with st.expander("➕ Add context and re-check"):
        st.caption(
            "Tell me about experience that isn't in your documents, for example "
            '"I used Airflow at my last job to schedule daily reports". '
            "I'll search again and use your note as evidence."
        )
        note = st.text_area("Your context", key=f"context_{job_id}", max_chars=2000)
        if st.button("Re-check with this context", disabled=not note.strip()):
            _after(
                _run_with_status(
                    "Re-checking your fit…",
                    lambda p: applications.submit_fit_review(
                        job_id, "add_context", text=note, on_progress=p
                    ),
                )
            )


def _fit_tab(view: WorkflowView) -> None:
    if view.stage in ("not_started", "interrupted"):
        st.warning(
            "The analysis didn't finish, maybe because the AI service was busy. "
            "Your progress is saved; retrying continues from the last finished step.",
            icon="⏳",
        )
        if st.button("Retry analysis", type="primary"):
            _after(
                _run_with_status(
                    "Analysing fit…",
                    lambda p: applications.retry_analysis(view.job_id, on_progress=p),
                )
            )
        return

    if view.stage == "failed":
        for error in view.errors:
            st.error(error)
        st.caption("You can delete this job and add it again with the full posting text.")
        return

    if view.job is None or view.fit is None:
        st.info("No fit analysis yet.")
        return

    fit_card(view.job, view.requirements, view.fit, view.warnings, view.extra_context)

    if view.stage == "fit_review":
        _fit_review_actions(view.job_id)
    elif view.review_action == "stop":
        st.info("You marked this job as **not a fit**. It stays on your board for reference.")
    elif view.review_action == "continue":
        st.success(
            "You decided to apply. The next step, the **claim check**, comes in Phase 5.",
            icon="▶",
        )


# ---------------------------------------------------------------- page

st.title("🧭 Job workspace")

choices = applications.list_job_choices()
if not choices:
    st.info("No jobs yet. Add one on the Board first.", icon="🗂️")
    if st.button("Go to the board →", type="primary"):
        st.switch_page(BOARD_PAGE)
    st.stop()

ids = [job_id for job_id, _ in choices]
labels = dict(choices)
job_id = current_job_id()
if job_id not in ids:
    job_id = ids[0]
    select_job(job_id)

picked = st.selectbox("Job", ids, index=ids.index(job_id), format_func=labels.get)
if picked != job_id:
    select_job(picked)
    st.rerun()

card = applications.get_job_summary(job_id)
head, status_col, delete_col = st.columns([4, 2, 1])
head.markdown(f"### {card.title}\n{card.company}")
new_status = status_col.selectbox(
    "Board column", STATUSES, index=STATUSES.index(card.status), format_func=STATUS_LABELS.get
)
if new_status != card.status:
    applications.change_status(job_id, new_status)
    st.rerun()
with delete_col.popover("🗑️", help="Delete this job"):
    st.write("Delete this job and its analysis? This can't be undone.")
    if st.button("Delete", type="primary"):
        applications.delete_job(job_id)
        st.query_params.clear()
        st.switch_page(BOARD_PAGE)

view = applications.get_workflow_view(job_id)
fit_tab, claims_tab, docs_tab, company_tab, interview_tab, notes_tab = st.tabs(
    ["🎯 Fit", "✅ Claim check", "📝 Documents", "🏢 Company", "🎤 Interview prep", "🗒️ Notes"]
)
with fit_tab:
    _fit_tab(view)
with claims_tab:
    coming_soon(5, "Confirm, edit or remove each claim about you before anything is written.")
with docs_tab:
    coming_soon(5, "Tailored CV bullets and a cover letter, editable, with DOCX export.")
with company_tab:
    coming_soon(6, "Company research with sources.")
with interview_tab:
    coming_soon(8, "A mock interview built from this job's requirements and your gaps.")
with notes_tab:
    coming_soon(5, "Draft versions, notes and your next action.")
