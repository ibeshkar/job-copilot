# What: the "What I know about you" page: the profile, writing preferences, facts confirmed in
#       claim checks, learned style rules and knowledge-base documents. Each can be edited or
#       deleted, and "Delete all my data" removes everything.
# Why: the app keeps personal data (a CV, salary wishes, confirmed facts). Users must see what
#      is stored and stay in control of it (PROJECT_PLAN.md §7 page 5, §9 PII).
# Used by: app/main.py (st.navigation). Work is done by app/services/my_data.py.

import streamlit as st

from app.memory.store import Profile
from app.services import my_data
from app.ui.components import preferences_form, profile_form
from app.ui.state import ONBOARDING_PAGE
from app.utils.errors import friendly_message

CONFIRM_WORD = "DELETE"
FLASH_KEY = "memory_flash"  # a success message shown once after the rerun


def _done(message: str) -> None:
    st.session_state[FLASH_KEY] = message
    st.rerun()


def _editable_list(kind: str, items: dict[str, str], on_save, on_delete) -> None:
    """One row per item: an editable text, 💾 save and 🗑️ delete."""
    for key, text in items.items():
        text_col, save_col, delete_col = st.columns([8, 1, 1], vertical_alignment="bottom")
        new_text = text_col.text_input(
            kind, value=text, key=f"{kind}_{key}", label_visibility="collapsed"
        )
        if save_col.button("💾", key=f"save_{kind}_{key}", help="Save the new wording"):
            on_save(key, new_text)
            _done(f"{kind.capitalize()} updated.")
        if delete_col.button("🗑️", key=f"delete_{kind}_{key}", help="Forget this"):
            on_delete(key)
            _done(f"{kind.capitalize()} deleted.")


st.title("🧠 What I know about you")
st.caption(
    "Everything the app remembers about you is listed here and stored only on this computer "
    "(`data/runtime/`). Texts are sent to OpenRouter (and Tavily for company research) only "
    "while an AI step runs."
)
if flash := st.session_state.pop(FLASH_KEY, None):
    st.success(flash, icon="✅")

data = my_data.load()
if data.is_empty:
    st.info(
        "I don't know anything about you yet. Set up your profile and documents on the "
        "**Get started** page.",
        icon="🧠",
    )
    if st.button("Get started →", type="primary"):
        st.switch_page(ONBOARDING_PAGE)

profile_tab, prefs_tab, facts_tab, rules_tab, docs_tab = st.tabs(
    [
        "👤 Profile",
        "✍️ Preferences",
        f"✅ Confirmed facts ({len(data.facts)})",
        f"📏 Style rules ({len(data.style_rules)})",
        f"📚 Documents ({len(data.documents)})",
    ]
)

with profile_tab:
    st.caption("Used to spot red flags (visa, language, seniority, location) in job postings.")
    new_profile = profile_form(data.profile or Profile(name=""), "memory_profile", "Save profile")
    if new_profile is not None:
        my_data.save_profile(new_profile)
        _done("Profile saved.")
    if data.profile and st.button("🗑️ Delete my profile"):
        my_data.delete_profile()
        _done("Profile deleted. The Get started page will ask for it again.")

with prefs_tab:
    st.caption("How your cover letters and the copilot's answers should sound.")
    new_prefs = preferences_form(data.preferences, "memory_prefs", "Save preferences")
    if new_prefs is not None:
        my_data.save_preferences(new_prefs)
        _done("Preferences saved.")
    if st.button("↩️ Reset to defaults"):
        my_data.reset_preferences()
        _done("Preferences reset to the defaults.")

with facts_tab:
    st.caption(
        "Things you confirmed in a claim check that your documents don't show. Drafts may use "
        "them as true. Edit the wording or delete the ones that are wrong."
    )
    if not data.facts:
        st.info("No confirmed facts yet. They appear after your first claim check.", icon="✅")
    _editable_list(
        "fact",
        {key: fact.text for key, fact in data.facts.items()},
        my_data.update_fact,
        my_data.delete_fact,
    )

with rules_tab:
    st.caption(
        "Writing rules for your drafts, e.g. \"Avoid the word 'passionate'\". They are learned "
        "from your edits (Phase 8) and applied to every new letter."
    )
    if not data.style_rules:
        st.info("No style rules yet.", icon="📏")
    _editable_list(
        "style rule",
        {key: rule.rule for key, rule in data.style_rules.items()},
        my_data.update_style_rule,
        my_data.delete_style_rule,
    )

with docs_tab:
    st.caption("The files fit analysis searches for evidence. Add more on the Get started page.")
    if not data.documents:
        st.info("No documents yet.", icon="📚")
    for doc in data.documents:
        name_col, delete_col = st.columns([9, 1], vertical_alignment="center")
        name_col.markdown(f"📄 `{doc.filename}` · {doc.doc_type} · {doc.chunk_count} chunks")
        if delete_col.button("🗑️", key=f"delete_doc_{doc.filename}", help="Remove this file"):
            try:
                my_data.delete_document(doc.filename)
            except Exception as exc:
                st.error(friendly_message(exc))
            else:
                _done(f"{doc.filename} removed from your knowledge base.")

st.divider()
with st.expander("⚠️ Delete all my data"):
    st.markdown(
        "This permanently deletes your profile, preferences, confirmed facts, style rules, "
        "documents, all jobs and drafts, saved chats, usage history and the log file. "
        "It can't be undone."
    )
    typed = st.text_input(f"Type **{CONFIRM_WORD}** to confirm", key="confirm_delete_all")
    if st.button("Delete everything", type="primary", disabled=typed.strip() != CONFIRM_WORD):
        try:
            my_data.delete_all_data()
        except Exception as exc:
            st.error(friendly_message(exc))
        else:
            # Forget this browser session's state too (open job, chat, wizard step).
            for key in list(st.session_state):
                del st.session_state[key]
            st.query_params.clear()
            st.switch_page(ONBOARDING_PAGE)
