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

**What uploads are allowed?**
Only PDF, DOCX, TXT and MD, up to 5 MB each and 10 files at once. Files without readable text
(for example scanned images) are rejected with a clear message. One bad file doesn't stop the
others (`app/services/onboarding.py` → `add_documents()`).

### Error handling
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
| Tests | 40 offline + 1 live (`uv run pytest`, `uv run pytest -m live`) |
| Chunking | 500 characters, 80 overlap, `RecursiveCharacterTextSplitter` |
| Embeddings | `openai/text-embedding-3-small` via OpenRouter (1,536 dimensions) |
| Vector store | Chroma, cosine distance, `data/runtime/chroma/` |
| Long-term memory | LangGraph `SqliteStore`, `data/runtime/memory.db` |
| Demo knowledge base | 4 documents, 21 chunks |
| Sample jobs | 3: good, partial and poor fit (German C1) |
