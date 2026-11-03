# What: the Copilot chat page: a chat with the ReAct copilot, its answer streamed live, each
#       tool call shown in a "🔧 Used: …" expander, an approval card before any tracker change,
#       starter prompts, a "New chat" button, and "Retry" when an answer broke off.
# Why: open questions ("What should I apply to next?") don't fit a fixed workflow. The page
#      only draws; app/services/copilot.py runs the graph. Like the job workspace, the history
#      is read from the checkpointer on every rerun, so a refresh keeps the conversation.
# Used by: app/main.py (st.navigation).

from collections.abc import Iterable

import streamlit as st

from app.config import CHAT_MAX_CHARS
from app.services import copilot
from app.services.copilot import ChatEvent, ChatTurn, PendingAction
from app.services.readiness import get_setup_status
from app.tools.registry import TOOL_REGISTRY
from app.ui.state import (
    ONBOARDING_DOCUMENTS_STEP,
    current_chat_id,
    enabled_toggles,
    open_onboarding,
    select_chat,
)
from app.validation.sanitize import md

ERROR_KEY = "chat_error"  # an error from the last run, shown once after the rerun
INPUT_KEY = "chat_input"
SENDING_KEY = "chat_sending"  # the submitted text while its answer streams (input disabled)


def _queue_message() -> None:
    """chat_input callback: keep the text, so this run can disable the box while it answers."""
    st.session_state[SENDING_KEY] = st.session_state.get(INPUT_KEY)


def _stream_answer(events: Iterable[ChatEvent]) -> None:
    """Draw the copilot's work live, then rerun so the history is drawn from saved state."""
    with st.chat_message("assistant"):
        placeholder = st.empty()
        text = ""
        for event in events:
            if event.kind == "token":
                text += event.text
                placeholder.markdown(text + " ▌")
            elif event.kind == "tool_call":
                st.caption(f"🔧 Using {event.text}…")
            elif event.kind == "approval":
                st.caption("✋ Waiting for your approval…")
            elif event.kind == "error":
                st.session_state[ERROR_KEY] = event.text
        placeholder.markdown(text)
    st.rerun()


def _turn(turn: ChatTurn) -> None:
    with st.chat_message(turn.role):
        for use in turn.tools:
            with st.expander(f"🔧 Used: {use.label}"):
                st.caption(f"`{use.name}`")
                st.json(use.args)
                if use.result:
                    st.text(use.result)
        if turn.text:
            # The user's own words are shown literally; the copilot's answer may use Markdown.
            st.markdown(md(turn.text) if turn.role == "user" else turn.text)


def _approval_card(thread_id: str, actions: list[PendingAction]) -> None:
    with st.chat_message("assistant"), st.container(border=True):
        st.markdown("**✋ May I make this change to your tracker?**")
        for action in actions:
            st.markdown(f"- {md(action.summary)}")
        approve_col, decline_col = st.columns(2)
        if approve_col.button("✅ Approve", type="primary", use_container_width=True):
            _stream_answer(copilot.answer_approval(thread_id, approved=True))
        if decline_col.button("✖️ Decline", use_container_width=True):
            _stream_answer(copilot.answer_approval(thread_id, approved=False))


# ---------------------------------------------------------------- page

st.title("💬 Copilot chat")
thread_id = current_chat_id(copilot.new_thread_id())

toggles = enabled_toggles()
info_col, new_col = st.columns([4, 1])
labels = [TOOL_REGISTRY[name].label for name in toggles]
info_col.caption(
    "Tools on: " + (", ".join(labels) if labels else "none") + " · change them in the sidebar."
)
if new_col.button("🆕 New chat", use_container_width=True):
    select_chat(copilot.new_thread_id())
    st.rerun()

# The chat still works without a profile or documents (company research, job search), but
# questions about the user's own experience can't be answered yet, so say so up front.
setup = get_setup_status()
if not setup.is_ready:
    note_col, go_col = st.columns([4, 1], vertical_alignment="center")
    note_col.info(
        "I don't know your profile or experience yet, so I can only help with companies and "
        "job ads. Set up your profile and CV to get answers about you.",
        icon="👋",
    )
    if go_col.button("Get started →", use_container_width=True):
        open_onboarding(1 if not setup.has_profile else ONBOARDING_DOCUMENTS_STEP)

history = copilot.get_history(thread_id)
pending = copilot.pending_approval(thread_id)

starter = None
if not history:
    st.info(
        "Ask me about your applications, companies, job ads or your own experience. "
        "I can look things up with my tools and, with your OK, update your tracker.",
        icon="💬",
    )
    columns = st.columns(len(copilot.STARTER_PROMPTS))
    for column, prompt in zip(columns, copilot.STARTER_PROMPTS, strict=True):
        if column.button(prompt, use_container_width=True):
            starter = prompt

for turn in history:
    _turn(turn)
if pending:
    _approval_card(thread_id, pending)
if error := st.session_state.pop(ERROR_KEY, None):
    st.error(error)
if not pending and copilot.can_retry(thread_id):
    # Read from the checkpointer, so the Retry button survives a refresh.
    st.warning("My last answer didn't finish. Retrying continues where it stopped.", icon="⏳")
    if st.button("🔁 Retry", type="primary"):
        _stream_answer(copilot.retry(thread_id))

sending = st.session_state.pop(SENDING_KEY, None)
st.chat_input(
    "Approve or decline the change above first" if pending else "Ask the copilot…",
    key=INPUT_KEY,
    disabled=bool(pending or sending),  # no second message while an answer streams
    max_chars=CHAT_MAX_CHARS,
    on_submit=_queue_message,
)
# Length, empty input and injection are checked by the graph's guard node, which answers
# politely instead of failing (app/guards/input_guard.py check_chat_message).
if text := sending or starter:
    with st.chat_message("user"):
        st.markdown(md(text))
    _stream_answer(copilot.send_message(thread_id, text, toggles))
