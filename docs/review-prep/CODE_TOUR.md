# Code tour

Two real requests, followed file by file. For each step: the file, the function, what
happens, and what the state looks like afterwards (a small, shortened JSON example). The
diagrams for both flows are in `ARCHITECTURE.md`.

- [Request 1: "User pastes a job posting"](#request-1-user-pastes-a-job-posting)
- [Request 2: "Research the company for my newest job and move it to Applied"](#request-2-research-the-company-for-my-newest-job-and-move-it-to-applied)

---

## Request 1: "User pastes a job posting"

The user is Alex Novak (demo persona). They paste the "Junior AI Engineer at Lumen Health"
posting (`data/samples/jobs/01_good_fit_junior_ai_engineer.md`) into the Add-job dialog.

### Step 1. The dialog: `app/ui/pages/1_board.py` → `add_job_dialog()`
The page only draws widgets. On **Analyse fit** it opens the shared progress box
(`run_with_progress` in `app/ui/components.py`) and hands the text to the service, with the box
as the progress callback:

```python
result = run_with_progress(
    "Analysing fit",
    lambda progress: applications.add_job(text, on_progress=progress),
    steps=applications.FIT_ANALYSIS_STEPS,
    done="Fit analysis done",
)
```

### Step 2. The service: `app/services/applications.py` → `add_job()`
1. `_require_llm_key()`: no OpenRouter key → a friendly message, no work done.
2. Creates a placeholder `Job` row ("New job / Analysis not finished") through `repo.create_job()`.
   The row gets `thread_id = "job-<id>"`; that id names this job's saved workflow run.
3. Calls `_stream()`, which wraps the run in `track_run("analyse_fit", job_id=…)` (cost tracking
   + LangSmith metadata) and runs `graph.stream(payload, config, stream_mode=["tasks",
   "updates"])`. When a node starts, `_running_message()` puts "Searching your documents…" in the
   box's header; when it ends, `_progress_message()` turns the update into a line like
   "Extracted 8 requirements".

Input to the graph:
```json
{"raw_input": "Junior AI Engineer – Lumen Health …", "job_id": 3}
```
Config: `{"configurable": {"thread_id": "job-3"}, "recursion_limit": 25}` (`thread_config()`).

### Step 3. `input_guard`: `app/agent/nodes/ingest.py` → `input_guard()`
Free Python checks, no LLM: is the user set up (a profile and at least one document, via
`setup_status()` in `app/services/readiness.py`, D90)? Is the text 200–30,000 characters? Does
it contain phrases like "ignore previous instructions"? (Those are **flagged**, not blocked: the
posting is data, D20.)
```json
{"warnings": []}
```
Without a profile or documents: `{"errors": ["Before I can analyse a job, …"]}` (or the
message for whichever is missing), and the edge
rule `_stop_on_errors` sends the run to END.

### Step 4. `ingest_job`: `app/agent/nodes/ingest.py` → `ingest_job()`
If the input is a URL, `deps.fetch_url()` downloads it through the URL guard
(`app/guards/url_guard.py` → `validate_url()`, SSRF protection) and trafilatura. Then one
structured LLM call: `invoke_structured(llm, JobPostingExtraction, job_extraction_messages(text))`.
The posting is wrapped in `<job_posting>` tags inside the prompt.
```json
{"job": {"title": "Junior AI Engineer", "company": "Lumen Health", "remote_policy": "hybrid",
         "seniority": "junior", "language_requirements": [{"language": "English", "level": "C1"}],
         "visa_sponsorship": "unknown", "summary": "…", "raw_text": "…", "source_url": null}}
```

### Step 5. `extract_requirements`: `app/agent/nodes/requirements.py`
One LLM call with few-shot examples (`prompts.py`) returns a `RequirementList`.
```json
{"requirements": [
   {"id": "R1", "text": "Python for production code", "category": "skill", "importance": "must"},
   {"id": "R2", "text": "Built an LLM or RAG application", "category": "experience", "importance": "must"},
   {"id": "R3", "text": "Docker", "category": "skill", "importance": "nice"}],
 "search_queries": {}, "evidence": {}, "rag_iterations": 0}
```

### Step 6. `retrieve_evidence` (agentic RAG): `app/agent/nodes/evidence.py`
1. One LLM call plans a search query per requirement (`SearchQueries`).
2. `deps.search(query, 4)` → Chroma, top 4 chunks each (`app/rag/retriever.py`).
3. One LLM call (in batches of ≤ 20 pairs) **grades** every (requirement, chunk) pair; chunks
   below 0.4 are dropped.
```json
{"evidence": {"R1": [{"chunk_id": "alex_novak_cv.md#2", "doc_name": "alex_novak_cv.md",
                      "snippet": "Python (4 years)…", "relevance": 0.9}],
              "R2": [{"chunk_id": "project_documind_readme.md#0", "…": "…"}],
              "R3": []},
 "search_queries": {"R1": "Python production", "R2": "RAG chatbot LLM", "R3": "Docker containers"}}
```
The edge rule `_after_retrieval` → `needs_rewrite()`: R3 found nothing and `rag_iterations < 2`,
so the graph goes to **`rewrite_queries`**, which asks the LLM for new words ("containerisation,
docker compose") and loops back. This is the one decision in the workflow that the agent makes.

### Step 7. `assess_fit`: `app/agent/nodes/fit.py` → `assess_fit()`
1. One LLM call classifies each requirement as met / partial / missing, citing evidence ids.
2. Python downgrades any "met"/"partial" whose cited ids weren't really given (D16).
3. `find_red_flags()` compares the posting with the profile (language level, visa, seniority,
   location, deal-breakers). Pure Python.
4. `compute_fit_score()` does the arithmetic: must = 3, nice = 1; met 1, partial 0.5, missing 0;
   capped at 40 if a red flag is blocking.
```json
{"fit": {"score": 88, "red_flags": [],
         "matches": [{"requirement_id": "R1", "status": "met", "evidence": ["…"],
                      "explanation": "Four years of Python in the CV.", "honest_positioning": null}],
         "strengths_summary": "…", "questions_to_ask_employer": ["…"]}}
```

### Step 8. The pause: `app/agent/nodes/review.py` → `fit_review()`
```python
answer = interrupt({"type": "fit_review", "fit": state["fit"].model_dump(mode="json"), ...})
```
The graph stops. The checkpointer has already saved the state in `checkpoints.db` under
`job-3`. Back in `add_job()`, `get_workflow_view()` checks for errors, `_sync_job()` copies the
title, company and score onto the board card, and the dialog sends the user to the workspace.

### Step 9. The page draws the pause: `app/ui/pages/2_job_workspace.py`
On every rerun (and after a browser refresh) the page calls
`applications.get_workflow_view(job_id)`. That reads `graph.get_state(config)`, finds the
interrupt payload's `type`, and returns:
```json
{"job_id": 3, "stage": "fit_review", "fit": {"score": 88, "…": "…"}, "warnings": []}
```
The Fit tab draws the score, red flags, the requirement table with evidence expanders, and the
three buttons. The open job is in the URL (`?job=3`), so a refresh lands on the same card.

### Step 10. Resume: `applications.submit_fit_review(3, "continue")`
`_resume()` checks the job is still waiting at `fit_review`, then runs
`graph.stream(Command(resume={"action": "continue", "text": null}), config)`. `fit_review()` runs
again from the top; this time `interrupt()` **returns** the answer instead of pausing.
`FitReviewDecision` validates it, and `_after_fit_review` routes to `plan_claims`.

### Step 11. `plan_claims` → `claim_check` (pause 2): `app/agent/nodes/claims.py`
`plan_claims` asks the LLM for up to 10 claims, each citing evidence ids. Python marks a claim
`supported` only if a cited id is real (D29).
```json
{"claims": [
  {"id": "C1", "text": "Built DocuMind, a RAG chatbot with LangChain and Chroma",
   "evidence_chunk_ids": ["project_documind_readme.md#0"], "supported": true, "user_decision": "pending"},
  {"id": "C4", "text": "Led a team of three engineers",
   "evidence_chunk_ids": [], "supported": false, "user_decision": "pending"}]}
```
`claim_check()` pauses with `{"type": "claim_check", ...}`. The Claim check tab shows each claim;
unsupported ones start unticked. The user unticks C4 and submits;
`submit_claim_review()` resumes with:
```json
{"decisions": [{"id": "C1", "action": "approve"}, {"id": "C4", "action": "remove"}]}
```
After the resume, C4 has `"user_decision": "removed"`.

### Step 12. `remember_confirmed_facts`
Kept claims with no document evidence (or reworded ones) are saved to the Store under
`("local", "facts")`, so next time they count as evidence. Here nothing new: `{"new_facts": []}`.

### Step 13. `draft_materials` ⇄ `output_guard`: `app/agent/nodes/drafting.py`
`draft_materials` builds the prompt with `claims_block(claims)`, which lists **only kept
claims**, so C4 is not even in the prompt. It adds the tone, letter length, language and the
learned style rules from the Store. Output (`Drafts`):
```json
{"drafts": {"cv_bullets": ["Built DocuMind, a RAG chatbot…"], "cover_letter": "Dear Lumen Health team, …",
            "used_claim_ids": ["C1", "C2", "C3"]}, "draft_attempts": 1}
```
`output_guard` is a second LLM call (an "LLM as a judge") plus two Python checks: the exact
wording of removed claims, and banned phrases from style rules. Any issue →
`_after_guard` sends it back for **one** rewrite; after that the issues are shown.
```json
{"guard_issues": []}
```
The AI version is saved to the `Draft` table (version 1).

### Step 14. `draft_review` (pause 3) and acceptance
The Documents tab shows editable bullets and letter. The user deletes "passionate" and
clicks **Accept & save**. `submit_draft_review()` resumes with
`{"action": "accept", "edited": {…}}`. Then:
- `learn_from_edits` (`app/agent/nodes/learning.py`) computes a sentence diff in Python and
  asks the LLM for 0–3 general style rules: `"Avoid the word 'passionate'"`,
  `avoid_phrases=["passionate"]`. Saved under `("local", "style_rules")`; the page shows a toast
  with **Undo**.
- `save_to_tracker` (`app/agent/nodes/review.py`) writes the posting and score to the `Job` row
  and saves the edited drafts as version 2.
- END. `get_workflow_view()` now returns `stage = "finished"`.

**Files touched in this request:** `1_board.py`, `applications.py`, `application_graph.py`,
`nodes/ingest.py`, `guards/input_guard.py`, `nodes/requirements.py`, `nodes/evidence.py`,
`rag/retriever.py`, `nodes/fit.py`, `nodes/review.py`, `2_job_workspace.py`, `nodes/claims.py`,
`memory/store.py`, `nodes/drafting.py`, `nodes/learning.py`, `db/repo.py`, `prompts.py`, `llm.py`.

---

## Request 2: "Research the company for my newest job and move it to Applied"

Typed in **💬 Copilot chat**, with the tools Company research and Tracker switched on.

### Step 1. The page: `app/ui/pages/3_copilot_chat.py`
`thread_id = current_chat_id(...)` (e.g. `chat-5f1c…`, also in the URL). `toggles =
enabled_toggles()` reads the sidebar switches. On send:
```python
_stream_answer(copilot.send_message(thread_id, text, toggles))
```

### Step 2. The service: `app/services/copilot.py` → `send_message()`
Builds the input and streams the graph with **two** stream modes: `"messages"` (the answer
token by token) and `"updates"` (tool calls, tool results, the approval pause). Everything runs
inside `track_run("chat", thread_id=…)`.
```json
{"messages": [{"type": "human", "content": "Research the company for my newest job and move it to Applied"}],
 "enabled_tools": ["research_company", "get_applications", "update_tracker"]}
```
`enabled_tool_names()` (`app/tools/registry.py`) turns the toggles into tool names and drops any
tool whose API key is missing.

### Step 3. `input_guard`: `app/agent/copilot_graph.py` → `input_guard()`
Layer 1, Python: `check_chat_message()` (empty, > 4,000 characters, "ignore all previous
instructions" → refused). Layer 2, a cheap LLM `TopicCheck`: is it about the job search? Yes.
Returns `{}`, and `_after_guard` goes to `agent`.

### Step 4. `agent`, round 1 (reason): `copilot_graph.py` → `agent()`
The system prompt (`copilot_system_message()`) has today's date, the tone, the enabled tool
names and the profile. `deps.llm.bind_tools(tools)` sends the three tool schemas. The model
doesn't know which job is newest, so it answers with a tool call:
```json
{"type": "ai", "content": "", "tool_calls": [{"name": "get_applications", "args": {}, "id": "call_1"}]}
```
`_after_agent`: tool calls, none needs approval → `tools`.

### Step 5. `tools` (act): `copilot_graph.py` → `run_tools()`
`ToolNode` runs `get_applications` (`app/tools/tracker.py` → `list_applications()`) and adds:
```json
{"type": "tool", "tool_call_id": "call_1", "name": "get_applications",
 "content": "- id 3: Junior AI Engineer at Lumen Health | saved | fit 88 | added 2026-10-29"}
```
Back to `agent` (the `tools → agent` edge: observe, then reason again).

### Step 6. `agent`, round 2 → `research_company`
```json
{"tool_calls": [{"name": "research_company", "args": {"company": "Lumen Health"}, "id": "call_2"}]}
```
`ToolNode` runs it (`app/tools/company.py`): Tavily search, results wrapped in
`<untrusted_web_content>` tags, so text from the web is treated as data. If Tavily is down, the
tool error becomes a `ToolMessage` like "Tool error: …" (`_tool_error()`), and the agent tells
the user instead of crashing.

### Step 7. `agent`, round 3 → a write tool → `approve` (pause)
```json
{"tool_calls": [{"name": "update_tracker", "args": {"job_id": 3, "status": "applied"}, "id": "call_3"}]}
```
`update_tracker` is in `APPROVAL_TOOLS`, so `_after_agent` routes to **`approve`**, which calls:
```python
interrupt({"type": "tool_approval", "actions": [{"tool": "update_tracker",
           "summary": "Job #3 (Junior AI Engineer at Lumen Health): move to Applied", ...}]})
```
The summary comes from `describe_update()` in `app/tools/tracker.py`. The stream yields an
`"approval"` event; the page reruns, `copilot.pending_approval()` finds the paused action, and
`_approval_card()` shows **Approve** / **Decline**. The chat input is blocked meanwhile.

### Step 8. The user approves: `copilot.answer_approval(thread_id, approved=True)`
Resumes with `Command(resume={"approved": True})`. `approve()` validates it
(`ToolApprovalDecision`) and returns `Command(goto="tools")`. `ToolNode` runs
`update_tracker` → `apply_update()` → `repo.update_job(session, 3, status="applied")`.
```json
{"type": "tool", "name": "update_tracker", "content": "Done. Job #3 (Junior AI Engineer at Lumen Health): move to Applied"}
```
If the user had declined, every call of that step would get "The user declined this action" and
the job would stay unchanged (`test_declined_update_changes_nothing`).

### Step 9. `agent`, round 4 → final answer → END
No more tool calls, so `_after_agent` returns END. The answer streams token by token: a short
company summary with sources, and "I moved it to Applied." The page reruns and redraws the whole
chat from the checkpointer with `copilot.get_history()`: each tool call becomes a "🔧 Used: …"
expander. On the board, the card is now in the **Applied** column.

**Message sequence in the checkpoint (thread `chat-5f1c…`):** Human → AI(tool_call
get_applications) → Tool → AI(tool_call research_company) → Tool → AI(tool_call update_tracker)
→ *pause* → Tool → AI(answer). 4 LLM calls for the agent + 1 for the topic check; well within
`recursion_limit = 12`.

**Files touched in this request:** `3_copilot_chat.py`, `ui/state.py`, `services/copilot.py`,
`tools/registry.py`, `copilot_graph.py`, `guards/input_guard.py`, `prompts.py`, `llm.py`,
`tools/tracker.py`, `tools/company.py`, `db/repo.py`, `services/observability.py`.
