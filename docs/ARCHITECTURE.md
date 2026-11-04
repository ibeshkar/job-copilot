# Architecture

How the app is built, in diagrams. Each diagram has 10 boxes or fewer. For the "why" behind
each choice, see [`DECISIONS.md`](DECISIONS.md); for the concepts, see
[`review-prep/CONCEPTS.md`](review-prep/CONCEPTS.md); to follow one request through the code, see
[`review-prep/CODE_TOUR.md`](review-prep/CODE_TOUR.md).

---

## 1. The layers

**One sentence:** the code is split into layers, and each layer only calls the layers below it.

**Analogy:** a restaurant. The pages are the waiters (they talk to the guest), the services are
the head chef (they decide what happens), the graphs and tools are the cooks, and the database,
Chroma and the Store are the pantry. A waiter never walks into the pantry.

```mermaid
flowchart TD
    UI["app/ui/ + app/main.py<br/>Streamlit pages, sidebar, components"]
    SVC["app/services/<br/>applications, copilot, interview, onboarding, my_data, usage, …"]
    AG["app/agent/<br/>3 graphs, nodes, prompts, llm factory"]
    TL["app/tools/<br/>5 copilot tools + registry"]
    GD["app/guards/<br/>input, URL (SSRF), output"]
    RAG["app/rag/<br/>ingest + retriever → Chroma"]
    MEM["app/memory/store.py<br/>long-term memory (SqliteStore)"]
    DB["app/db/<br/>SQLModel tables + repo"]
    UI --> SVC --> AG
    AG --> TL
    AG --> GD
    AG --> RAG
    AG --> MEM
    SVC --> DB
```

| Layer | Folder | Rule |
|---|---|---|
| UI | `app/ui/`, `app/main.py` | Draws widgets and calls services. Never calls the LLM or the database. |
| Services | `app/services/` | One function per user action (`add_job`, `submit_fit_review`, `send_message`). Wraps runs in cost tracking and turns errors into friendly messages. |
| Agent | `app/agent/` | The three LangGraph graphs and their nodes. All prompts in `prompts.py`; every LLM call through `llm.py`. |
| Tools | `app/tools/` | The copilot's tools. `registry.py` lists them for the toggles. |
| Guards | `app/guards/` | Input checks, URL checks, unsupported-claim checks. |
| Data | `app/rag/`, `app/memory/`, `app/db/` | Chroma (documents), the Store (facts about the user), SQLite (jobs, drafts, usage). |

Nodes get their services (LLM, retriever, Store, database) through `GraphDeps`
(`app/agent/deps.py`), so tests can swap in fakes without a network (D14).

---

## 2. Where data lives

Everything is on the user's computer, in `data/runtime/` (gitignored).

```mermaid
flowchart LR
    APP[The app]
    A[("app.db<br/>Job, Draft, Document,<br/>UsageLog, Feedback, InterviewSession")]
    C[("checkpoints.db<br/>graph state per thread_id<br/>(short-term memory)")]
    M[("memory.db<br/>profile, preferences, facts,<br/>style rules (long-term memory)")]
    V[("chroma/<br/>document chunks + embeddings")]
    O[OpenRouter / Tavily / Arbeitnow<br/>only while an AI step runs]
    APP --> A
    APP --> C
    APP --> M
    APP --> V
    APP -.text sent.-> O
```

| Store | File | Example content |
|---|---|---|
| App database | `app.db` | Job #3 "Junior AI Engineer", status `applied`, fit 88 |
| Checkpointer | `checkpoints.db` | thread `job-3` is paused at `claim_check` with 8 claims |
| Long-term memory | `memory.db` | `("local", "style_rules")` → "Avoid the word 'passionate'" |
| Vector store | `chroma/` | chunk `alex_novak_cv.md#3`: "Built DocuMind, a RAG chatbot…" |
| Developer overrides | `dev_settings.json` | `{"default_model": "openai/gpt-4.1-mini", "temperature": 0.2, "max_tokens": 2000}` |
| Log | `app.log` | errors with the job or thread id, never shown to the user |

---

## 3. The three graphs

The app uses three different agent shapes on purpose. Each fits its job.

| | Application workflow | Copilot chat | Mock interview |
|---|---|---|---|
| File | `app/agent/application_graph.py` | `app/agent/copilot_graph.py` | `app/agent/interview_graph.py` |
| Shape | Workflow (fixed order) | ReAct agent (LLM picks tools) | Loop (5 rounds) |
| Who picks the next step | Our edges | The LLM's tool calls | Our edges + a counter |
| Pauses (`interrupt()`) | fit_review, claim_check, draft_review | tool_approval | one per question |
| Thread id | `job-<id>` | `chat-<uuid>` | `interview-<job id>` |
| Why this shape | High stakes; must be auditable; nothing may skip the claim check | Open questions; any mix of tools | The same round repeats |

All three are compiled with the same SQLite checkpointer, so every pause survives a browser
refresh and an app restart.

### 3.1 Application workflow graph (the core)

15 nodes in the code. To keep the diagram readable, the analysis half and the writing half are
drawn separately.

**Analysis half** (from "Add job" to the fit card):

```mermaid
flowchart TD
    S[START] --> G[input_guard<br/>length, injection flag, empty KB]
    G -->|error| X[END with message]
    G --> I[ingest_job<br/>URL or text → JobPosting]
    I --> R[extract_requirements<br/>few-shot]
    R --> E[retrieve_evidence<br/>search + grade]
    E -->|nothing relevant,<br/>max 2 times| W[rewrite_queries]
    W --> E
    E --> F[assess_fit<br/>LLM classifies, Python scores]
    F --> H1{{fit_review<br/>pause}}
```

`ingest_job` and `extract_requirements` also stop at END with a friendly message if something is
wrong (unreadable link, not a job posting). That is the `_stop_on_errors` edge rule.

**Writing half** (from the user's "Continue" to the saved application):

```mermaid
flowchart TD
    H1{{fit_review}} -->|add context| E[retrieve_evidence]
    H1 -->|stop| S[save_to_tracker]
    H1 -->|continue| C[plan_claims]
    C --> H2{{claim_check<br/>pause}}
    H2 --> M[remember_confirmed_facts]
    M --> D[draft_materials<br/>approved claims only]
    D --> OG[output_guard<br/>LLM judge + Python checks]
    OG -->|issues, 1 rewrite| D
    OG --> H3{{draft_review<br/>pause}}
    H3 -->|regenerate a section| D
    H3 -->|accept| L[learn_from_edits] --> S --> Z[END]
```

The routing functions are small and named after the decision: `_after_retrieval`,
`_after_fit_review`, `_after_guard`, `_after_draft_review` in `application_graph.py`.

### 3.2 Copilot chat agent (ReAct)

```mermaid
flowchart LR
    S[START] --> G[input_guard<br/>rules + topic classifier]
    G -->|refused| E[END]
    G --> A[agent<br/>LLM + enabled tools]
    A -->|read tools| T[tools<br/>ToolNode]
    T --> A
    A -->|write tool| H{{approve<br/>pause}}
    H -->|yes| T
    H -->|no: 'declined'| A
    A -->|no tool calls| E
```

- Tools are bound on every call from the sidebar toggles (`llm.bind_tools(enabled)`).
- `recursion_limit = 12` steps per message (`chat_config()`).
- The system prompt carries today's date, the tone, the enabled tool names and the user profile.

### 3.3 Mock interview loop

```mermaid
flowchart LR
    S[START] --> Q[generate_question<br/>kind from KIND_PLAN]
    Q --> K{{ask<br/>pause for answer}}
    K -->|answer| V[evaluate_answer<br/>STAR, score 1–5, better answer]
    K -->|stop| Y[summarise]
    V -->|fewer than 5| Q
    V -->|5 done| Y --> E[END]
```

---

## 4. The Streamlit + `interrupt()` pattern

Streamlit reruns the whole page script on every click, so the page can't hold the workflow in
memory. The checkpointer holds it instead.

```mermaid
sequenceDiagram
    participant U as User
    participant P as Page (2_job_workspace.py)
    participant S as Service (applications.py)
    participant G as Graph + checkpointer
    U->>P: opens ?job=3 (or refreshes)
    P->>S: get_workflow_view(3)
    S->>G: get_state(thread "job-3")
    G-->>S: paused at fit_review, payload = fit card
    S-->>P: WorkflowView(stage="fit_review", fit=…)
    P-->>U: draws the fit card + buttons
    U->>P: clicks "Continue to application"
    P->>S: submit_fit_review(3, "continue")
    S->>G: stream(Command(resume={"action": "continue"}))
    G-->>S: runs plan_claims, pauses at claim_check
    P-->>U: rerun → draws the claim check
```

---

## 5. The tools

| # | Tool (LLM name) | Type | Needs | File |
|---|---|---|---|---|
| 1 | `fetch_job_posting` | read | — | `app/tools/job_posting.py` |
| 2 | `search_my_experience` | read | — | `app/tools/experience.py` |
| 3 | `research_company` | read, external | `TAVILY_API_KEY` | `app/tools/company.py` |
| 4 | `search_jobs` | read, external | — (Arbeitnow, free) | `app/tools/job_search.py` |
| 5 | `estimate_salary` | read, external | `TAVILY_API_KEY` | `app/tools/salary.py` |
| 6 | `get_applications` / `update_tracker` | read / **write** (approval) | — | `app/tools/tracker.py` |

`app/tools/registry.py` → `TOOL_REGISTRY` holds a label, a default, the required keys and the
calls that need approval for each tool. A tool with missing keys is shown off and disabled.

---

## 6. Cross-cutting pieces

| Concern | Where | How |
|---|---|---|
| LLM access | `app/agent/llm.py` | `get_llm()` builds a `ChatOpenAI` pointed at OpenRouter; `invoke_structured()` adds retries and one schema retry |
| Prompts | `app/agent/prompts.py` | All prompts and tool descriptions, `PROMPT_VERSION`, `wrap()` for delimiters |
| Cost | `app/services/usage.py` | `track_usage(action)` counts tokens of every LLM call inside it → `UsageLog` |
| Tracing | `app/services/observability.py` | `track_run(action)` = cost tracking + LangSmith tags and metadata |
| Errors | `app/utils/errors.py` | `AppError` subclasses with a `user_message`; `friendly_message()` for unknown errors |
| Logging | `app/utils/logging.py` | `data/runtime/app.log`, with job and thread ids |
| Settings | `app/config.py` | `Settings` (pydantic-settings) + developer overrides + `MODEL_PRICING` |
