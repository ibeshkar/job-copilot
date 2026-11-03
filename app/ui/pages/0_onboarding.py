# What: the 3-step onboarding wizard: profile → documents → writing preferences → done.
# Why: the app needs to know who the user is and what they have done before it can judge
#      job fit. Shown first automatically while no profile exists (see app/main.py).
# Used by: app/main.py (st.navigation). Logic lives in app/services/onboarding.py and
#          app/memory/store.py; this file only draws widgets.

import streamlit as st

from app.config import MAX_FILES_PER_UPLOAD, UPLOAD_EXTENSIONS
from app.memory.store import (
    Profile,
    get_preferences,
    get_profile,
    profile_problems,
    save_preferences,
    save_profile,
)
from app.services import onboarding
from app.services.onboarding import IngestReport
from app.ui.components import busy_button, preferences_form, profile_form, progress_status
from app.ui.state import ONBOARDING_STEP_KEY as STEP_KEY
from app.utils.errors import InvalidInputError
from app.validation.sanitize import md

PROFILE_DRAFT_KEY = "onboarding_profile_draft"


def _go(step: int) -> None:
    st.session_state[STEP_KEY] = step
    st.rerun()


def _show_report(report: IngestReport) -> None:
    for filename, chunks in report.added.items():
        st.success(f"Added **{md(filename)}** ({chunks} chunks)", icon="📄")
    for filename, message in report.errors.items():
        st.error(f"**{md(filename)}**: {md(message)}", icon="⚠️")


def _step_profile() -> None:
    st.subheader("Step 1 of 3: About you")
    st.caption("This helps me spot red flags like visa, language or seniority mismatches.")

    if st.button("✨ Fill in the demo persona (Alex Novak)"):
        st.session_state[PROFILE_DRAFT_KEY] = onboarding.DEMO_PROFILE
        st.rerun()

    saved = get_profile()
    problems = profile_problems(saved) if saved else {}
    if problems:
        st.warning(
            "Some of your saved details don't match the current rules. Please fix the marked "
            "fields and save again.",
            icon="✏️",
        )
    p = st.session_state.get(PROFILE_DRAFT_KEY) or saved or Profile(name="")
    profile = profile_form(p, "profile_form", "Save and continue →", problems=problems)
    if profile is not None:
        try:
            save_profile(profile)  # validates again before saving (defence in depth)
        except InvalidInputError as exc:
            st.error(exc.user_message)
            return
        st.session_state.pop(PROFILE_DRAFT_KEY, None)
        _go(2)


def _step_documents() -> None:
    st.subheader("Step 2 of 3: Your documents")
    st.caption(
        "Upload your CV, project READMEs, certificates, a brag doc or past cover letters. "
        "I only use what's in these files as evidence. PDF, DOCX, TXT or MD; up to 5 MB each "
        f"and {MAX_FILES_PER_UPLOAD} files at once."
    )

    uploads = st.file_uploader(
        "Add documents",
        type=[ext.lstrip(".") for ext in UPLOAD_EXTENSIONS],
        accept_multiple_files=True,
    )
    col_add, col_demo = st.columns(2)
    with col_add:
        add = busy_button(
            "Add to my knowledge base", key="docs_add", disabled=not uploads, type="primary"
        )
    if add:
        with progress_status("Indexing your documents", done="Documents indexed") as progress:
            progress(f"Reading and indexing {len(uploads)} file(s)…")
            report = onboarding.add_documents([(f.name, f.getvalue()) for f in uploads])
        _show_report(report)

    with col_demo:
        demo = busy_button("🧪 Use the demo persona", key="docs_demo")
    if demo:
        with progress_status("Loading the demo persona", done="Demo persona loaded") as progress:
            progress("Indexing Alex Novak's CV, project READMEs and brag doc…")
            report = onboarding.load_demo_documents()
        _show_report(report)
    col_demo.caption("Files: " + ", ".join(onboarding.demo_file_names()))

    chunks, docs = onboarding.knowledge_base_summary()
    if docs:
        st.info(f"Knowledge base so far: **{chunks} chunks from {docs} documents**.", icon="📚")
    else:
        st.warning(
            "Your knowledge base is empty. Fit analysis needs at least one document.", icon="📭"
        )

    col_back, col_next = st.columns(2)
    if col_back.button("← Back"):
        _go(1)
    if col_next.button("Continue →", type="primary"):
        _go(3)


def _step_preferences() -> None:
    st.subheader("Step 3 of 3: How should your applications sound?")
    prefs = preferences_form(get_preferences(), "preferences_form", "Finish setup ✔")
    if st.button("← Back"):
        _go(2)
    if prefs is not None:
        try:
            save_preferences(prefs)
        except InvalidInputError as exc:
            st.error(exc.user_message)
            return
        _go(4)


def _step_done() -> None:
    profile = get_profile()
    chunks, docs = onboarding.knowledge_base_summary()
    st.subheader(f"You're all set{', ' + md(profile.name) if profile else ''}! 🎉")
    st.success(f"Your knowledge base: **{chunks} chunks from {docs} documents** ✔")
    with st.expander("What's in it"):
        for filename, doc_type, n in onboarding.list_knowledge_base_files():
            st.markdown(f"- {md(filename)} ({doc_type}, {n} chunks)")

    col_board, col_edit = st.columns(2)
    if col_board.button("Go to my board →", type="primary"):
        st.switch_page("ui/pages/1_board.py")
    if col_edit.button("Edit my answers"):
        _go(1)


st.title("👋 Get started")
st.session_state.setdefault(STEP_KEY, 1)
st.progress(min(st.session_state[STEP_KEY], 3) / 3)

{1: _step_profile, 2: _step_documents, 3: _step_preferences, 4: _step_done}[
    st.session_state[STEP_KEY]
]()
