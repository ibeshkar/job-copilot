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

### Security
**How are secrets handled?**
They're read from `.env` by `app/config.py` → `Settings` and stored as `SecretStr`, which hides
the value in logs. `.env` is in `.gitignore`, and only `.env.example` with empty values is
committed.

**How is SQL injection prevented?**
All queries use the SQLModel ORM (`app/db/repo.py`), which sends values as bound parameters.
No query is built by joining strings.

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
| Tests | 17 (run with `uv run pytest`) |
| Sample jobs | 3: good, partial and poor fit (German C1) |
