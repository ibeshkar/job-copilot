# Demo script (5–7 minutes)

What to click and what to say in the live review. Times are targets; total ≈ 6.5 minutes.

## Before the review (10 minutes earlier)

1. `uv run streamlit run app/main.py` and check the banner says nothing about the API key.
2. **Start from a clean state** so onboarding shows: Memory page → *Delete all my data* → type
   DELETE. (Or use a fresh folder: `RUNTIME_DIR=/tmp/demo uv run streamlit run app/main.py`.)
3. Pre-load one finished job as a safety net: add the **partial fit** sample job and take it
   through to accepted drafts. If the live run fails, you can show this one instead.
4. Optional: turn on LangSmith (`LANGSMITH_TRACING=true` + key in `.env`) and open the
   project in a browser tab, so step 7 is one click.
5. Open `docs/assets/` in Finder: screenshots of every step (backup plan, see the end).
6. Close other tabs. Zoom the browser to 110% so the reviewer can read it.

---

## 1. The problem (30 s)

**Say:** "Tailoring a job application takes 30 to 60 minutes, people can't judge their own fit
objectively, and generic AI writers make it worse because they invent experience. This app
checks fit requirement by requirement with evidence from *your own* documents, writes only what
you approved, and keeps everything in one tracker. It's for career changers and junior people,
especially in the EU, where language and visa requirements matter."

## 2. Onboarding with the demo persona (45 s)

**Click:** 👋 Get started → *✨ Fill in the demo persona (Alex Novak)* → save the profile →
*🧪 Use the demo persona* → Continue → Preferences (leave *Friendly*) → *Finish setup ✔*.

**Point at:** "Your knowledge base: 21 chunks from 4 documents ✔".

**Say:** "Alex Novak is a fictional logistics coordinator who moved into AI engineering. The
four documents (CV, two project READMEs, a brag doc) were split into ~500-character chunks,
embedded, and stored in Chroma on my laptop. The profile goes into LangGraph's Store, which is
long-term memory: it survives restarts."

## 3. Add the good-fit job → fit card (75 s)

**Click:** 🗂️ Board → ➕ Add job → pick the sample **Good fit: …Junior AI Engineer…** → Analyse fit.

**Point at the status box while it runs:** "Each line is one graph node finishing: reading the
posting, extracting requirements, searching my documents, grading, scoring. That's
`stream_mode='updates'`."

**On the Fit tab, say:**
- "The summary says how many must-haves I meet. Gaps come first and open; each row has the
  **exact snippet and file** that proves it behind a button." *(click 📄 Evidence on a met
  requirement, then switch the filter to **Gaps only**)*
- "The LLM only decides met, partial or missing. **Python computes the score**: must-haves weigh
  3, nice-to-haves 1, met counts 1, partial half. So the same assessment always gives the same
  number, and I can explain where it came from."
- "The evidence search is **agentic RAG**: an LLM grades every chunk, and if nothing relevant is
  found it rewrites the query and searches again, at most twice."
- *(refresh the browser)* "Refreshing keeps everything: the graph is paused with `interrupt()`,
  and its state is in a SQLite checkpointer."

**Click:** Continue to application ▶.

## 4. Claim check → remove a claim (60 s)

**On the ✅ Claim check tab, say:** "Before anything is written, the agent shows every claim it
wants to make about me. Green ones are backed by a document; unsupported ones start unticked.
This is the second human-in-the-loop pause."

**Click:** untick one claim that's distinctive (for example the one about a specific project or
number). Remember its wording. → *Confirm claims and write drafts ▶*.

**On 📝 Documents, say:** "The writer's prompt only receives approved claims, so the removed
claim can't leak in. Then a second LLM call, the output guard, checks every statement about me
against the approved claims. Ctrl+F for the removed claim: it's not there."

## 5. Edit the letter → learned style rule (45 s)

**Click:** in the cover letter, delete a word like "passionate" or "excited" (or rewrite one
sentence shorter) → ✅ Accept & save.

**Point at the toast:** "🧠 I learned: Avoid the word 'passionate'", with Undo.

**Say:** "Python diffs my edit sentence by sentence, the LLM turns it into a general style rule,
and it's saved in long-term memory. The next letter gets the rule in its prompt, and the output
guard also searches for the banned word in plain Python." *(Optional: show it on 🧠 What I know
about you → Style rules.)*

**Click (optional, 10 s):** ⬇️ Export DOCX.

## 6. Copilot chat: tools + approval (60 s)

**Click:** 💬 Copilot chat. In the sidebar, show the 🔧 tool toggles. Type:
> Research the company for my newest job and move it to Applied

**Say while it runs:** "This one is a **ReAct agent**: the LLM decides which tools to call. It
calls `get_applications` to find the newest job, then `research_company` (Tavily). Each 🔧 line is
a tool call; the expanders show the arguments and results."

**When the approval card appears:** "`update_tracker` changes data, so the graph pauses before
it runs. That's another `interrupt()`." **Click:** Approve.

**Say:** "Why two kinds of agent? Applying is high-stakes and must never skip the claim check,
so code fixes the order. Chat questions are open-ended, so the LLM chooses."

*(If there is no Tavily key, type "Move my newest job to Applied" instead; same approval card.)*

## 7. LangSmith trace + cost badge (30 s)

**Point at the sidebar:** "💰 This session: … tokens · $…". "Every action is counted with a
usage callback, priced from a table, and saved to SQLite. A full fit analysis costs about a third
of a cent with gpt-4o-mini."

**Switch to LangSmith tab:** open the latest `analyse_fit` trace. "Each node is a span; the
metadata has the action, the job id and the prompt version, so I can compare runs before and
after a prompt change."

## 8. The poor-fit job (30 s)

**Click:** Board → ➕ Add job → the sample **Poor fit: …Senior ML Engineer…** → Analyse fit.

**Point at:** the red flag "German C1 required, you have A2" and the score ≤ 40.

**Say:** "A blocking red flag caps the score at 40, no matter how many skills match. The app
tells you honestly that this one isn't realistic, and suggests questions to ask instead of
pretending."

**Close (10 s):** "The evaluation report in `docs/EVALUATION.md` measures this: faithfulness
0.89, zero removed claims leaked in 5 cases, and an honest note that the partial-fit score still
moves too much between runs."

---

## Backup plans

| If… | Do this |
|---|---|
| OpenRouter is down / slow | Show the pre-loaded partial-fit job (step 3 of "Before"). Every page reads saved state, so no API call is needed to browse it. |
| Everything AI is down | Show screenshots in `docs/assets/` (one per step) and say what would happen. Then show `tests/test_injection.py` and `compute_fit_score()` in the editor. |
| The good-fit run gives an odd score | Say it honestly: "The LLM classification varies a bit; that's in my evaluation, §7.1". Point at the evidence instead of the number. |
| The output guard flags something | Good! "This is the guard working": show the highlighted statement. |
| No Tavily key | Use "Move my newest job to Applied" (tracker tools only). |
| Time is short | Skip steps 5 and 7; say one sentence about each. |

## Likely follow-up questions during the demo
See `REVIEW_GUIDE.md` §5. The top three: checkpointer vs. Store, why the score is Python, and
what happens with "ignore all previous instructions" in a posting (answer: `tests/test_injection.py`).
