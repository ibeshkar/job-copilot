# Phase 10: Documentation and review prep

## What I built (plain English, 3–5 sentences)
The study material for the live review, and a README that a stranger can follow. Four new
documents: `ARCHITECTURE.md` (the system in diagrams), `CODE_TOUR.md` (two real requests followed
file by file), `DEMO_SCRIPT.md` (what to click and say in 6–7 minutes) and `SECURITY.md` (threats,
code, tests and known gaps). `REVIEW_GUIDE.md` is now complete: the 3-minute journey, the
criteria map, the top 8 decisions, 87 questions with answers, an honest weak-spot list and a
cheat sheet. A separate Claude agent critiqued usability, security and prompts; its findings and
my responses are in `DECISIONS.md` D77, and the one high-severity finding is fixed.

## Why it matters for the user
- The README now explains the problem, the features, a 3-command quick start, the four use cases
  and what each application costs (~$0.006).
- The user guide has 8 screenshots, so a new user knows what each step looks like.
- **Security fix:** Streamlit listened on every network interface, and the app has no login, so
  anyone on the same Wi-Fi could open the user's CV data. It now listens on `localhost` only.

## Concepts used
- [What is an AI agent? Agent vs. chain vs. workflow](../CONCEPTS.md#what-is-an-ai-agent-agent-vs-chain-vs-workflow) (new)
- [Agent types: ReAct, plan-and-execute, workflow, multi-agent](../CONCEPTS.md#agent-types-react-plan-and-execute-workflow-multi-agent) (new)
- Threat modelling and the "lethal trifecta" (`SECURITY.md`, glossary)

## Files created or changed
| File | Purpose |
|---|---|
| `README.md` | Rewritten to the §13 outline: problem, features, quick start, use cases, architecture, tools, memory, security, evaluation, decisions, limitations, cost per application |
| `docs/ARCHITECTURE.md` | New: layers, where data lives, the three graphs (≤ 10 boxes each), the interrupt sequence, tools, cross-cutting pieces |
| `docs/CODE_TOUR.md` | New: "user pastes a job posting" (14 steps) and "research the company and move it to Applied" (9 steps), with the state after each step |
| `docs/DEMO_SCRIPT.md` | New: the 8-step demo with clicks, talking points, a pre-review checklist and backup plans |
| `docs/SECURITY.md` | New: threat table with code and tests, privacy, defence in depth, known gaps |
| `docs/REVIEW_GUIDE.md` | Finished: §2, §3, §4, §6 added; new Q&A groups (problem, agent types, reflection); stale lines fixed; cheat sheet final |
| `docs/CONCEPTS.md` | Two new sections at the top: what an agent is, and agent types |
| `docs/GLOSSARY.md` | 10 new terms (chain, plan-and-execute, multi-agent, lethal trifecta, DNS rebinding, …) |
| `docs/DECISIONS.md` | D75–D78, including the external critique with 19 findings and responses (D77) |
| `docs/USER_GUIDE.md` | 8 screenshots |
| `docs/assets/*.png` | New: screenshots of onboarding, board, fit (good and poor), claim check, documents, chat approval, memory |
| `.streamlit/config.toml` | `server.address = "localhost"` (critique S1) |

## How it works (small diagram or numbered steps)
How the screenshots were made, without touching the user's own data:
1. A throwaway script (not committed) set `RUNTIME_DIR` to a temporary folder, loaded the demo
   persona, and called the real services: `add_job` ×3, `submit_fit_review`, `submit_claim_review`,
   and `copilot.send_message("Move my newest job to Applied")`. Cost: about 2 cents.
2. Streamlit ran on that folder; headless Chrome was driven over the DevTools protocol to open
   each page, click the right tab and save a full-page screenshot.
3. The usage log of that run gave the cost-per-application table in the README.

## How to try it (exact commands / clicks)
```bash
uv run ruff check . && uv run pytest     # nothing in the code changed except the config line
uv run streamlit run app/main.py         # now on http://localhost:8501 only
```
- Read `docs/REVIEW_GUIDE.md` top to bottom; practise §5 out loud.
- Follow `docs/DEMO_SCRIPT.md` once end to end, with a timer.
- Open any `file.py → function()` reference in `CODE_TOUR.md` and check it exists.

## Decisions and alternatives
- **Split the workflow diagram in two** (D75): the 15-node graph doesn't fit the 10-box rule.
- **Reference functions, not line numbers** (D76): line numbers go stale.
- **Fix only S1 now** (D77): the other critique findings change behaviour and need tests;
  they're listed as planned work instead of being rushed into a documentation phase.
- **Screenshots from a throwaway runtime** (D78): the user's data is never shown.
- **Demo GIF/video:** not recorded (optional). It needs a screen recorder and a real run in a
  browser; the 8 screenshots in `docs/assets/` are the backup plan in the demo script.

## Problems I hit and how I solved them
- **Streamlit tabs are `div`s, not buttons.** The first screenshot script clicked nothing and
  every workspace shot showed the auto-selected tab. Fixed by selecting `[role=tab]`.
- **A chat id in the URL was ignored.** `current_chat_id()` only accepts `chat-` + 12 hex
  characters; my test id didn't match. Using `copilot.new_thread_id()` fixed it (and confirmed
  the URL check works).
- **A real weakness found while taking screenshots:** the poor-fit job marked "6+ years of
  experience in machine learning" as ✅ met, citing a CV chunk about a 2025–26 course. Python
  checks that a cited id exists, not that the snippet proves the requirement. The score stayed
  18 (German C1 cap), but the card was wrong. Written up as weak spot #1 in REVIEW_GUIDE §6.
- **The generated summary in `EVALUATION.md` only shows extraction.** The last eval run before
  the Phase 9 commit used `--only extraction`, and the report replaces the whole generated block.
  The full-run numbers are still in EVALUATION.md §7 and `evals/results/latest.json`, so the
  README cites those. A full re-run (`uv run python evals/run_evals.py`) fixes the block.

## Possible review questions (with short answers)
**Which agent types did you use, and which not?** ReAct for chat, a workflow for applications, a
loop for the interview. Not plan-and-execute (our steps are known) and not multi-agent (5 tools
fit one agent). `CONCEPTS.md` "Agent types".

**What did the AI critique find?** 19 findings; the worst was that the app listened on the whole
network (fixed). The most interesting was a read-only tool used as an outbound channel by a
prompt injection, which my threat table had missed. D77.

**What is the biggest weakness you know of?** The assessor can cite a real chunk that doesn't
prove the requirement; a second "does this prove it?" check would fix it. REVIEW_GUIDE §6.

**How would you deploy it for many users?** Login, Postgres checkpointer and Store (namespaces
already start with the user id), per-user Chroma collections, a background queue.
