# What: the Help page: how the app works in 4 steps, a FAQ, the privacy note, and a small
#       help-bot that answers questions from docs/USER_GUIDE.md.
# Why: new users need to understand the flow (fit → claims → drafts → interview) and what
#      happens to their data, without leaving the app.
# Used by: app/main.py (st.navigation). The help-bot's work is done by app/services/help.py.

import streamlit as st

from app.config import CHAT_MAX_CHARS
from app.services import help as help_bot
from app.ui.components import progress_status
from app.validation.sanitize import md

HISTORY_KEY = "help_history"  # [(question, answer, sections)] of this browser session

STEPS = [
    (
        "1️⃣ Tell it about you",
        "Profile, documents (CV, project READMEs) and writing preferences on **Get started**. "
        "Or use the demo persona.",
    ),
    (
        "2️⃣ Check the fit",
        "Paste a job on the **Board**. You get a 0-100 score, red flags and evidence from your "
        "own documents for every requirement.",
    ),
    (
        "3️⃣ Apply honestly",
        "Approve, edit or remove each claim about you. The drafts use only what you approved, "
        "and a fact-checker looks for anything else.",
    ),
    (
        "4️⃣ Practise and track",
        "A 5-question mock interview with feedback, company research, the tracker board and "
        "the copilot chat.",
    ),
]

FAQ = [
    (
        "Will the app invent experience I don't have?",
        "No. Every statement about you must come from a claim you approved at the claim check. "
        "A second AI call checks the drafts, and plain Python checks that removed claims and "
        "words your style rules ban don't appear.",
    ),
    (
        "Why is my fit score low?",
        "Open the requirement list on the 🎯 Fit tab: each ❌ shows that your documents don't "
        "mention it. A **blocking** red flag (for example a required language) caps the score "
        "at 40. If you do have the experience, use **Add context and re-check**.",
    ),
    (
        "How does the app learn my style?",
        "When you edit a draft and save it, the app compares the two versions and may learn a "
        "rule such as \"Avoid the word 'passionate'\". You can undo it right away, or edit it "
        "on the 🧠 Memory page.",
    ),
    (
        "What does the tone selector change?",
        "How the app writes for you: new cover letters, chat answers, interview feedback and "
        "this help-bot. Formal, friendly or concise.",
    ),
    (
        "Can I continue later?",
        "Yes. Every step is saved. Refreshing the page or restarting the app brings you back to "
        "the same review or interview question.",
    ),
    (
        "Why can't the app read my LinkedIn link?",
        "Many job sites block automatic reading. Paste the job text instead.",
    ),
]

st.title("❓ Help")

st.markdown("### How it works")
for col, (title, text) in zip(st.columns(4), STEPS, strict=True):
    with col.container(border=True):
        st.markdown(f"**{title}**")
        st.caption(text)

st.markdown("### Ask the help-bot")
st.caption(
    "Questions about using the app, answered from the user guide. Questions about your own jobs "
    "and experience belong in the 💬 Copilot chat."
)
history = st.session_state.setdefault(HISTORY_KEY, [])
for question, answer, sections in history:
    st.chat_message("user").markdown(md(question))
    with st.chat_message("assistant", avatar="❓"):
        st.write(answer)
        if sections:
            st.caption("📖 From the guide: " + " · ".join(sections))

if question := st.chat_input("e.g. How do I undo a learned style rule?", max_chars=CHAT_MAX_CHARS):
    st.chat_message("user").markdown(md(question))
    with (
        st.chat_message("assistant", avatar="❓"),
        progress_status("Looking it up", done="Answer ready") as progress,
    ):
        progress("Searching the user guide and writing an answer…")
        reply = help_bot.ask(question, [(q, a) for q, a, _ in history])
        if not reply.ok:
            progress.fail(reply.answer)
    if reply.ok:
        history.append((question, reply.answer, reply.sections))
        st.rerun()

if history and st.button("🧹 Clear the conversation"):
    st.session_state[HISTORY_KEY] = []
    st.rerun()

st.markdown("### FAQ")
for question, answer in FAQ:
    with st.expander(question):
        st.markdown(answer)

st.markdown("### Privacy")
st.info(
    "Everything is stored only on your computer, in `data/runtime/`: your profile, documents, "
    "jobs, drafts, interviews and memories. Texts are sent to OpenRouter (the AI service), and "
    "to Tavily for company research, only while an AI step runs. See and delete what the app "
    "knows on **🧠 What I know about you**; **Delete all my data** removes everything.",
    icon="🔒",
)
