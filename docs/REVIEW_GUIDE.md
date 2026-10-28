# Review guide

The main document to study before the live review. It grows after each phase and is finished
in Phase 10 (see PROJECT_PLAN.md §17.2 for the final structure).

## 1. The project in 30 seconds
Job seekers waste time on generic applications and can't easily tell whether they fit a role.
Job Application Copilot checks fit requirement by requirement, using evidence from the user's own
documents. It writes tailored CV bullets and cover letters only from claims the user approved, and
it tracks every application on a board. Everything runs locally; only text sent to the AI leaves
the computer.

## 5. Questions and answers

### Problem definition
**Who is the app for?**
Bootcamp graduates, career changers, and junior to mid-level tech people, especially
international applicants in the EU, for whom visa and language requirements matter. The demo
persona, Alex Novak (`data/samples/persona/`), is a logistics coordinator who moved into AI
engineering.

### Agent concepts
**Why is the application flow a workflow graph and not a free agent?**
Applying for a job is high-stakes and must be auditable. The steps always run in the same order
(guard → read posting → requirements → evidence → assess → human review), and the user always
sees the fit before anything is written. The only LLM-driven decision is inside the evidence
loop: search again or not. The open-ended chat (Phase 6) will be a ReAct agent instead.
Code: `app/agent/application_graph.py` → `build_application_graph()`.

**Why is the fit score calculated in Python instead of by the LLM?**
LLM numbers are not reproducible, and you can't explain where "73" came from. The LLM only
classifies each requirement (met / partial / missing). `compute_fit_score()` in
`app/agent/nodes/fit.py` does the arithmetic: must = 3, nice = 1; met = 1, partial = 0.5,
missing = 0; rounded half up; capped at 40 if there's a blocking red flag. It has unit tests
with worked examples.

**What makes your RAG "agentic"?**
The agent judges its own results and acts on them. After the vector search, an LLM grades each
chunk against the requirement and throws away chunks that only look similar. If a requirement
has no relevant chunk, the graph routes to `rewrite_queries`, which tries other words (max 2
times). In the sample "Data Engineer" job you can see two rewrites for Spark and Airflow before
the agent honestly reports them missing.

**How do you stop the agent inventing experience at the analysis stage?**
Three layers. (1) The grader filters out chunks that don't really show the skill. (2) The
assessor must cite evidence ids, and the prompt says "if unsure, choose the lower status".
(3) Python downgrades any "met" or "partial" whose cited ids weren't actually given to it
(`assess_fit`). The claim check and output guard (next question) protect the letter.

**How do you stop the agent inventing experience in the letter?**
Five layers. (1) `plan_claims` proposes claims with evidence ids, and Python marks a claim
supported only if the ids exist. (2) At the claim check the user approves, edits or removes each
claim; unsupported ones start unticked. (3) The writer prompt only receives approved claims
(`claims_block()`), so a removed claim can't leak in. (4) The output guard, a second LLM call,
lists statements no approved claim backs; the draft is rewritten once, then any leftovers are
highlighted. (5) A Python check flags a removed claim's exact wording. See CONCEPTS.md,
"Stopping invented experience".

**What is the output guard, and why not just trust the prompt?**
Prompts are instructions, not guarantees. A model can still add "led a team" or "5 years". The
guard is an "LLM as a judge": a separate call with only the approved claims, the profile basics,
the removed claims and the draft. Its findings drive one rewrite, then go to the user
(`needs_redraft()`, max 2 attempts). In a live run it caught two inflated statements
("…equipped me with the skills to design RAG pipelines") and the rewrite fixed them.

### Human-in-the-loop
**How does the fit review pause work?**
`fit_review` calls `interrupt(payload)`. LangGraph stops, and the `SqliteSaver` checkpointer
saves the state under the thread id (`job-<id>`). `graph.get_state(config).next` is
`("fit_review",)` and the payload holds the fit card. `graph.invoke(Command(resume={"action":
"continue"}), config)` runs the node again, and `interrupt()` returns the answer. Because the
state is in SQLite, this works after a restart. `test_resumes_after_restart_from_sqlite_checkpoint`
proves it.

**What can the user do at the fit review?**
Continue, stop ("not a fit"), or add context ("I used Airflow at work"). Adding context resets
the search and re-runs the evidence loop for every requirement that isn't "met", with the note
as an extra evidence item labelled "Your note". The resume value is validated by
`FitReviewDecision`, so an unknown action is rejected.

**What does the user decide at the claim check?**
For each claim: keep it (tick), reword it (edit the text), or drop it (untick). Unticked or
missing claims count as removed (`claim_check`, "when in doubt, leave it out"), and at least one
must be kept. Kept claims without document evidence, and reworded ones, are saved as confirmed
facts in long-term memory, so the next job treats them as evidence instead of asking again.

**What can the user do at the draft review?**
Edit the bullets and the letter directly, regenerate one section with an instruction ("shorter,
mention the remote setup"), export a DOCX, copy the text, and rate each part 👍/👎. **Accept &
save** finishes the run. The AI version and, if changed, the edited version are saved in the
`Draft` table, and the Notes tab lists every version.

### UI
**How does `interrupt()` work with Streamlit, which reruns the whole script on every click?**
Nothing important lives in Streamlit's memory. The graph's state is saved in SQLite under
`thread_id = job-<id>`. On every rerun, `app/services/applications.py` → `get_workflow_view()`
calls `graph.get_state()` and returns a stage (e.g. `fit_review`), and the Fit tab draws that
step. A button calls `submit_fit_review()`, which resumes with `Command(resume=...)`.

**What happens if the user refreshes the browser during the fit review?**
They see the same fit card and buttons. The open job is in the URL (`?job=3`, see
`app/ui/state.py`), and the paused run is in the checkpointer. `tests/test_ui_pages.py` checks
this with a fresh `AppTest` session on the same URL.

**How does the user see progress during a 20-second analysis?**
`add_job()` uses `graph.stream(..., stream_mode="updates")`. After each node it sends a short
message ("Extracted 8 requirements", "Scored the fit: 84/100") to `st.status`'s `write`, so the
user sees the agent work step by step.

**What are the empty, loading and error states?**
Empty: the board says "No jobs yet…" and points to the sample jobs; the workspace without jobs
points to the board; an empty knowledge base shows a warning. Loading: `st.status` with step
messages. Error: friendly messages in the dialog (input problems), or "⏳ analysis not finished"
with **Retry analysis** (outages). Never a stack trace.

### Code organisation
**How is the code organised?**
In layers: `app/ui/` (Streamlit pages only), `app/agent/` (LLM and graphs), `app/db/` (tables
and the repo), `app/utils/` (errors and logging), and later `tools/`, `rag/`, `memory/`,
`guards/` and `services/`. UI code never talks to the LLM or the database directly. It calls
functions in lower layers. This keeps logic testable without a browser.

**Why is all database access in `app/db/repo.py`?**
There's one place to test and change it. Every function takes a `Session`, so tests pass an
in-memory database (`tests/conftest.py`) and never touch real data. It also gives the copilot's
tracker tool (Phase 6) a safe, validated API: for example, `update_job()` rejects unknown fields
and invalid statuses.

### Memory
**What is the difference between the checkpointer and the Store?**
The checkpointer saves the state of one graph run (one `thread_id`), like a save game, so a
job's workflow can pause for the user and continue later. The Store is long-term memory about
the user across all runs: profile, preferences, confirmed facts and style rules. Ours is a
`SqliteStore` in `app/memory/store.py`; the checkpointer comes in Phase 3.

**How is long-term memory organised?**
In namespaces: tuples like `("local", "profile")`, with the user id first. The profile and
preferences each have one item (key `"main"`). Facts and style rules have one item per entry
(random key), so each can be deleted on its own. "Delete all my data" removes everything under
`("local",)`.

### RAG
**How do documents get into the knowledge base?**
`app/rag/ingest.py` → `ingest_file()`: extract the text (pypdf, python-docx or plain text),
split it into ~500-character chunks with 80 characters of overlap, embed each chunk with
`openai/text-embedding-3-small` through OpenRouter, and store it in Chroma with metadata
(`doc_name`, `doc_type`, `chunk_id`). Re-uploading a file deletes its old chunks first.

**Why RAG instead of putting all the documents in the prompt?**
Three reasons. Cost: each call sends only a few relevant paragraphs, not every document.
Scale: past letters and project READMEs pile up over time. Citations: each chunk has a file name
and id, so we can show the user exactly which sentence proves a requirement.

**How do you know retrieval works?**
`tests/test_rag.py` ingests the demo persona and checks that "RAG chatbot" finds
`project_documind_readme.md`. Offline, it uses fake keyword embeddings. The live test uses the
real model and checks that the README is the top hit. Phase 9 adds a proper evaluation.

### Security
**How are secrets handled?**
They're read from `.env` by `app/config.py` → `Settings` and stored as `SecretStr`, which hides
the value in logs. `.env` is in `.gitignore`, and only `.env.example` with empty values is
committed.

**How is SQL injection prevented?**
All queries use the SQLModel ORM (`app/db/repo.py`), which sends values as bound parameters.
No query is built by joining strings.

**What happens if a job posting contains "ignore all previous instructions"?**
It's analysed normally. The posting is wrapped in `<job_posting>` tags, and every prompt says
text inside tags is data, never instructions (`prompts.wrap`, which also neutralises fake closing
tags). `input_guard` spots the phrase and shows a warning. Even a fooled LLM can't set the score,
because Python computes it. `test_injection_text_is_flagged_but_analysis_continues` checks this.

**What stops someone using the URL fetcher to reach internal systems (SSRF)?**
`app/guards/url_guard.py` → `validate_url()`: only http and https, and every IP the host
resolves to must be public (no 127.0.0.1, 10.x, 192.168.x, 169.254.169.254, ::1). The fetcher
doesn't follow redirects automatically. It re-checks each redirect target, and stops after
10 seconds or 2 MB. Tests cover all these addresses, plus a hostname that resolves to a private IP.

**Can a tampered checkpoint file run code?**
The checkpointer's serializer has an allowlist (`CHECKPOINT_TYPES`): it only rebuilds our own
state models from disk, nothing else.

**What uploads are allowed?**
Only PDF, DOCX, TXT and MD, up to 5 MB each and 10 files at once. Files without readable text
(for example scanned images) are rejected with a clear message. One bad file doesn't stop the
others (`app/services/onboarding.py` → `add_documents()`).

### Error handling
**What if the AI service is busy or returns broken output?**
`invoke_structured()` in `app/agent/llm.py` retries timeouts, rate limits and 5xx errors 3 times
with growing waits (tenacity), then raises `LLMServiceError` ("The AI service is busy, please
retry"). If the answer doesn't match the Pydantic schema, the model gets one more try with the
error message. After that, `StructuredOutputError`. A rejected API key becomes a `ConfigError`
with a hint.

**What if the job link can't be read (e.g. LinkedIn)?**
`ingest_job` catches the `FetchError` and ends the run with "I couldn't read that page. Please
paste the job text instead." The same happens for blocked URLs. An empty knowledge base stops
the run before any LLM call, with a pointer to onboarding.

**What if the model's answer is too long?**
The answer is cut off at `max_tokens` and can't be parsed (`openai.LengthFinishReasonError`).
`invoke_structured()` turns that into a friendly `StructuredOutputError` without a useless retry.
A live test found this with 15 requirements, so grading now runs in batches of at most 20
(requirement, chunk) pairs (`GRADE_BATCH_PAIRS`, D36).

**What if the DOCX export fails?**
`app/services/export.py` → `export_drafts()` catches the error, logs it, and returns a `.txt`
file instead, so the user still gets their text (§10).

**What does the user see when something fails?**
Never a stack trace. Code raises an `AppError` subclass (`app/utils/errors.py`), and the UI
shows its `user_message`, such as "The AI service is busy, please retry". The full details go to
`data/runtime/app.log` through `log_exception()`, together with the job or thread id.

**What if the OpenRouter key is missing?**
The app still opens, with a banner explaining how to add the key (`app/main.py`). Any AI call
goes through `get_llm()`, which raises `ConfigError` with a clear fix.

## 7. Cheat sheet (so far)
| Fact | Value |
|---|---|
| Python | 3.14 (supports ≥ 3.11) |
| LLM provider | OpenRouter (OpenAI-compatible API) |
| Default model | `openai/gpt-4o-mini` (set by `DEFAULT_MODEL`) |
| App database | SQLite, `data/runtime/app.db`, 6 tables |
| Tests | 131 offline + 4 live (`uv run pytest`, `uv run pytest -m live`) |
| Pages working | Get started, Board, Job workspace (Fit, Claim check, Documents, Notes tabs) |
| Workflow stages shown in UI | not_started, fit_review, claim_check, draft_review, interrupted, failed, finished |
| Graph nodes | 14: input_guard, ingest_job, extract_requirements, retrieve_evidence, rewrite_queries, assess_fit, fit_review, plan_claims, claim_check, remember_confirmed_facts, draft_materials, output_guard, draft_review, save_to_tracker |
| Human-in-the-loop pauses | 3: fit_review, claim_check, draft_review |
| Claims and drafts | max 10 claims; letter ≤ 280 words (medium); 3–6 bullets; max 2 draft attempts per round |
| Agentic RAG | top 4 chunks, grade ≥ 0.4 kept, max 2 rewrites, max 4 evidence per requirement, grading in batches of 20 pairs |
| Fit score | must 3 / nice 1; met 1 / partial 0.5 / missing 0; half-up; cap 40 if blocking |
| Sample scores (one run) | good 84, partial 43, poor 0 (German C1 blocking) |
| LLM calls per analysis | 5–8, about $0.003 with `openai/gpt-4o-mini` |
| Prompt version | 2026-10-28.4 (9 prompts) |
| LLM calls, continue → drafts | 3–5 (claims, draft, guard, maybe a rewrite + guard) |
| Chunking | 500 characters, 80 overlap, `RecursiveCharacterTextSplitter` |
| Embeddings | `openai/text-embedding-3-small` via OpenRouter (1,536 dimensions) |
| Vector store | Chroma, cosine distance, `data/runtime/chroma/` |
| Long-term memory | LangGraph `SqliteStore`, `data/runtime/memory.db` |
| Demo knowledge base | 4 documents, 21 chunks |
| Sample jobs | 3: good, partial and poor fit (German C1) |
