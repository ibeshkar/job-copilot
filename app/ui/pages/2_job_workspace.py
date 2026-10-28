# What: the Job workspace: one job's details in tabs. The Fit, Claim check and Documents tabs
#       render whatever the paused workflow is waiting for (fit review, claim check, draft
#       review) and send the user's decision back. Notes keeps notes, next action and versions.
# Why: this is the interrupt-rendering pattern from PROJECT_PLAN.md §7. On every rerun the page
#      reads the saved graph state, so refreshing the browser or restarting the app shows the
#      same step again.
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py.

import re

import streamlit as st

from app.agent.state import Drafts
from app.db.models import JobStatus
from app.services import applications
from app.services.applications import RunResult, WorkflowView
from app.services.export import drafts_to_text
from app.ui.components import (
    STATUS_LABELS,
    claim_badge,
    claim_evidence,
    coming_soon,
    fit_card,
    guard_issues_box,
    reviewed_claims,
)
from app.ui.state import BOARD_PAGE, current_job_id, select_job

STATUSES = [s.value for s in JobStatus]
TABS = ["🎯 Fit", "✅ Claim check", "📝 Documents", "🏢 Company", "🎤 Interview prep", "🗒️ Notes"]
# Open the tab that needs the user's attention, e.g. "Claim check" while claims wait for review.
TAB_FOR_STAGE = {"claim_check": TABS[1], "draft_review": TABS[2]}


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
            "You decided to apply. Next: confirm the claims in the **✅ Claim check** tab, "
            "then review your drafts in **📝 Documents**.",
            icon="▶",
        )


# ---------------------------------------------------------------- claim check tab


def _claim_review_form(view: WorkflowView) -> None:
    st.markdown(
        "These are the things your application would say about you. **Untick** anything you "
        "don't want to say, and **edit** the wording if it's not quite right. Only the claims "
        "you keep are used, and the writer never sees the rest."
    )
    if any(not c.supported for c in view.claims):
        st.warning(
            "Claims marked **no evidence** start unticked. Tick one only if it's true; I'll "
            "remember it as a fact you confirmed.",
            icon="⚠️",
        )
    with st.form(f"claims_{view.job_id}"):
        for claim in view.claims:
            with st.container(border=True):
                keep_col, text_col = st.columns([1, 9])
                keep_col.checkbox(
                    "Keep", value=claim.supported, key=f"keep_{view.job_id}_{claim.id}"
                )
                text_col.text_area(
                    f"Claim {claim.id}",
                    value=claim.text,
                    key=f"text_{view.job_id}_{claim.id}",
                    height=68,
                    label_visibility="collapsed",
                )
                text_col.markdown(claim_badge(claim))
                with text_col.expander("Evidence"):
                    claim_evidence(claim)
        submitted = st.form_submit_button(
            "Confirm claims and write drafts ▶", type="primary", use_container_width=True
        )
    if not submitted:
        return

    decisions = []
    for claim in view.claims:
        if not st.session_state[f"keep_{view.job_id}_{claim.id}"]:
            decisions.append({"id": claim.id, "action": "remove"})
            continue
        text = st.session_state[f"text_{view.job_id}_{claim.id}"].strip()
        action = "approve" if text == claim.text else "edit"
        decisions.append({"id": claim.id, "action": action, "text": text})
    _after(
        _run_with_status(
            "Writing your drafts…",
            lambda p: applications.submit_claim_review(view.job_id, decisions, on_progress=p),
        )
    )


def _claims_tab(view: WorkflowView) -> None:
    if view.stage == "claim_check":
        if not view.claims:
            st.info("I couldn't find any claims to make. Add context on the Fit tab first.")
        _claim_review_form(view)
    elif view.claims and any(c.user_decision != "pending" for c in view.claims):
        st.caption("Your decisions at the claim check:")
        reviewed_claims(view.claims, view.new_facts)
    else:
        st.info(
            "Claims appear here after you choose **Continue to application** on the Fit tab.",
            icon="✅",
        )


# ---------------------------------------------------------------- documents tab


def _drafts_from_inputs(prefix: str, used_claim_ids: list[str]) -> Drafts:
    """Read the edited drafts back from the text areas (one bullet per line)."""
    bullet_lines = st.session_state[f"bullets_{prefix}"].splitlines()
    return Drafts(
        cv_bullets=[b for b in (re.sub(r"^\s*[-•*]\s*", "", x).strip() for x in bullet_lines) if b],
        cover_letter=st.session_state[f"letter_{prefix}"].strip(),
        used_claim_ids=used_claim_ids,
    )


def _draft_editors(drafts: Drafts, prefix: str) -> None:
    st.text_area(
        "CV bullets (one per line)",
        value="\n".join(f"- {b}" for b in drafts.cv_bullets),
        height=180,
        key=f"bullets_{prefix}",
    )
    st.text_area("Cover letter", value=drafts.cover_letter, height=380, key=f"letter_{prefix}")


def _rate(job_id: int, target: str, key: str) -> None:
    value = st.session_state.get(key)
    if value is not None:
        applications.rate_drafts(job_id, target, 1 if value == 1 else -1)
        st.toast("Thanks, I saved your rating.")


def _export_copy_rate(job_id: int, drafts: Drafts, prefix: str) -> None:
    card = applications.get_job_summary(job_id)
    file = applications.export_drafts(job_id, drafts)
    export_col, rate_letter, rate_bullets = st.columns([2, 1, 1])
    export_col.download_button(
        "⬇️ Export DOCX" if file.file_name.endswith(".docx") else "⬇️ Export .txt",
        data=file.data,
        file_name=file.file_name,
        mime=file.mime,
        on_click="ignore",
    )
    for col, target in ((rate_letter, "letter"), (rate_bullets, "bullets")):
        col.caption(f"Rate the {target}")
        key = f"rate_{target}_{prefix}"
        col.feedback("thumbs", key=key, on_change=_rate, args=(job_id, target, key))
    with st.expander("📋 Copy as text"):
        st.caption("Use the copy icon in the top-right corner of the box.")
        st.code(drafts_to_text(drafts, card.title, card.company), language=None, wrap_lines=True)


def _draft_review(view: WorkflowView) -> None:
    versions = applications.list_draft_versions(view.job_id)
    prefix = f"{view.job_id}_{versions[0].version if versions else 0}"
    guard_issues_box(view.guard_issues)
    _draft_editors(view.drafts, prefix)
    edited = _drafts_from_inputs(prefix, view.drafts.used_claim_ids)

    if st.button("✅ Accept & save", type="primary", use_container_width=True):
        _after(
            _run_with_status(
                "Saving your drafts…",
                lambda p: applications.submit_draft_review(
                    view.job_id, "accept", edited=edited, on_progress=p
                ),
            )
        )
    with st.expander("🔄 Regenerate a section"):
        section = st.radio(
            "Section",
            ["cover_letter", "cv_bullets"],
            format_func={"cover_letter": "Cover letter", "cv_bullets": "CV bullets"}.get,
            horizontal=True,
            key=f"regen_section_{prefix}",
        )
        instruction = st.text_input(
            "What should change? (optional)",
            placeholder="e.g. shorter, more about the RAG project",
            key=f"regen_instruction_{prefix}",
            max_chars=500,
        )
        st.caption("Your edits to the other section are kept.")
        if st.button("Regenerate"):
            _after(
                _run_with_status(
                    "Rewriting…",
                    lambda p: applications.submit_draft_review(
                        view.job_id,
                        "regenerate",
                        edited=edited,
                        section=section,
                        instruction=instruction,
                        on_progress=p,
                    ),
                )
            )
    _export_copy_rate(view.job_id, edited, prefix)


def _accepted_drafts(view: WorkflowView) -> None:
    versions = applications.list_draft_versions(view.job_id)
    latest = versions[0]
    prefix = f"{view.job_id}_{latest.version}"
    st.success("You accepted these drafts. You can still edit them; each save is a new version.")
    _draft_editors(latest.drafts, prefix)
    edited = _drafts_from_inputs(prefix, [])
    if st.button("💾 Save changes"):
        saved = applications.save_edited_drafts(view.job_id, edited)
        st.toast("Saved as a new version." if saved else "Nothing changed.")
        if saved:
            st.rerun()
    _export_copy_rate(view.job_id, edited, prefix)


def _documents_tab(view: WorkflowView) -> None:
    if view.stage == "draft_review" and view.drafts is not None:
        _draft_review(view)
    elif view.stage == "finished" and applications.list_draft_versions(view.job_id):
        _accepted_drafts(view)
    else:
        st.info(
            "Your tailored CV bullets and cover letter appear here after the claim check.",
            icon="📝",
        )


# ---------------------------------------------------------------- notes tab


def _notes_tab(job_id: int) -> None:
    card = applications.get_job_summary(job_id)
    with st.form(f"notes_{job_id}"):
        notes = st.text_area("Notes", value=card.notes, height=140)
        action_col, date_col = st.columns([2, 1])
        next_action = action_col.text_input(
            "Next action",
            value=card.next_action or "",
            placeholder="e.g. Follow up with the recruiter",
        )
        due = date_col.date_input(
            "Due", value=card.next_action_date.date() if card.next_action_date else None
        )
        if st.form_submit_button("Save notes"):
            applications.save_notes(job_id, notes, next_action, due)
            st.toast("Notes saved.")

    versions = applications.list_draft_versions(job_id)
    st.markdown("##### Draft versions")
    if not versions:
        st.caption("No drafts yet.")
    for v in versions:
        who = "your edit" if v.is_user_edited else "AI draft"
        with st.expander(f"Version {v.version} · {who} · {v.created_at:%d %b %Y %H:%M}"):
            st.markdown("\n".join(f"- {b}" for b in v.drafts.cv_bullets))
            st.text(v.drafts.cover_letter)


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
if view.stage in TAB_FOR_STAGE:
    st.info(f"Waiting for you in **{TAB_FOR_STAGE[view.stage]}**.", icon="👉")
fit_tab, claims_tab, docs_tab, company_tab, interview_tab, notes_tab = st.tabs(
    TABS, default=TAB_FOR_STAGE.get(view.stage)
)
with fit_tab:
    _fit_tab(view)
with claims_tab:
    _claims_tab(view)
with docs_tab:
    _documents_tab(view)
with company_tab:
    coming_soon(6, "Company research with sources.")
with interview_tab:
    coming_soon(8, "A mock interview built from this job's requirements and your gaps.")
with notes_tab:
    _notes_tab(job_id)
