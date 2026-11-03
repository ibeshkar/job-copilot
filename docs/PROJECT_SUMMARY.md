# Project summary: Job Application Copilot

## 1. In one sentence
A local Streamlit app with an AI agent that checks whether you fit a job using evidence from
your own documents, writes an honest application from claims you approved, and tracks it.

## 2. The problem
Tailoring one application takes 30–60 minutes, so people send generic ones. It is hard to judge
your own fit objectively. Generic AI writers make it worse: they invent experience you don't
have. And applications get lost across tabs, notes and spreadsheets.

## 3. Who it's for
- Bootcamp graduates and career changers (for example, Turing College learners)
- Junior and mid-level tech professionals
- International applicants in the EU, where visa, language and location rules matter

## 4. How it works
1. **Profile:** fill in the onboarding wizard and upload your CV and other documents (or use the demo persona).
2. **Add a job:** paste the posting text or a URL on the Board.
3. **Fit analysis:** the agent extracts the requirements, searches your documents for evidence, and shows a 0–100 score with red flags. You decide: continue or stop.
4. **Claim check:** you approve, edit or remove every claim the agent wants to make about you.
5. **Documents:** it writes CV bullets and a cover letter from approved claims only. You edit, rate, and export PDF or Word.
6. **Track and prepare:** move the job on the board, research the company, get a salary estimate, and practise a 5-question mock interview.

## 5. Key features
- **Grounded fit analysis:** every requirement shows ✅/⚠️/❌ with the quoted evidence; the score is computed in Python, not by the LLM.
- **Claim check (human-in-the-loop):** the workflow pauses; nothing reaches the writer without your approval.
- **Output guard:** a second LLM check flags letter sentences not backed by an approved claim.
- **Learns from you:** your edits and repeated 👎 ratings become style rules for future drafts.
- **Copilot chat:** an agent with 7 tools (6 on/off toggles) that asks before changing your tracker.
- **Company research** and **salary estimate** from web data (Tavily); the salary range is calculated in Python, or "not enough data".
- **Mock interview:** 5 questions, a 1–5 score with one tip per answer, and a better example answer.
- **Kanban tracker board:** Saved → Applied → Interview → Offer / Rejected.
- **One-page PDF and Word export** of the cover letter.
- **Token and cost tracking** per action and per session.

## 6. What makes it different from just using ChatGPT
- It **proves** fit with quotes from your own documents instead of guessing.
- It **cannot invent experience** without you noticing: claim check + output guard.
- It **remembers you** (profile, facts, style) and keeps every application in one place.

## 7. Tech in one glance
- **UI:** Streamlit, 7 pages.
- **Agent framework:** LangGraph, 3 graphs (a fixed workflow, a ReAct chat agent, an interview loop).
- **LLM:** `openai/gpt-4o-mini` via OpenRouter (an OpenAI-compatible gateway).
- **Memory:** short-term = LangGraph checkpointer (saves each run step); long-term = LangGraph Store (profile, facts, style rules), both SQLite.
- **Knowledge base:** your documents chunked and embedded into Chroma (a local vector database).
- **Tools:** job posting reader, experience search, company research, job search (Arbeitnow), salary estimate, tracker read/update.
- **Observability:** optional LangSmith tracing (records every LLM call) with action and prompt-version tags.
- **Evaluation:** DeepEval (LLM-as-judge) plus plain Python metrics, in `evals/`.

## 8. Project stats

| Metric | Value |
|---|---|
| App code (`app/`) | 72 Python files, 13,850 lines |
| Eval + script code | 11 files, 1,267 lines |
| Test code (`tests/`) | 42 files, 7,737 lines |
| Tests | 637 total: 623 offline, **623 passed (100%)**; 14 live tests not run here |
| Lint (`ruff check .`) | 0 issues |
| Agent tools | 7 tools in 6 toggles (1 needs approval) |
| LangGraph graphs / nodes | 3 graphs / 23 nodes (15 + 4 + 4) |
| Human-in-the-loop interrupt points | 5 (fit review, claim check, draft review, chat tool approval, interview answer) |
| UI pages | 7 |
| Docs | 23 Markdown files (README + 11 in `docs/`, including this one, + 11 phase notes), 92 recorded decisions |
| Evaluation | Extraction precision / recall 1.00 / 0.98; faithfulness 0.89; tailoring 0.84; leaked removed claims 0 / 5; chat relevancy 0.86; fit-score spread ≤ 6.5 points (target ≤ 5 ❌) |
| Tokens and cost per full application | ~25,000 tokens, ~$0.006 (last documented demo run, Phase 10); the local usage log is empty now, so not re-measured |
| Course optional tasks | Easy 4 (+1 partly) / Medium 6 of 8 / Hard 5 of 5 |

*How measured (3 Nov 2026):* lines = `wc -l` on git-tracked `.py` files (includes comments and
blank lines); tests = `pytest --co` and `pytest -m "not live"`; graphs, nodes and interrupts = `grep`
for `StateGraph(`, `add_node` and `interrupt(` in `app/`; tools = `app/tools/registry.py`; pages = `app/ui/pages/`;
evaluation = `docs/EVALUATION.md` §6 (extraction from the 29 Oct 12:38 run, other metrics from the last full run, 12:18);
cost = `docs/REVIEW_GUIDE.md` §7 / README §12, since `usagelog` in `data/runtime/app.db` has 0 rows.

## 9. Requirements checklist
- ✅ **Agent purpose:** clear problem, users and "why not ChatGPT" (README §1).
- ✅ **Core functionality:** fit → claim check → drafts → tracker, plus chat, research, salary, interview.
- ✅ **User interface:** 7 Streamlit pages, onboarding checklist, help page with help-bot, loading/error states.
- ✅ **Technical implementation:** LangGraph, RAG, retries, friendly errors, input validation, guards, 623 tests.
- ✅ **Documentation:** README, user guide with screenshots, 4 common use cases, 92 decisions.

## 10. Honest limitations
- The fit score for a partial-fit job moves between runs (36–52), above the ±5 target.
- Single local user: no login, one Streamlit process; not ready for many users.
- Many job sites (for example LinkedIn) block reading, so pasting the text is the reliable path.
- The model list only has OpenAI models; the LLM can still mis-read evidence or slightly exaggerate.

## 11. What I'd do next
- Stabilise the fit score: temperature 0 and a second check that each "met" quote really proves the requirement.
- Multi-user: login, Postgres for memory and tracker, per-user knowledge base.
- Offer non-OpenAI models through OpenRouter and compare cost and quality with the eval suite.

---
More detail: [README](../README.md) · [Review guide](REVIEW_GUIDE.md) · [Demo script](DEMO_SCRIPT.md)
