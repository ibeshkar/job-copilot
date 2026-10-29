# What: the Job workspace: one job's details in tabs. The Fit, Claim check and Documents tabs
#       render whatever the paused workflow is waiting for (fit review, claim check, draft
#       review) and send the user's decision back; Documents also shows the style rules learned
#       from the user's edits and 👎 comments (with Undo). Company shows web research about the
#       employer. Interview prep runs the mock interview. Notes keeps notes, next action and
#       versions.
# Why: this is the interrupt-rendering pattern from PROJECT_PLAN.md §7. On every rerun the page
#      reads the saved graph state, so refreshing the browser or restarting the app shows the
#      same step again.
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py,
#          company.py, interview.py and learning.py.

import re

import streamlit as st

from app.agent.state import Drafts, InterviewTurn
from app.config import get_settings
from app.db.models import JobStatus
from app.guards.input_guard import MAX_ANSWER_CHARS
from app.services import applications, company, interview, learning
from app.services.applications import RunResult, WorkflowView
from app.services.export import drafts_to_text
from app.services.interview import InterviewView
from app.services.learning import LearnedRule
from app.ui.components import (
    STATUS_LABELS,
    claim_badge,
    claim_evidence,
    fit_card,
    guard_issues_box,
    reviewed_claims,
)
from app.ui.state import BOARD_PAGE, current_job_id, select_job

STATUSES = [s.value for s in JobStatus]
TABS = ["🎯 Fit", "✅ Claim check", "📝 Documents", "🏢 Company", "🎤 Interview prep", "🗒️ Notes"]
# Open the tab that needs the user's attention, e.g. "Claim check" while claims wait for review.
TAB_FOR_STAGE = {"claim_check": TABS[1], "draft_review": TABS[2]}
TARGET_NAMES = {"letter": "cover letter", "bullets": "CV bullets"}
KIND_LABELS = {"behavioural": "Behavioural", "technical": "Technical", "gap": "About a gap"}
STAR = ["situation", "task", "action", "result"]
TOASTED_KEY = "learned_rules_toasted"  # rule keys already announced with a toast


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
            "The last step didn't finish, maybe because the AI service was busy or the internet "
            "connection dropped. "
            "Your progress is saved; retrying continues from the last finished step.",
            icon="⏳",
        )
        if st.button("🔁 Retry", type="primary"):
            _after(
                _run_with_status(
                    "Continuing…",
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


def _session_learned_key(job_id: int) -> str:
    return f"learned_{job_id}"


def _remember_learned(job_id: int, rules: list[LearnedRule]) -> None:
    """Keep rules learned outside the graph (Save changes, 👎 comments) for the Undo box."""
    saved = st.session_state.setdefault(_session_learned_key(job_id), [])
    saved += [{"key": r.key, "rule": r.rule} for r in rules]


def _learned_rules_box(view: WorkflowView) -> None:
    """Announce newly learned style rules (toast) and offer ↩️ Undo for each (§6.5)."""
    learned = view.learned_rules + st.session_state.get(_session_learned_key(view.job_id), [])
    rules = learning.still_saved(learned)
    if not rules:
        return
    toasted = st.session_state.setdefault(TOASTED_KEY, set())
    for rule in rules:
        if rule.key not in toasted:
            st.toast(f"🧠 I learned: *{rule.rule}*")
            toasted.add(rule.key)
    with st.container(border=True):
        st.markdown("🧠 **I learned from your feedback.** New letters will follow:")
        for rule in rules:
            text_col, undo_col = st.columns([6, 1], vertical_alignment="center")
            text_col.markdown(f"- {rule.rule}")
            if undo_col.button("↩️ Undo", key=f"undo_rule_{rule.key}"):
                learning.undo(rule.key)
                st.toast("Okay, I forgot that rule.")
                st.rerun()


def _why_key(key: str) -> str:
    return f"why_{key}"


def _rate(job_id: int, target: str, key: str) -> None:
    """👍 is saved at once; 👎 first asks what was wrong (the comment can become a rule)."""
    value = st.session_state.get(key)
    if value == 1:
        applications.rate_drafts(job_id, target, 1)
        st.session_state[_why_key(key)] = False
        st.toast("Thanks, I saved your rating.")
    elif value == 0:
        st.session_state[_why_key(key)] = True


def _thumbs_down_form(job_id: int, target: str, key: str) -> None:
    with st.form(f"why_form_{key}"):
        comment = st.text_input(
            f"👎 What should be different about the {TARGET_NAMES[target]}? (optional)",
            placeholder="e.g. too long, too formal, don't start with 'I am writing to…'",
            max_chars=300,
        )
        if not st.form_submit_button("Send rating"):
            return
    applications.rate_drafts(job_id, target, -1, comment.strip())
    st.session_state[_why_key(key)] = False
    learned = learning.learn_from_ratings(target) if comment.strip() else []
    _remember_learned(job_id, learned)
    st.toast("Thanks, I saved your rating.")
    st.rerun()


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
    for target in ("letter", "bullets"):
        key = f"rate_{target}_{prefix}"
        if st.session_state.get(_why_key(key)):
            _thumbs_down_form(job_id, target, key)
    with st.expander("📋 Copy as text"):
        st.caption("Use the copy icon in the top-right corner of the box.")
        st.code(drafts_to_text(drafts, card.title, card.company), language=None, wrap_lines=True)


def _draft_review(view: WorkflowView) -> None:
    versions = applications.list_draft_versions(view.job_id)
    prefix = f"{view.job_id}_{versions[0].version if versions else 0}"
    _learned_rules_box(view)
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
    _learned_rules_box(view)
    _draft_editors(latest.drafts, prefix)
    edited = _drafts_from_inputs(prefix, [])
    if st.button("💾 Save changes"):
        saved = applications.save_edited_drafts(view.job_id, edited)
        st.toast("Saved as a new version." if saved else "Nothing changed.")
        if saved:
            with st.spinner("Learning from your edits…"):
                _remember_learned(
                    view.job_id, learning.learn_from_edit(view.job_id, latest.drafts, edited)
                )
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


# ---------------------------------------------------------------- company tab


def _bullets(title: str, items: list[str]) -> None:
    if items:
        st.markdown(f"**{title}**")
        st.markdown("\n".join(f"- {item}" for item in items))


def _company_tab(job_id: int) -> None:
    card = applications.get_job_summary(job_id)
    settings = get_settings()
    research = company.saved_research(job_id)
    if not settings.has_tavily_key:
        st.info(
            "Company research uses Tavily web search. Get a free key at tavily.com, set "
            "`TAVILY_API_KEY` in `.env` and restart the app.",
            icon="🔑",
        )
    focus_col, button_col = st.columns([3, 1])
    focus = focus_col.text_input(
        "Focus (optional)",
        placeholder="e.g. tech stack, recent funding, engineering culture",
        key=f"company_focus_{job_id}",
        max_chars=200,
    )
    label = "🔄 Research again" if research else f"🔎 Research {card.company}"
    button_col.markdown("&nbsp;")  # lines the button up with the text box
    if button_col.button(
        label,
        disabled=not (settings.has_tavily_key and settings.has_llm_key),
        use_container_width=True,
    ):
        with st.spinner(f"Searching the web for {card.company}…"):
            fresh, message = company.run_research(job_id, focus)
        if fresh is None:
            st.error(message)
        else:
            st.rerun()

    if research is None:
        st.caption("No research yet. It's saved for this job, so you only pay for it once.")
        return
    st.markdown(f"#### {research.company}")
    st.write(research.brief.summary)
    left, right = st.columns(2)
    with left:
        _bullets("Products", research.brief.products)
        _bullets("Tech stack", research.brief.tech_stack)
    with right:
        _bullets("Culture", research.brief.culture)
        _bullets("Recent news", research.brief.recent_news)
    if research.brief.talking_points:
        st.success(
            "**Things you could mention**\n"
            + "\n".join(f"- {point}" for point in research.brief.talking_points),
            icon="💡",
        )
    with st.expander(f"🔗 Sources ({len(research.sources)})"):
        for source in research.sources:
            st.markdown(f"- [{source.title or source.url}]({source.url})")
    st.caption(
        f"Researched {research.researched_at:%d %b %Y %H:%M} UTC from web search results. "
        "Web content can be wrong or out of date; check the sources."
    )


# ---------------------------------------------------------------- interview prep tab


def _answer_feedback(turn: InterviewTurn) -> None:
    fb = turn.feedback
    star = " · ".join(("✅ " if p in fb.star_parts else "▫️ ") + p.capitalize() for p in STAR)
    st.markdown(f"**Score: {fb.score}/5** &nbsp; {star}")
    st.markdown(f"- **Relevance:** {fb.relevance}\n- **Specificity:** {fb.specificity}")
    st.info(f"**Try this:** {fb.improvement}", icon="💡")
    with st.expander("✨ Improved example answer"):
        st.write(fb.improved_answer)
        sources = sorted({e.doc_name for e in turn.evidence if e.chunk_id in fb.evidence_ids_used})
        st.caption(
            "Built from your answer and: " + ", ".join(sources)
            if sources
            else "Built from your answer only."
        )


def _interview_transcript(view: InterviewView) -> None:
    for n, turn in enumerate(view.turns, start=1):
        with st.chat_message("assistant", avatar="🎤"):
            st.markdown(f"**Q{n} · {KIND_LABELS[turn.question.kind]}**  \n{turn.question.question}")
        st.chat_message("user").write(turn.answer)
        if turn.feedback is not None:
            with st.chat_message("assistant", avatar="🧑‍🏫"):
                _answer_feedback(turn)


def _interview_question(view: InterviewView) -> None:
    question = view.current
    with st.chat_message("assistant", avatar="🎤"):
        st.markdown(
            f"**Q{view.number} of {view.total} · {KIND_LABELS[question.kind]}**  \n"
            f"{question.question}"
        )
        st.caption(f"Why they ask: {question.why}")
    with st.form(f"answer_{view.job_id}_{view.number}"):
        text = st.text_area(
            "Your answer",
            height=180,
            max_chars=MAX_ANSWER_CHARS,
            placeholder="Answer as you would in the interview. Tip: Situation → Task → Action "
            "→ Result, with one number if you have it.",
        )
        send_col, stop_col = st.columns(2)
        send = send_col.form_submit_button(
            "Send answer ▶", type="primary", use_container_width=True
        )
        stop = stop_col.form_submit_button("End interview now", use_container_width=True)
    if send:
        _after(
            _run_with_status(
                "Scoring your answer…",
                lambda p: interview.answer(view.job_id, text, on_progress=p),
            )
        )
    if stop:
        _after(
            _run_with_status(
                "Summarising…", lambda p: interview.end_early(view.job_id, on_progress=p)
            )
        )


def _rate_interview(job_id: int, key: str) -> None:
    value = st.session_state.get(key)
    if value is not None:  # None = the user took their thumb back
        interview.rate(job_id, 1 if value == 1 else -1)
        st.toast("Thanks, I saved your rating.")


def _interview_summary(view: InterviewView) -> None:
    if view.summary is None:
        st.info("You ended the interview before answering a question.", icon="🎤")
        return
    score_col, rate_col = st.columns([3, 1])
    score_col.metric("Average score", f"{view.overall_score}/5")
    rate_col.caption("Was the feedback useful?")
    key = f"rate_interview_{view.job_id}_{len(interview.past_interviews(view.job_id))}"
    rate_col.feedback("thumbs", key=key, on_change=_rate_interview, args=(view.job_id, key))
    strong, weak, steps = st.columns(3)
    for col, title, items in (
        (strong, "💪 Strongest", view.summary.strongest_areas),
        (weak, "🎯 Practise most", view.summary.weakest_areas),
        (steps, "📋 Next steps", view.summary.next_steps),
    ):
        col.markdown(f"**{title}**")
        col.markdown("\n".join(f"- {item}" for item in items))


def _past_interviews(job_id: int) -> None:
    past = interview.past_interviews(job_id)
    if past:
        st.markdown("##### Past interviews")
        for p in past:
            score = f"{p.overall_score}/5" if p.overall_score is not None else "no score"
            st.caption(f"{p.created_at:%d %b %Y %H:%M} · {p.answered} answers · {score}")


def _start_interview(job_id: int, label: str, primary: bool = True) -> None:
    if st.button(label, type="primary" if primary else "secondary", key=f"start_iv_{job_id}"):
        _after(
            _run_with_status(
                "Preparing your first question…",
                lambda p: interview.start(job_id, on_progress=p),
            )
        )


def _interview_tab(job_id: int) -> None:
    view = interview.get_view(job_id)
    if view.stage == "unavailable":
        st.info(interview.NEEDS_ANALYSIS, icon="🎤")
        return
    if view.stage == "not_started":
        st.markdown(
            f"Practise with **{view.total} questions** built from this job: behavioural, "
            "technical, and one about a gap from your fit analysis. After each answer you get a "
            "score from 1 to 5, a STAR check, one improvement and a better example answer made "
            "from your own documents."
        )
        _start_interview(job_id, "🎤 Start mock interview")
        _past_interviews(job_id)
        return

    if view.stage == "interrupted":
        st.warning(
            "The last step didn't finish (maybe the AI service was busy or the internet dropped). "
            "Your answers are saved.",
            icon="⏳",
        )
        if st.button("🔁 Retry", key=f"retry_iv_{job_id}"):
            _after(
                _run_with_status("Continuing…", lambda p: interview.retry(job_id, on_progress=p))
            )

    if view.stage == "finished":
        _interview_summary(view)
        _start_interview(job_id, "🔁 Start again", primary=False)
        with st.expander(f"Your answers and feedback ({len(view.turns)})"):
            _interview_transcript(view)
        _past_interviews(job_id)
        return

    answered = len(view.turns)
    st.progress(answered / view.total, text=f"{answered} of {view.total} answered")
    _interview_transcript(view)
    if view.stage == "question" and view.current is not None:
        _interview_question(view)


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
    _company_tab(job_id)
with interview_tab:
    _interview_tab(job_id)
with notes_tab:
    _notes_tab(job_id)
