# What: the 3-step onboarding wizard: profile → documents → writing preferences → done.
# Why: the app needs to know who the user is and what they have done before it can judge
#      job fit. Shown first automatically while no profile exists (see app/main.py).
# Used by: app/main.py (st.navigation). Logic lives in app/services/onboarding.py and
#          app/memory/store.py; this file only draws widgets.

import streamlit as st

from app.memory.store import (
    Preferences,
    Profile,
    get_preferences,
    get_profile,
    save_preferences,
    save_profile,
)
from app.services import onboarding
from app.services.onboarding import IngestReport

STEP_KEY = "onboarding_step"
PROFILE_DRAFT_KEY = "onboarding_profile_draft"
SENIORITIES = ["intern", "junior", "mid", "senior", "lead"]
TONES = ["formal", "friendly", "concise"]
LENGTHS = ["short", "medium", "long"]


def _split(text: str) -> list[str]:
    """Turn "Berlin, Remote" into ["Berlin", "Remote"]."""
    return [part.strip() for part in text.split(",") if part.strip()]


def _go(step: int) -> None:
    st.session_state[STEP_KEY] = step
    st.rerun()


def _show_report(report: IngestReport) -> None:
    for filename, chunks in report.added.items():
        st.success(f"Added **{filename}** ({chunks} chunks)", icon="📄")
    for filename, message in report.errors.items():
        st.error(f"**{filename}**: {message}", icon="⚠️")


def _step_profile() -> None:
    st.subheader("Step 1 of 3: About you")
    st.caption("This helps me spot red flags like visa, language or seniority mismatches.")

    if st.button("✨ Fill in the demo persona (Alex Novak)"):
        st.session_state[PROFILE_DRAFT_KEY] = onboarding.DEMO_PROFILE
        st.rerun()

    p = st.session_state.get(PROFILE_DRAFT_KEY) or get_profile() or Profile(name="")
    with st.form("profile_form"):
        name = st.text_input("Your name *", value=p.name)
        roles = st.text_input(
            "Target roles (comma-separated)",
            value=", ".join(p.target_roles),
            placeholder="Junior AI Engineer, Data Analyst",
        )
        seniority = st.selectbox("Seniority", SENIORITIES, index=SENIORITIES.index(p.seniority))
        locations = st.text_input(
            "Locations (comma-separated)",
            value=", ".join(p.locations),
            placeholder="Berlin, Remote (EU)",
        )
        work_auth = st.text_input(
            "Work authorisation",
            value=p.work_authorisation,
            placeholder="EU citizen / Blue Card / needs visa sponsorship",
        )
        languages = st.text_input(
            "Languages with level (comma-separated)",
            value=", ".join(p.languages),
            placeholder="English C1, German A2",
        )
        salary = st.text_input("Salary expectations (optional)", value=p.salary_expectations or "")
        submitted = st.form_submit_button("Save and continue →", type="primary")

    if submitted:
        if not name.strip():
            st.error("Please enter your name.")
            return
        save_profile(
            Profile(
                name=name.strip(),
                target_roles=_split(roles),
                seniority=seniority,
                locations=_split(locations),
                work_authorisation=work_auth.strip(),
                languages=_split(languages),
                salary_expectations=salary.strip() or None,
            )
        )
        st.session_state.pop(PROFILE_DRAFT_KEY, None)
        _go(2)


def _step_documents() -> None:
    st.subheader("Step 2 of 3: Your documents")
    st.caption(
        "Upload your CV, project READMEs, certificates, a brag doc or past cover letters. "
        "I only use what's in these files as evidence. PDF, DOCX, TXT or MD; up to 5 MB each "
        f"and {onboarding.MAX_FILES_PER_UPLOAD} files at once."
    )

    uploads = st.file_uploader(
        "Add documents", type=["pdf", "docx", "txt", "md"], accept_multiple_files=True
    )
    col_add, col_demo = st.columns(2)
    if col_add.button("Add to my knowledge base", disabled=not uploads, type="primary"):
        with st.spinner("Reading and indexing your documents…"):
            report = onboarding.add_documents([(f.name, f.getvalue()) for f in uploads])
        _show_report(report)

    if col_demo.button("🧪 Use the demo persona"):
        with st.spinner("Loading Alex Novak's CV, project READMEs and brag doc…"):
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
    prefs = get_preferences()
    with st.form("preferences_form"):
        tone = st.radio("Tone", TONES, index=TONES.index(prefs.tone), horizontal=True)
        length = st.radio(
            "Cover letter length",
            LENGTHS,
            index=LENGTHS.index(prefs.letter_length),
            horizontal=True,
        )
        language = st.text_input("Language of letters", value=prefs.letter_language)
        deal_breakers = st.text_input(
            "Deal-breakers (comma-separated)",
            value=", ".join(prefs.deal_breakers),
            placeholder="No relocation, no unpaid overtime",
        )
        submitted = st.form_submit_button("Finish setup ✔", type="primary")

    if st.button("← Back"):
        _go(2)
    if submitted:
        save_preferences(
            Preferences(
                tone=tone,
                letter_length=length,
                letter_language=language.strip() or "Same as the job posting",
                deal_breakers=_split(deal_breakers),
            )
        )
        _go(4)


def _step_done() -> None:
    profile = get_profile()
    chunks, docs = onboarding.knowledge_base_summary()
    st.subheader(f"You're all set{', ' + profile.name if profile else ''}! 🎉")
    st.success(f"Your knowledge base: **{chunks} chunks from {docs} documents** ✔")
    with st.expander("What's in it"):
        for filename, doc_type, n in onboarding.list_knowledge_base_files():
            st.write(f"- `{filename}` ({doc_type}, {n} chunks)")

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
