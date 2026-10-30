# What: the Board page: all jobs as cards in five columns (Saved → Applied → Interview →
#       Offer / Rejected) and the "➕ Add job" dialog that starts the fit analysis.
# Why: the tracker is the user's home base (feature M7) and the entry point for new jobs (M3).
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py.

import streamlit as st

from app.db.models import JobStatus
from app.services import applications, onboarding
from app.ui.components import STATUS_LABELS, fit_badge, salary_badge, stage_badge
from app.ui.state import ONBOARDING_PAGE, open_job

STATUSES = [s.value for s in JobStatus]
DIALOG_OPEN = "add_job_dialog_open"


def _close_dialog() -> None:
    st.session_state[DIALOG_OPEN] = False


# The dialog's open/closed state lives in session_state, so it stays open across reruns
# (e.g. while typing) and closes only when the user dismisses it or the analysis succeeds.
@st.dialog("➕ Add a job", width="large", on_dismiss=_close_dialog)
def add_job_dialog() -> None:
    st.caption(
        "Paste the full job posting, or a link to it. Links from sites that need a login "
        "(like LinkedIn) usually can't be read, so pasting the text is the safest option."
    )
    samples = applications.sample_jobs()
    choice = st.selectbox("Or try a sample job", ["(none)", *samples])
    text = st.text_area(
        "Job posting text or link",
        value=samples.get(choice, ""),
        height=260,
        key=f"add_job_text_{choice}",  # a new key per sample, so picking one refills the box
        placeholder="https://… or paste the posting here",
    )
    if not st.button("Analyse fit", type="primary", disabled=not text.strip()):
        return

    with st.status("Analysing fit…", expanded=True) as status:
        result = applications.add_job(text, on_progress=status.write)
        if result.ok:
            status.update(label="Fit analysis ready ✔", state="complete")
        else:
            status.update(label="Couldn't finish the analysis", state="error")

    if result.ok:
        _close_dialog()
        open_job(result.job_id)
    st.error(result.message)
    if result.job_id is not None:
        st.caption("The job is on your board; open it there to retry the analysis.")


def _on_status_change(job_id: int) -> None:
    applications.change_status(job_id, st.session_state[f"status_{job_id}"])


st.title("🗂️ Board")

if not onboarding.is_onboarded():
    st.info("Set up your profile first, so I can compare jobs with your experience.", icon="👋")
    if st.button("Get started →", type="primary"):
        st.switch_page(ONBOARDING_PAGE)

_, docs = onboarding.knowledge_base_summary()
if docs == 0:
    st.warning(
        "Your knowledge base is empty, so fit analysis can't find evidence yet. Upload your "
        "CV or use the demo persona on the **Get started** page.",
        icon="📭",
    )

if st.button("➕ Add job", type="primary"):
    st.session_state[DIALOG_OPEN] = True
if st.session_state.get(DIALOG_OPEN):
    add_job_dialog()

board = applications.list_board()
if not any(board.values()):
    st.info(
        "No jobs yet. Click **➕ Add job** and paste a job posting, or pick one of the 3 "
        "sample jobs in the dialog.",
        icon="🗂️",
    )

for column, status in zip(st.columns(len(STATUSES)), STATUSES, strict=True):
    cards = board[status]
    column.markdown(f"**{STATUS_LABELS[status]}** · {len(cards)}")
    for card in cards:
        with column.container(border=True):
            st.markdown(f"**{card.title}**  \n{card.company}")
            st.markdown(
                f"{fit_badge(card.fit_score)} {stage_badge(card.stage, card.review_action)} "
                f"{salary_badge(card.salary)}"
            )
            if card.next_action:
                due = f" · {card.next_action_date:%d %b}" if card.next_action_date else ""
                st.caption(f"Next: {card.next_action}{due}")
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
