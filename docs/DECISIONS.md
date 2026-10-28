# Technical decisions

A log of the choices made while building the app: what we chose, why, and what else we
considered. Newest entries at the bottom.

## D1. Code lives in a top-level `app/` package (Phase 1)
- **What:** The plan's folder layout (`app/…`) is used as-is. The `src/job_copilot/` stub that
  `uv init` created was removed. `pyproject.toml` tells the `uv_build` backend that the package
  is called `app` and sits in the project root (`module-name = "app"`, `module-root = ""`).
- **Why:** `uv sync` then installs `app` into the virtual environment, so `import app.config`
  works from Streamlit, pytest and scripts without any `sys.path` tricks.
- **Alternative:** keep `src/job_copilot/`. That's the more common packaging layout, but it would
  make every path in the plan and the docs different from the code.
- **Trade-off:** `app` is a generic name. That's fine for an application nobody imports as a
  library.

## D2. Python 3.14 (Phase 1)
- **What:** The project runs on Python 3.14 (from `.python-version`). `requires-python` is `>=3.11`
  as in the plan.
- **Why:** all dependencies install and the tests pass on 3.14, and it was already the
  project's interpreter.
- **Trade-off:** `deepeval` and `langsmith` print deprecation warnings on 3.14. They are
  filtered in the pytest config and are harmless.

## D3. Status columns stored as plain text, checked in the repo (Phase 1)
- **What:** `Job.status` and `Feedback.target` are `str` columns. `app/db/repo.py` checks them
  against the `JobStatus` / `FeedbackTarget` enums before saving.
- **Why:** SQLAlchemy enum columns store the enum *name* (`"APPLIED"`), which is confusing in a
  SQLite viewer. Plain text stores `"applied"`, and the repo still rejects bad values.
- **Alternative:** a database-level `Enum` column or a CHECK constraint.

## D4. All database access goes through `app/db/repo.py` (Phase 1)
- **What:** pages, tools and graph nodes call functions like `create_job(session, …)`. They
  don't build queries themselves.
- **Why:** one tested place for database logic. The functions take a `Session` argument, so tests
  can pass an in-memory database (`tests/conftest.py`).
- **Alternative:** a repository class, or querying inside the pages. A class adds nothing for a
  single-user app, and queries inside pages would break rule 3 of the plan.

## D5. `ChatOpenAI` client retries kept at 1 (Phase 1)
- **What:** `get_llm()` sets `max_retries=1`.
- **Why:** the plan uses `tenacity` for retries (3 tries with exponential backoff, §10). If the
  client also retried 3 times, one failure could become 9 calls.

## D6. Keys come from `.env` only, for now (Phase 1)
- **What:** `Settings` (pydantic-settings) reads `.env` and environment variables. Keys are
  `SecretStr`, so they show up as `**********` in logs.
- **Why:** this is enough for local use. Reading `st.secrets` matters only for Streamlit Cloud,
  which the plan rules out (no cloud hosting).

## D7. LLM calls go through OpenRouter, not OpenAI directly (Phase 1)
- **What:** `get_llm()` still uses `ChatOpenAI` from `langchain-openai`, but with
  `base_url=https://openrouter.ai/api/v1` and `OPENROUTER_API_KEY`. Model names use OpenRouter
  ids such as `openai/gpt-4o-mini`. This changes the plan, which assumed an OpenAI key.
- **Why:** the developer has an OpenRouter key. OpenRouter accepts the same request format as
  OpenAI, so function calling and structured output still work and no new library is needed.
  As a bonus, switching to another provider's model is just a change to `DEFAULT_MODEL`.
- **Alternative:** a direct OpenAI key (needs a separate account and billing), or
  `langchain-openrouter`-style wrappers (an extra dependency for no real gain).
- **Trade-off:** one more company sees the data, and OpenRouter adds a small markup and latency.
  Embeddings for Phase 2 must also go through OpenRouter's embeddings endpoint; check this
  works before relying on it.

## D8. Both `.env` and `.env.example` exist (Phase 1)
- **What:** `.env` holds the real keys and is gitignored. `.env.example` is the same list of
  variable names with empty values, and it is committed.
- **Why:** anyone who clones the repo (a reviewer, or you on a new laptop) needs to know which
  variables exist, but must never see your keys. The example file documents them safely.

## D9. Memory namespaces start with the user id (Phase 2)
- **What:** namespaces are `(user_id, kind)`, for example `("local", "profile")` and
  `("local", "style_rules")`. The plan wrote `("user", "profile")`; we read "user" as a placeholder.
- **Why:** with the user id as the first part, adding more users later is just a different id,
  and "Delete all my data" is one prefix search: `store.search(("local",))`.
- **Alternative:** a fixed `("user", …)` namespace. That's simpler, but every user would share it.

## D10. Re-uploading a file deletes its chunks first; chunk ids are stable (Phase 2)
- **What:** chunk ids are `<filename>#<n>`. Before adding a file, all chunks with that
  `doc_name` are deleted.
- **Why:** an "upsert" alone would leave old chunks behind when the new version is shorter
  (for example, an old `cv.md#9` when the new CV has only 7 chunks).
- **Trade-off:** two different files with the same name replace each other. That's usually what
  the user means ("my updated CV").

## D11. Document type is guessed from the file name (Phase 2)
- **What:** `guess_doc_type()` maps names like `*cv*`, `*readme*` and `*brag*` to `cv`,
  `project`, `brag_doc`, …, otherwise `other`.
- **Why:** it keeps the wizard to one upload box. The type is only used to filter searches.
- **Alternative:** a type dropdown per file (more accurate, more clicks), or an LLM classifier
  (costs money for little gain). We may add the dropdown later.

## D12. Offline fake embeddings in unit tests (Phase 2)
- **What:** `tests/fakes.py` → `KeywordEmbeddings` hashes words into a vector. One test marked
  `@pytest.mark.live` uses the real OpenRouter model.
- **Why:** tests must run without a network, for free, and give the same result every time.
- **Trade-off:** fake embeddings only match exact words (no synonyms), so the offline test is
  looser (top 4) than the live one (top 1).

## D13. A service layer for onboarding (Phase 2)
- **What:** `app/services/onboarding.py` (not listed in the plan's folder tree) holds the
  wizard's logic: upload limits, ingest plus `Document` row, demo persona, summary.
- **Why:** rule 3 of the plan (no business logic in Streamlit files). It also makes the upload
  rules testable without a browser.
