# Job Application Copilot

An AI agent that helps job seekers find out whether they really fit a role, prove it with
evidence from their own documents, write honest tailored applications, and track every
application in one place.

> 🚧 Work in progress. Phases 1–6 are done: onboarding, knowledge base, fit analysis, board,
> claim check, drafts with fact check, DOCX export, copilot chat with 5 tools, company research.
> See `PROJECT_PLAN.md` for the full plan and
> `docs/phase-notes/` for what each phase added.

## Quick start
```bash
uv sync                          # create .venv and install dependencies
# put your OPENROUTER_API_KEY in .env (create it from .env.example if missing)
# optional: TAVILY_API_KEY (company research), ADZUNA_APP_ID + ADZUNA_APP_KEY (job search)
uv run streamlit run app/main.py
```

## Development
```bash
uv run python scripts/analyse_sample_jobs.py   # fit analysis on the 3 sample jobs (real LLM)
uv run ruff check .              # lint
uv run pytest                    # unit tests (no network)
uv run pytest -m live            # end-to-end tests with the real LLM (costs a few cents)
```

## Privacy
All data is stored locally in `data/runtime/` (gitignored). Text you analyse is sent to OpenRouter,
which forwards it to the model provider (and to Tavily for company research, once that's added).
