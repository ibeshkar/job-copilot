# What: the "What I know about you" page: the profile, writing preferences, facts confirmed in
#       claim checks, learned style rules and knowledge-base documents. Each can be edited or
#       deleted, and "Delete all my data" removes everything.
# Why: the app keeps personal data (a CV, salary wishes, confirmed facts). Users must see what
#      is stored and stay in control of it (PROJECT_PLAN.md §7 page 5, §9 PII).
# Used by: app/main.py (st.navigation). Work is done by app/services/my_data.py.

import streamlit as st

from app import config as C
from app.memory.store import Profile
from app.services import my_data
from app.ui.components import (
    action_row,
    busy_button,
    clear_form_errors,
    fail_form,
    field_error,
    form_errors,
    preferences_form,
    profile_form,
)
from app.ui.state import ONBOARDING_PAGE
from app.utils.errors import InvalidInputError, friendly_message
from app.validation.sanitize import md

CONFIRM_WORD = "DELETE"
FLASH_KEY = "memory_flash"  # a success message shown once after the rerun
NEW_RULE_VERSION = "memory_new_rule_version"  # bumped after a rule is added: empties the box


def _done(message: str) -> None:
    st.session_state[FLASH_KEY] = message
    st.rerun()


RULE_SOURCES = {
    "edit_diff": "learned from your edits",
    "rating": "learned from your 👎 comments",
    "explicit": "added by you",
}


def _editable_list(
    kind: str,
    items: dict[str, str],
    on_save,
    on_delete,
    max_chars: int,
    captions: dict[str, str] | None = None,
) -> None:
    """One row per item: an editable text, 💾 save and 🗑️ delete, and an optional caption.

    The save goes through the memory store's validation; a wrong wording keeps the typed text
    and shows the message under the row.
    """
    errors = form_errors(f"memory_{kind}")
    for key, text in items.items():
        text_col, save_col, delete_col = action_row(8, 1, 1)
        new_text = text_col.text_input(
            kind,
            value=text,
            key=f"{kind}_{key}",
            label_visibility="collapsed",
            max_chars=max_chars,
        )
        if save_col.button("💾", key=f"save_{kind}_{key}", help="Save the new wording"):
            try:
                on_save(key, new_text)
            except InvalidInputError as exc:
                fail_form(f"memory_{kind}", {key: next(iter(exc.errors.values()))})
            clear_form_errors(f"memory_{kind}")
            _done(f"{kind.capitalize()} updated.")
        field_error(errors, key)
        if delete_col.button("🗑️", key=f"delete_{kind}_{key}", help="Forget this"):
            on_delete(key)
            _done(f"{kind.capitalize()} deleted.")
        if captions and captions.get(key):
            st.caption(captions[key])  # below the row, so it can't push the buttons down


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
    # Saved before today's rules existed? Load it anyway and ask for the fixes (not a crash).
    problems = my_data.profile_problems(data.profile)
    if problems:
        st.warning(
            f"{len(problems)} saved detail{'s' if len(problems) > 1 else ''} in your profile "
            "don't match the current rules. Please fix the marked fields and save.",
            icon="✏️",
        )
    new_profile = profile_form(
        data.profile or Profile(name=""), "memory_profile", "Save profile", problems=problems
    )
    if new_profile is not None:
        try:
            my_data.save_profile(new_profile)
        except InvalidInputError as exc:
            st.error(exc.user_message)
        else:
            _done("Profile saved.")
    if data.profile and st.button("🗑️ Delete my profile"):
        my_data.delete_profile()
        _done("Profile deleted. The Get started page will ask for it again.")

with prefs_tab:
    st.caption("How your cover letters and the copilot's answers should sound.")
    new_prefs = preferences_form(data.preferences, "memory_prefs", "Save preferences")
    if new_prefs is not None:
        try:
            my_data.save_preferences(new_prefs)
        except InvalidInputError as exc:
            st.error(exc.user_message)
        else:
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
        max_chars=C.FACT_MAX_CHARS,
    )

with rules_tab:
    st.caption(
        "Writing rules for your drafts, e.g. \"Avoid the word 'passionate'\". I learn them when "
        "you edit a draft or explain a 👎, and follow them in every new letter. Banned words are "
        "also checked in Python, so a draft that uses one is rewritten once, then highlighted."
    )
    if not data.style_rules:
        st.info(
            "No style rules yet. Edit a cover letter and click **Accept & save**, or add one "
            "below.",
            icon="📏",
        )
    _editable_list(
        "style rule",
        {key: rule.rule for key, rule in data.style_rules.items()},
        my_data.update_style_rule,
        my_data.delete_style_rule,
        max_chars=C.STYLE_RULE_MAX_CHARS,
        captions={
            key: RULE_SOURCES[rule.source]
            + (f" · checks for: {md(', '.join(rule.avoid_phrases))}" if rule.avoid_phrases else "")
            for key, rule in data.style_rules.items()
        },
    )
    # No clear_on_submit: a rejected rule must stay in the box. After a successful add, a new
    # widget key gives an empty box instead.
    version = st.session_state.get(NEW_RULE_VERSION, 0)
    rule_errors = form_errors("add_style_rule")
    with st.form("add_style_rule"):
        new_rule = st.text_input(
            "Add a rule",
            placeholder="e.g. Keep the cover letter under 200 words",
            max_chars=C.STYLE_RULE_MAX_CHARS,
            key=f"new_rule_{version}",
        )
        field_error(rule_errors, "rule")
        add = busy_button("➕ Add rule", key="add_style_rule_submit", form=True)
    if add:
        try:
            my_data.add_style_rule(new_rule)
        except InvalidInputError as exc:
            fail_form("add_style_rule", exc.errors)
        clear_form_errors("add_style_rule")
        st.session_state[NEW_RULE_VERSION] = version + 1
        _done("Style rule added.")

with docs_tab:
    st.caption("The files fit analysis searches for evidence. Add more on the Get started page.")
    if not data.documents:
        st.info("No documents yet.", icon="📚")
    for doc in data.documents:
        name_col, delete_col = st.columns([9, 1], vertical_alignment="center")
        name_col.markdown(f"📄 {md(doc.filename)} · {doc.doc_type} · {doc.chunk_count} chunks")
        if delete_col.button("🗑️", key=f"delete_doc_{doc.filename}", help="Remove this file"):
            try:
                my_data.delete_document(doc.filename)
            except Exception as exc:
                st.error(friendly_message(exc))
            else:
                _done(f"{md(doc.filename)} removed from your knowledge base.")

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
