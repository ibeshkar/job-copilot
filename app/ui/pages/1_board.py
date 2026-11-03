# What: the Board page: all jobs as cards in five columns (Saved → Applied → Interview →
#       Offer / Rejected) and the "➕ Add job" dialog that starts the fit analysis.
#       Until the user has a profile and documents, a "Let's get you set up" checklist replaces
#       the board and "➕ Add job" is disabled (app/services/readiness.py decides).
# Why: the tracker is the user's home base (feature M7) and the entry point for new jobs (M3).
#      Fit analysis can't work without a profile and documents, so we prevent the dead end
#      instead of warning after it, with one clear next step.
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py.

import streamlit as st

from app.config import JOB_TEXT_MAX_CHARS, JOB_TEXT_MIN_CHARS
from app.db.models import JobStatus
from app.services import applications, onboarding
from app.services.readiness import ADD_JOB_LOCKED_HELP, get_setup_status
from app.ui.components import (
    STATUS_LABELS,
    busy_button,
    clear_form_errors,
    fail_form,
    field_error,
    fit_badge,
    form_errors,
    progress_status,
    run_with_progress,
    salary_badge,
    setup_checklist,
    stage_badge,
)
from app.ui.state import ONBOARDING_DOCUMENTS_STEP, open_job, open_onboarding
from app.validation.models import JobInput, check_input
from app.validation.sanitize import md

STATUSES = [s.value for s in JobStatus]
DIALOG_OPEN = "add_job_dialog_open"
# Set while the checklist is on screen, so "✅ Setup complete" shows once when it's done.
SETUP_PENDING = "board_setup_pending"
FORM = "add_job"  # scope of the dialog's validation errors


def _close_dialog() -> None:
    st.session_state[DIALOG_OPEN] = False
    clear_form_errors(FORM)


# The dialog's open/closed state lives in session_state, so it stays open across reruns
# (e.g. while typing) and closes only when the user dismisses it or the analysis succeeds.
@st.dialog("➕ Add a job", width="large", on_dismiss=_close_dialog)
def add_job_dialog() -> None:
    st.caption(
        "Paste the full job posting, or a link to it. Links from sites that need a login "
        "(like LinkedIn) usually can't be read, so pasting the text is the safest option."
    )
    # Defence in depth: the button is disabled while setup is incomplete, but the dialog checks
    # again in case it was opened another way (e.g. state left over from before a data reset).
    status = get_setup_status()
    if not status.is_ready:
        st.warning(status.message, icon="👋")
        if st.button("Get started →", key="dialog_get_started"):
            _close_dialog()
            open_onboarding(1 if not status.has_profile else ONBOARDING_DOCUMENTS_STEP)
    samples = applications.sample_jobs()
    choice = st.selectbox("Or try a sample job", ["(none)", *samples])
    text = st.text_area(
        "Job posting text or link *",
        value=samples.get(choice, ""),
        height=260,
        key=f"add_job_text_{choice}",  # a new key per sample, so picking one refills the box
        max_chars=JOB_TEXT_MAX_CHARS,
        placeholder="https://jobs.example.com/ai-engineer … or paste the whole posting here",
        help=f"A link (http or https), or the posting text: at least {JOB_TEXT_MIN_CHARS} "
        "characters with the title, tasks and requirements.",
    )
    field_error(form_errors(FORM), "raw_input")
    if not busy_button(
        "Analyse fit", key="add_job_submit", type="primary", disabled=not status.is_ready
    ):
        return
    job, errors = check_input(JobInput, {"raw_input": text})
    if job is None:
        fail_form(FORM, errors)  # reruns; the text stays in the box
    clear_form_errors(FORM)

    result = run_with_progress(
        "Analysing fit",
        lambda progress: applications.add_job(text, on_progress=progress),
        steps=applications.FIT_ANALYSIS_STEPS,
        done="Fit analysis done",
    )
    if result.ok:
        _close_dialog()
        open_job(result.job_id)
    if result.job_id is not None:
        st.caption("The job is on your board; open it there to retry the analysis.")


def _on_status_change(job_id: int) -> None:
    applications.change_status(job_id, st.session_state[f"status_{job_id}"])


def _draw_columns(board: dict) -> None:
    """The five status columns with one card per job."""
    for column, status in zip(st.columns(len(STATUSES)), STATUSES, strict=True):
        cards = board[status]
        column.markdown(f"**{STATUS_LABELS[status]}** · {len(cards)}")
        for card in cards:
            with column.container(border=True):
                st.markdown(f"**{md(card.title)}**  \n{md(card.company)}")
                st.markdown(
                    f"{fit_badge(card.fit_score)} {stage_badge(card.stage, card.review_action)} "
                    f"{salary_badge(card.salary)}"
                )
                if card.next_action:
                    due = f" · {card.next_action_date:%d %b}" if card.next_action_date else ""
                    st.caption(f"Next: {md(card.next_action)}{due}")
                st.selectbox(
                    "Status",
                    STATUSES,
                    index=STATUSES.index(card.status),
                    format_func=STATUS_LABELS.get,
                    key=f"status_{card.id}",
                    label_visibility="collapsed",
                    on_change=_on_status_change,
                    args=(card.id,),
                )
                if st.button("Open", key=f"open_{card.id}", use_container_width=True):
                    open_job(card.id)


def _use_demo_persona() -> None:
    with progress_status("Loading the demo persona", done="Demo persona loaded") as box:
        box("Saving Alex Novak's profile and indexing the CV, project READMEs and brag doc…")
        report = onboarding.use_demo_persona()
        if report.errors:
            box.fail(" ".join(f"{md(name)}: {md(msg)}" for name, msg in report.errors.items()))
    if not report.errors:
        st.rerun()


st.title("🗂️ Board")

setup = get_setup_status()
if not setup.is_ready:
    st.session_state[SETUP_PENDING] = True
    action = setup_checklist(setup)
    if action == "profile":
        open_onboarding(1)
    elif action == "documents":
        open_onboarding(ONBOARDING_DOCUMENTS_STEP)
    elif action == "demo":
        _use_demo_persona()
elif st.session_state.pop(SETUP_PENDING, False):
    st.success("Setup complete! Add your first job below.", icon="✅")

# Only one primary button on screen: while setup is incomplete it's the checklist's next step.
if st.button(
    "➕ Add job",
    type="primary" if setup.is_ready else "secondary",
    disabled=not setup.is_ready,
    help=None if setup.is_ready else ADD_JOB_LOCKED_HELP,
):
    st.session_state[DIALOG_OPEN] = True
if st.session_state.get(DIALOG_OPEN):
    add_job_dialog()

board = applications.list_board()
if any(board.values()):
    _draw_columns(board)
elif setup.is_ready:
    # Empty columns (Saved · 0, Applied · 0, …) are only noise; they appear with the first job.
    st.info(
        "No jobs yet. Click **➕ Add job** and paste a job posting, or pick one of the 3 "
        "sample jobs in the dialog.",
        icon="🗂️",
    )
