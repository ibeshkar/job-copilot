# What: the Job workspace: one job's details in tabs. The Fit, Claim check and Documents tabs
#       render whatever the paused workflow is waiting for (fit review, claim check, draft
#       review) and send the user's decision back; Documents also shows the style rules learned
#       from the user's edits and 👎 comments (with Undo). Company shows web research about the
#       employer. Salary shows a market salary estimate. Interview prep runs the mock interview.
#       Notes keeps notes, next action and versions.
# Why: this is the interrupt-rendering pattern from PROJECT_PLAN.md §7. On every rerun the page
#      reads the saved graph state, so refreshing the browser or restarting the app shows the
#      same step again.
# Used by: app/main.py (st.navigation). Work is done by app/services/applications.py,
#          company.py, salary.py, interview.py and learning.py.

import re

import streamlit as st

from app import config as C
from app.agent.state import UNKNOWN_COMPANY, CoverLetter, Drafts, InterviewTurn
from app.config import get_settings
from app.db.models import JobStatus
from app.guards.input_guard import check_interview_answer
from app.services import applications, company, interview, learning, salary
from app.services.applications import RunResult, WorkflowView
from app.services.export import LAYOUT, ExportFile, LetterBlock, drafts_to_text
from app.services.interview import InterviewView
from app.services.learning import LearnedRule
from app.tools.salary import DISCLAIMER
from app.ui.components import (
    CONFIDENCE_BADGES,
    STATUS_LABELS,
    action_row,
    busy_button,
    claim_keys,
    claim_list,
    claims_summary,
    clear_claim_state,
    clear_form_errors,
    error_summary,
    fail_form,
    field_error,
    fit_card,
    form_errors,
    guard_issues_box,
    init_claim_state,
    input_with_action,
    kept_claim_text,
    news_line,
    progress_status,
    reviewed_claims,
    run_with_progress,
    set_claims_kept,
)
from app.ui.state import BOARD_PAGE, current_job_id, select_job
from app.utils.errors import InvalidInputError
from app.validation.models import (
    CompanyFocusInput,
    ContextInput,
    CoverLetterInput,
    NoteInput,
    check_input,
)
from app.validation.sanitize import html_text, md

STATUSES = [s.value for s in JobStatus]
TABS = [
    "🎯 Fit",
    "✅ Claim check",
    "📝 Documents",
    "🏢 Company",
    "💰 Salary",
    "🎤 Interview prep",
    "🗒️ Notes",
]
POSITION_TEXT = {
    "below": "Your expectation is **below** this range: you may be able to ask for more.",
    "within": "Your expectation is **within** this range.",
    "above": "Your expectation is **above** this range: be ready to justify it.",
}
# Open the tab that needs the user's attention, e.g. "Claim check" while claims wait for review.
TAB_FOR_STAGE = {"claim_check": TABS[1], "draft_review": TABS[2]}
TARGET_NAMES = {"letter": "cover letter", "bullets": "CV bullets"}
KIND_LABELS = {"behavioural": "Behavioural", "technical": "Technical", "gap": "About a gap"}
STAR = ["situation", "task", "action", "result"]
TOASTED_KEY = "learned_rules_toasted"  # rule keys already announced with a toast
# The recipient block, as (CoverLetter field, label) for the editor. A contact person only
# appears in the salutation, which has its own input.
RECIPIENT_FIELDS = [
    ("recipient_company", "Company"),
    ("recipient_department", "Department (e.g. Recruiting – Data team)"),
    ("recipient_city_country", "City, country"),
]
PX_PER_PT = 1.2  # the preview's "paper" is a bit smaller than the printed page


def _after(result: RunResult) -> None:
    """Show the new state after a successful run (a failed run's box shows the error)."""
    if result.ok:
        st.rerun()


def _fit_review_actions(job_id: int) -> None:
    st.divider()
    st.markdown("##### What do you want to do?")
    col_continue, col_stop = st.columns(2)
    with col_continue:
        go_on = busy_button(
            "Continue to application ▶",
            key=f"fit_continue_{job_id}",
            type="primary",
            use_container_width=True,
        )
    with col_stop:
        stop = busy_button("🚫 Not a fit", key=f"fit_stop_{job_id}", use_container_width=True)
    if go_on:
        _after(
            run_with_progress(
                "Planning claims about you",
                lambda p: applications.submit_fit_review(job_id, "continue", on_progress=p),
                done="Claims ready to check",
            )
        )
    if stop:
        _after(
            run_with_progress(
                "Saving your decision",
                lambda p: applications.submit_fit_review(job_id, "stop", on_progress=p),
                done="Saved as not a fit",
            )
        )

    with st.expander("➕ Add context and re-check"):
        st.caption(
            "Tell me about experience that isn't in your documents, for example "
            '"I used Airflow at my last job to schedule daily reports". '
            "I'll search again and use your note as evidence."
        )
        scope = f"context_{job_id}"
        note = st.text_area(
            "Your context *",
            key=f"context_{job_id}",
            max_chars=C.CONTEXT_MAX_CHARS,
            placeholder="I used Airflow at my last job to schedule daily reports.",
        )
        field_error(form_errors(scope), "text")
        if busy_button("Re-check with this context", key=f"recheck_{job_id}"):
            clean, errors = check_input(ContextInput, {"text": note})
            if clean is None:
                fail_form(scope, errors)  # reruns; the note stays in the box
            clear_form_errors(scope)
            _after(
                run_with_progress(
                    "Re-checking your fit",
                    lambda p: applications.submit_fit_review(
                        job_id, "add_context", text=note, on_progress=p
                    ),
                    steps=applications.RECHECK_STEPS,
                    done="Fit re-checked",
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
        if busy_button("🔁 Retry", key=f"retry_fit_{view.job_id}", type="primary"):
            _after(
                run_with_progress(
                    "Continuing the analysis",
                    lambda p: applications.retry_analysis(view.job_id, on_progress=p),
                    done="Analysis continued",
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

    fit_card(
        view.job,
        view.requirements,
        view.fit,
        view.warnings,
        view.extra_context,
        key=f"fit_{view.job_id}",
    )

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


def _claim_review(view: WorkflowView) -> None:
    """Header with bulk actions, the flat claim list, and a footer with the one primary action.

    No st.form: the ✏️ / Save / Cancel buttons must rerun the page, which a form doesn't allow.
    The choices live in st.session_state until the user submits (see init_claim_state).
    """
    scope = str(view.job_id)
    claims = view.claims
    init_claim_state(claims, scope)
    backed = [c for c in claims if c.supported]
    unconfirmed = [c for c in claims if not c.supported]

    st.markdown(
        "These are the things your application would say about you. **Untick** anything you "
        "don't want to say, and **edit** the wording if it's not quite right. Only the claims "
        "you keep are used, and the writer never sees the rest."
    )
    st.markdown(claims_summary(claims))
    with st.container(horizontal=True, gap="small"):
        st.button(
            "Keep all backed",
            key=f"claims_{scope}_keep_backed",
            disabled=not backed,
            on_click=set_claims_kept,
            args=(backed, scope, True),
        )
        st.button(
            "Untick unconfirmed",
            key=f"claims_{scope}_untick_unconfirmed",
            disabled=not unconfirmed,
            on_click=set_claims_kept,
            args=(unconfirmed, scope, False),
        )

    claim_list(claims, scope)

    kept = {c.id: kept_claim_text(c, scope) for c in claims}
    selected = sum(text is not None for text in kept.values())
    st.divider()
    count_col, button_col = st.columns([3, 2], vertical_alignment="center")
    count_col.markdown(f"**{selected} of {len(claims)} claims selected**")
    if any(st.session_state[claim_keys(scope, c.id).editing] for c in claims):
        count_col.caption("✏️ Save or cancel your open edit; unsaved wording isn't used.")
    if not selected:
        count_col.caption("Tick at least one claim: otherwise there's nothing true to write about.")
    with button_col:
        write = busy_button(
            "✍️ Write my application →",
            key=f"write_application_{scope}",
            type="primary",
            disabled=not selected,
            use_container_width=True,
        )
    if not write:
        return

    # Same payload as before the redesign: remove, approve (unchanged) or edit (new wording).
    decisions = []
    for claim in claims:
        text = kept[claim.id]
        if text is None:
            decisions.append({"id": claim.id, "action": "remove"})
            continue
        action = "approve" if text == claim.text else "edit"
        decisions.append({"id": claim.id, "action": action, "text": text})
    result = run_with_progress(
        "Writing your drafts",
        lambda p: applications.submit_claim_review(view.job_id, decisions, on_progress=p),
        done="Drafts ready",
    )
    if result.ok:
        clear_claim_state(claims, scope)
    _after(result)


def _claims_tab(view: WorkflowView) -> None:
    if view.stage == "claim_check":
        if not view.claims:
            st.info("I couldn't find any claims to make. Add context on the Fit tab first.")
            return
        _claim_review(view)
    elif view.claims and any(c.user_decision != "pending" for c in view.claims):
        st.caption("Your decisions at the claim check:")
        reviewed_claims(view.claims, view.new_facts)
    else:
        st.info(
            "Claims appear here after you choose **Continue to application** on the Fit tab.",
            icon="✅",
        )


# ---------------------------------------------------------------- documents tab


def _letter_key(prefix: str, field: str) -> str:
    return f"letter_{field}_{prefix}"


def _letter_from_inputs(prefix: str, letter: CoverLetter) -> CoverLetter:
    """Read the edited letter back from its inputs. An emptied paragraph is dropped."""
    state = st.session_state
    paragraphs = [state[_letter_key(prefix, f"p{i}")] for i in range(len(letter.body_paragraphs))]
    paragraphs.append(state[_letter_key(prefix, "p_new")])
    fields = {name: state[_letter_key(prefix, name)].strip() for name, _ in RECIPIENT_FIELDS}
    return letter.model_copy(
        update={
            **{name: value or None for name, value in fields.items()},
            "recipient_company": fields["recipient_company"],
            "subject": state[_letter_key(prefix, "subject")].strip(),
            "salutation": state[_letter_key(prefix, "salutation")].strip(),
            "body_paragraphs": [p.strip() for p in paragraphs if p.strip()],
            "closing": state[_letter_key(prefix, "closing")].strip(),
        }
    )


def _drafts_scope(prefix: str) -> str:
    return f"drafts_{prefix}"


def _letter_editor(letter: CoverLetter, prefix: str) -> None:
    st.caption(
        "Your name and contact details come from your profile (🧠 Memory page). Empty "
        "company, subject, salutation or closing are filled in from the job."
    )
    errors = form_errors(_drafts_scope(prefix))
    columns = st.columns(2)
    for i, (name, label) in enumerate(RECIPIENT_FIELDS):
        with columns[i % 2]:
            st.text_input(
                label,
                value=getattr(letter, name) or "",
                key=_letter_key(prefix, name),
                max_chars=C.PLACE_MAX_CHARS
                if name == "recipient_city_country"
                else C.SHORT_TEXT_MAX_CHARS,
            )
            field_error(errors, name)
    st.text_input(
        "Subject",
        value=letter.subject,
        key=_letter_key(prefix, "subject"),
        max_chars=C.SHORT_TEXT_MAX_CHARS,
    )
    field_error(errors, "subject")
    st.text_input(
        "Salutation",
        value=letter.salutation,
        key=_letter_key(prefix, "salutation"),
        max_chars=C.SHORT_TEXT_MAX_CHARS,
    )
    field_error(errors, "salutation")
    for i, paragraph in enumerate(letter.body_paragraphs):
        st.text_area(
            f"Paragraph {i + 1}",
            value=paragraph,
            height=110,
            key=_letter_key(prefix, f"p{i}"),
            max_chars=C.PARAGRAPH_MAX_CHARS,
            help="Empty a paragraph to remove it. At least one paragraph must stay.",
        )
    st.text_area(
        "Add a paragraph (optional)",
        height=80,
        key=_letter_key(prefix, "p_new"),
        max_chars=C.PARAGRAPH_MAX_CHARS,
    )
    field_error(errors, "body_paragraphs")
    st.text_input(
        "Closing",
        value=letter.closing,
        key=_letter_key(prefix, "closing"),
        max_chars=C.CLOSING_MAX_CHARS,
    )
    field_error(errors, "closing")


def _letter_preview(blocks: list[LetterBlock]) -> None:
    """A white "paper" card with the letter's lines in the PDF's order, sizes and gaps."""
    lines = []
    for block in blocks:
        size = LAYOUT.size(block.kind) * PX_PER_PT
        weight = "bold" if block.kind in ("name", "subject") else "normal"
        colour = "#666" if block.kind == "contact" else "#111"
        style = (
            f"margin:{LAYOUT.gap_before(block.kind) * PX_PER_PT:.0f}px 0 0;font-size:{size:.1f}px;"
            f"line-height:{1.2 if block.kind == 'name' else LAYOUT.line_spacing};"
            f"font-weight:{weight};color:{colour}"
        )
        text = html_text(block.text)  # user text inside HTML: always escaped
        lines.append(f'<p style="{style}">{text}</p>')
    st.markdown(
        '<div style="background:#fff;padding:40px 44px;border-radius:4px;'
        "box-shadow:0 1px 6px rgba(0,0,0,.25);font-family:Helvetica,Arial,sans-serif;"
        f'text-align:left">{"".join(lines)}</div>',
        unsafe_allow_html=True,
    )


def _draft_editors(job_id: int, drafts: Drafts, prefix: str, used_claim_ids: list[str]) -> Drafts:
    """The bullet editor, the letter editor and a live preview. Returns the edited drafts."""
    errors = form_errors(_drafts_scope(prefix))
    error_summary(errors, CoverLetterInput)
    st.text_area(
        "CV bullets * (one per line)",
        value="\n".join(f"- {b}" for b in drafts.cv_bullets),
        height=180,
        key=f"bullets_{prefix}",
        help=f"1–{C.CV_MAX_BULLETS} bullets, each up to {C.CV_BULLET_MAX_CHARS} characters.",
    )
    field_error(errors, "cv_bullets")
    st.markdown("##### Cover letter")
    edit_col, preview_col = st.columns(2, gap="large")
    with edit_col:
        _letter_editor(drafts.cover_letter, prefix)
    letter = _letter_from_inputs(prefix, drafts.cover_letter)
    with preview_col:
        st.caption("Preview (the PDF and Word files look like this)")
        _letter_preview(applications.cover_letter_blocks(job_id, letter))
    bullet_lines = st.session_state[f"bullets_{prefix}"].splitlines()
    return Drafts(
        cv_bullets=[b for b in (re.sub(r"^\s*[-•*]\s*", "", x).strip() for x in bullet_lines) if b],
        cover_letter=letter,
        used_claim_ids=used_claim_ids,
    )


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
            st.toast(f"🧠 I learned: *{md(rule.rule)}*")
            toasted.add(rule.key)
    with st.container(border=True):
        st.markdown("🧠 **I learned from your feedback.** New letters will follow:")
        for rule in rules:
            text_col, undo_col = st.columns([6, 1], vertical_alignment="center")
            text_col.markdown(f"- {md(rule.rule)}")
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
            max_chars=C.FEEDBACK_COMMENT_MAX_CHARS,
        )
        if not st.form_submit_button("Send rating"):
            return
    try:
        applications.rate_drafts(job_id, target, -1, comment)
    except InvalidInputError as exc:
        st.error(exc.user_message)
        return
    st.session_state[_why_key(key)] = False
    learned = learning.learn_from_ratings(target) if comment.strip() else []
    _remember_learned(job_id, learned)
    st.toast("Thanks, I saved your rating.")
    st.rerun()


def _download(column, file: ExportFile, label: str, primary: bool) -> None:
    column.download_button(
        "⬇️ Letter (.txt)" if file.fell_back else label,
        data=file.data,
        file_name=file.file_name,
        mime=file.mime,
        type="primary" if primary else "secondary",
        on_click="ignore",
        key=f"download_{label}",
    )


def _export_copy_rate(job_id: int, drafts: Drafts, prefix: str) -> None:
    card = applications.get_job_summary(job_id)
    pdf = applications.export_cover_letter(job_id, drafts.cover_letter, "pdf")
    docx = applications.export_cover_letter(job_id, drafts.cover_letter, "docx")
    pdf_col, docx_col, rate_letter, rate_bullets = st.columns(4)
    _download(pdf_col, pdf, "⬇️ PDF", primary=True)
    _download(docx_col, docx, "⬇️ Word (.docx)", primary=False)
    if pdf.fell_back or docx.fell_back:
        st.warning(
            "Sorry, I couldn't build the "
            + " and ".join(n for n, f in (("PDF", pdf), ("Word file", docx)) if f.fell_back)
            + ". You can still download your letter as a .txt file and paste it into your "
            "own template.",
            icon="⚠️",
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


def _check_drafts(edited: Drafts, prefix: str) -> None:
    """Validate the edited drafts; on errors rerun with messages under the fields."""
    try:
        applications.validate_drafts(edited)
    except InvalidInputError as exc:
        fail_form(_drafts_scope(prefix), exc.errors)
    clear_form_errors(_drafts_scope(prefix))


def _draft_review(view: WorkflowView) -> None:
    versions = applications.list_draft_versions(view.job_id)
    prefix = f"{view.job_id}_{versions[0].version if versions else 0}"
    _learned_rules_box(view)
    guard_issues_box(view.guard_issues)
    edited = _draft_editors(view.job_id, view.drafts, prefix, view.drafts.used_claim_ids)

    if busy_button(
        "✅ Accept & save", key=f"accept_{prefix}", type="primary", use_container_width=True
    ):
        _check_drafts(edited, prefix)
        _after(
            run_with_progress(
                "Saving your drafts",
                lambda p: applications.submit_draft_review(
                    view.job_id, "accept", edited=edited, on_progress=p
                ),
                done="Drafts saved",
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
            max_chars=C.INSTRUCTION_MAX_CHARS,
        )
        st.caption("Your edits to the other section are kept.")
        if busy_button("Regenerate", key=f"regenerate_{prefix}"):
            _check_drafts(edited, prefix)
            _after(
                run_with_progress(
                    "Rewriting the section",
                    lambda p: applications.submit_draft_review(
                        view.job_id,
                        "regenerate",
                        edited=edited,
                        section=section,
                        instruction=instruction,
                        on_progress=p,
                    ),
                    done="Section rewritten",
                )
            )
    _export_copy_rate(view.job_id, edited, prefix)


def _accepted_drafts(view: WorkflowView) -> None:
    versions = applications.list_draft_versions(view.job_id)
    latest = versions[0]
    prefix = f"{view.job_id}_{latest.version}"
    st.success("You accepted these drafts. You can still edit them; each save is a new version.")
    _learned_rules_box(view)
    edited = _draft_editors(view.job_id, latest.drafts, prefix, [])
    if busy_button("💾 Save changes", key=f"save_drafts_{prefix}"):
        _check_drafts(edited, prefix)
        saved = applications.save_edited_drafts(view.job_id, edited)
        st.toast("Saved as a new version." if saved else "Nothing changed.")
        if saved:
            with progress_status("Learning from your edits", done="Style check done"):
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


def _recent_news(news: list | None) -> None:
    """Dated news from the last 12 months (filtered in Python), undated items marked."""
    st.markdown("**Recent news**")
    if news is None:
        st.caption("Research again to load news from the last 12 months.")
    elif not news:
        st.caption("No recent news found in the last 12 months.")
    else:
        st.markdown("\n".join(f"- {news_line(item)}" for item in news))


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
    if card.company == UNKNOWN_COMPANY:
        st.info(
            "The posting doesn't name the company. Add its name with **✏️** next to the job "
            "title above, then research it here.",
            icon="🏢",
        )
        return
    scope = f"company_focus_{job_id}"
    focus, clicked = input_with_action(
        "Focus (optional)",
        "🔄 Research again" if research else f"🔎 Research {card.company}",
        key=f"company_focus_{job_id}",
        placeholder="e.g. tech stack, recent funding, engineering culture",
        max_chars=C.FOCUS_MAX_CHARS,
        disabled=not (settings.has_tavily_key and settings.has_llm_key),
    )
    field_error(form_errors(scope), "focus")
    if clicked:
        clean, errors = check_input(CompanyFocusInput, {"focus": focus})
        if clean is None:
            fail_form(scope, errors)
        clear_form_errors(scope)
        with progress_status(
            f"Researching {md(card.company)}", done="Company research done"
        ) as progress:
            progress("Searching the web and summarising what I find…")
            fresh, message = company.run_research(job_id, focus)
            if fresh is None:
                progress.fail(message)
        if fresh is not None:
            st.rerun()

    if research is None:
        st.caption("No research yet. It's saved for this job, so you only pay for it once.")
        return
    st.markdown(f"#### {md(research.company)}")
    st.write(research.brief.summary)
    left, right = st.columns(2)
    with left:
        _bullets("Products", research.brief.products)
        _bullets("Tech stack", research.brief.tech_stack)
    with right:
        _bullets("Culture", research.brief.culture)
        _recent_news(research.news)
    if research.brief.talking_points:
        st.success(
            "**Things you could mention**\n"
            + "\n".join(f"- {point}" for point in research.brief.talking_points),
            icon="💡",
        )
    with st.expander(f"🔗 Sources ({len(research.sources)})"):
        for source in research.sources:
            st.markdown(f"- [{md(source.title or source.url)}]({source.url})")
    st.caption(
        f"Researched {research.researched_at:%d %b %Y %H:%M} UTC from web search results. "
        "Web content can be wrong or out of date; check the sources."
    )


# ---------------------------------------------------------------- salary tab


def _salary_sources(estimate) -> None:
    usable = [p for p in estimate.usable if not p.from_posting]
    dropped = [p for p in estimate.data_points if p.dropped_reason]
    with st.expander(f"🔗 Sources and data points ({len(usable)} used, {len(dropped)} dropped)"):
        for p in usable:
            point = p.point
            mark = "🎯" if p.relevant else "▫️"
            st.markdown(
                f"- {mark} **€{p.yearly_gross:,.0f}**/year · {point.role or '?'}, "
                f"{point.location or '?'} · from {point.amount:,.0f} {point.currency} "
                f"{point.period} {point.basis} · [{p.source_title or 'source'}]({point.source_url})"
            )
        for p in dropped:
            st.caption(f"Dropped: {p.point.amount:,.0f} {p.point.currency} ({p.dropped_reason})")
        st.caption(
            "🎯 = same role and place as this job. Searches: " + " · ".join(estimate.queries)
        )


def _salary_tab(job_id: int) -> None:
    settings = get_settings()
    estimate = salary.saved_estimate(job_id)
    if not settings.has_tavily_key:
        st.info(
            "Salary estimates use Tavily web search. Get a free key at tavily.com, set "
            "`TAVILY_API_KEY` in `.env` and restart the app.",
            icon="🔑",
        )
    label = "🔄 Refresh" if estimate else "💰 Estimate salary"
    if busy_button(
        label,
        key=f"salary_run_{job_id}",
        type="secondary" if estimate else "primary",
        disabled=not (settings.has_tavily_key and settings.has_llm_key),
    ):
        with progress_status("Estimating the salary", done="Salary estimate done") as progress:
            progress("Searching salary data and computing the range…")
            fresh, message = salary.run_estimate(job_id)
            if fresh is None:
                progress.fail(message)
        if fresh is not None:
            st.rerun()

    if estimate is None:
        st.caption(
            "No estimate yet. I search the web for salaries of similar roles (the Entgeltatlas "
            "for jobs in Germany), compute a range from the figures I find, and compare it "
            "with your expectation. It's saved for this job."
        )
        return
    if estimate.status != "ok":
        posting = (
            f"the posting's salary ({estimate.posting_salary}) couldn't be read"
            if estimate.posting_salary
            else "the posting doesn't state a salary"
        )
        st.warning(
            f"**Not enough data.** I found {len(estimate.usable)} usable salary figure(s) and "
            f"{posting}, so I won't guess a range. Try **Refresh** "
            "later, check the [Entgeltatlas](https://web.arbeitsagentur.de/entgeltatlas/), or "
            "ask the recruiter for the salary band.",
            icon="🤷",
        )
        _salary_sources(estimate)
        st.caption(f"Checked {estimate.estimated_at:%d %b %Y %H:%M} UTC. {DISCLAIMER}")
        return

    st.markdown(f"#### €{estimate.low:,} – €{estimate.high:,} per year (gross)")
    st.markdown(
        f"Typical: **€{estimate.typical:,}** · {CONFIDENCE_BADGES[estimate.confidence]} · "
        f"{len(estimate.usable)} data points"
    )
    if estimate.posting_salary:
        st.caption(f"The posting says: {md(estimate.posting_salary)}")
    if estimate.expectation:
        text = POSITION_TEXT.get(estimate.expectation_position, "")
        st.info(f"Your expectation: **{md(estimate.expectation)}**. {text}", icon="🎯")
    else:
        st.caption("Add your expected salary on the Memory page to compare it with the range.")

    if explanation := estimate.explanation:
        left, right = st.columns(2)
        with left:
            _bullets("⬆️ Pushes pay up", explanation.factors_up)
        with right:
            _bullets("⬇️ Pushes pay down", explanation.factors_down)
        st.write(explanation.expectation_comparison)
        if explanation.negotiation_tips:
            st.success(
                "**Negotiation tips**\n"
                + "\n".join(f"- {tip}" for tip in explanation.negotiation_tips),
                icon="💡",
            )
    _salary_sources(estimate)
    st.caption(
        f"Estimated {estimate.estimated_at:%d %b %Y %H:%M} UTC. The range is the 25th–75th "
        f"percentile of the figures found, converted to yearly gross EUR. **{DISCLAIMER}**"
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
        st.chat_message("user").markdown(md(turn.answer))
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
    scope = f"answer_{view.job_id}_{view.number}"
    errors = form_errors(scope)
    with st.form(scope):
        text = st.text_area(
            "Your answer *",
            height=180,
            max_chars=C.ANSWER_MAX_CHARS,
            placeholder="Answer as you would in the interview. Tip: Situation → Task → Action "
            "→ Result, with one number if you have it.",
        )
        field_error(errors, "text")
        send_col, stop_col = st.columns(2)
        with send_col:
            send = busy_button(
                "Send answer ▶",
                key=f"{scope}_send",
                form=True,
                type="primary",
                use_container_width=True,
            )
        with stop_col:
            stop = busy_button(
                "End interview now", key=f"{scope}_stop", form=True, use_container_width=True
            )
    if send:
        check = check_interview_answer(text)  # the same check the service runs again
        if not check.allowed:
            fail_form(scope, {"text": check.reason})  # reruns; the answer stays in the box
        clear_form_errors(scope)
        _after(
            run_with_progress(
                "Scoring your answer",
                lambda p: interview.answer(view.job_id, text, on_progress=p),
                done="Answer scored",
            )
        )
    if stop:
        _after(
            run_with_progress(
                "Summarising your interview",
                lambda p: interview.end_early(view.job_id, on_progress=p),
                done="Summary ready",
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
    if busy_button(label, type="primary" if primary else "secondary", key=f"start_iv_{job_id}"):
        _after(
            run_with_progress(
                "Preparing your first question",
                lambda p: interview.start(job_id, on_progress=p),
                done="First question ready",
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
        if busy_button("🔁 Retry", key=f"retry_iv_{job_id}"):
            _after(
                run_with_progress(
                    "Continuing the interview",
                    lambda p: interview.retry(job_id, on_progress=p),
                    done="Interview continued",
                )
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
    scope = f"notes_{job_id}"
    errors = form_errors(scope)
    with st.form(scope):
        error_summary(errors, NoteInput)
        notes = st.text_area("Notes", value=card.notes, height=140, max_chars=C.NOTES_MAX_CHARS)
        field_error(errors, "notes")
        action_col, date_col = st.columns([2, 1])
        with action_col:
            next_action = st.text_input(
                "Next action",
                value=card.next_action or "",
                placeholder="e.g. Follow up with the recruiter",
                max_chars=C.SHORT_TEXT_MAX_CHARS,
            )
            field_error(errors, "next_action")
        with date_col:
            due = st.date_input(
                "Due",
                value=card.next_action_date.date() if card.next_action_date else None,
                format="DD.MM.YYYY",
            )
            field_error(errors, "next_action_date")
        submitted = busy_button("Save notes", key=f"{scope}_save", form=True)
    if submitted:
        clean, new_errors = check_input(
            NoteInput, {"notes": notes, "next_action": next_action, "next_action_date": due}
        )
        if clean is None:
            fail_form(scope, new_errors)
        clear_form_errors(scope)
        for warning in applications.save_notes(job_id, notes, next_action, due):
            st.toast(warning, icon="📅")
        st.toast("Notes saved.")

    versions = applications.list_draft_versions(job_id)
    st.markdown("##### Draft versions")
    if not versions:
        st.caption("No drafts yet.")
    for v in versions:
        who = "your edit" if v.is_user_edited else "AI draft"
        with st.expander(f"Version {v.version} · {who} · {v.created_at:%d %b %Y %H:%M}"):
            st.markdown("\n".join(f"- {md(b)}" for b in v.drafts.cv_bullets))
            st.text(v.drafts.cover_letter.text)


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
head, controls = st.columns([4, 3])
head.markdown(f"### {md(card.title)}\n{md(card.company)}")
with controls:
    status_col, rename_col, delete_col = action_row(4, 1, 1)
new_status = status_col.selectbox(
    "Board column", STATUSES, index=STATUSES.index(card.status), format_func=STATUS_LABELS.get
)
if new_status != card.status:
    applications.change_status(job_id, new_status)
    st.rerun()
with (
    rename_col.popover("✏️", help="Correct the job title or company"),
    st.form(f"rename_{job_id}", border=False),
):
    new_title = st.text_input("Job title", value=card.title, max_chars=C.JOB_TITLE_MAX_CHARS)
    unknown = card.company == UNKNOWN_COMPANY
    new_company = st.text_input(
        "Company",
        value="" if unknown else card.company,
        placeholder="The posting doesn't name it" if unknown else None,
        max_chars=C.COMPANY_MAX_CHARS,
    )
    saved = st.form_submit_button("Save", type="primary")
if saved:
    try:
        if applications.rename_job(job_id, new_title, new_company):
            st.rerun()
    except InvalidInputError as exc:
        st.error(exc.user_message, icon="✏️")
with delete_col.popover("🗑️", help="Delete this job"):
    st.write("Delete this job and its analysis? This can't be undone.")
    if st.button("Delete", type="primary"):
        applications.delete_job(job_id)
        st.query_params.clear()
        st.switch_page(BOARD_PAGE)

view = applications.get_workflow_view(job_id)
if view.stage in TAB_FOR_STAGE:
    st.info(f"Waiting for you in **{TAB_FOR_STAGE[view.stage]}**.", icon="👉")
fit_tab, claims_tab, docs_tab, company_tab, salary_tab, interview_tab, notes_tab = st.tabs(
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
with salary_tab:
    _salary_tab(job_id)
with interview_tab:
    _interview_tab(job_id)
with notes_tab:
    _notes_tab(job_id)
