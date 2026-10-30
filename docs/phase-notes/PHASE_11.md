# Phase 11: Salary suggestion

## What I built (plain English, 3–5 sentences)
A salary estimate for each job. The app runs 2–3 web searches for salaries of similar roles in
the job's city (for Germany also the Bundesagentur für Arbeit's Entgeltatlas), an LLM lists the
salary figures it finds, and plain Python checks every figure against its source, converts it
to yearly gross EUR, drops outliers and computes the range (25th percentile, median, 75th
percentile) and a confidence level. A second LLM call explains the range: what pushes pay up or
down, how it compares with the user's expected salary, and 2–3 honest negotiation tips. With
too little data the app says "not enough data" instead of guessing.

## Why it matters for the user
- They go into a salary conversation knowing the market range, with sources they can check.
- Their expectation from the profile is compared with the range (below / within / above).
- The board shows a 💰 badge, and the copilot answers "What salary should I ask for at …?".
- No invented numbers: every figure in the range is in a real source, and "not enough data" is
  an honest answer.

## Concepts used
- [LLM extracts, Python calculates: the salary estimate](../CONCEPTS.md#llm-extracts-python-calculates-the-salary-estimate-phase-11) (new)
- [Structured output with Pydantic](../CONCEPTS.md#structured-output-with-pydantic)
- [Guardrails: prompt injection](../CONCEPTS.md#guardrails-prompt-injection-and-ssrf): search results inside `<untrusted_web_content>`
- [Token and cost tracking](../CONCEPTS.md#token-and-cost-tracking): the new action `estimate_salary`

## Files created or changed
| File | Purpose |
|---|---|
| `app/tools/salary.py` | New: searches, LLM extraction, Python checks / normalisation / outliers / percentiles / confidence, explanation, cache, the `estimate_salary` tool |
| `app/services/salary.py` | New: `run_estimate()` for the tab (tracked as `estimate_salary`, friendly errors) and `saved_estimate()` |
| `app/agent/state.py` | New models: `SalaryDataPoint`, `SalaryExtraction`, `NormalisedSalary`, `SalaryExplanation`, `SalaryEstimate` |
| `app/agent/prompts.py` | `SALARY_EXTRACTION_SYSTEM`, `SALARY_EXPLANATION_SYSTEM`, the tool description, a copilot rule for salary questions; `PROMPT_VERSION` 2026-10-30.1 |
| `app/db/models.py`, `app/db/repo.py` | New `SalaryCache` table; save / get / list; removed with the job and by "Delete all my data" |
| `app/tools/company.py` | New `web_search()` (with an optional site filter), shared by company research and salary |
| `app/tools/registry.py` | New toggle "💰 Estimate salaries" (on by default, needs `TAVILY_API_KEY`) |
| `app/services/applications.py` | `JobCard.salary` for the board badge |
| `app/ui/pages/2_job_workspace.py` | New 💰 Salary tab |
| `app/ui/pages/1_board.py`, `app/ui/components.py` | Salary badge on the card; confidence badges |
| `tests/test_salary.py` | New: 41 tests (maths offline; pipeline, tool, cache, errors with a fake Tavily and a scripted LLM) |
| `tests/test_tools.py`, `tests/test_copilot_service.py` | The registry now has 6 toggles |
| Docs | `PROJECT_PLAN.md` (C6, tools table, models, tables, pages, Phase 11), `DECISIONS.md` D80–D82, `CONCEPTS.md`, `REVIEW_GUIDE.md` (4 new questions), `USER_GUIDE.md` (use case 5), `GLOSSARY.md`, `ARCHITECTURE.md`, `SECURITY.md`, `README.md` |

## How it works (small diagram or numbered steps)
```
job posting ─┐
             ├─ salary_queries ─► Tavily ×2–3 ─► LLM: SalaryExtraction (figures + URLs)
profile ─────┘                                        │
                         Python: check_point (URL is a result? number in its text?)
                                 → yearly gross EUR → relevant? → drop_outliers
                                 → build_estimate (P25 / median / P75, confidence)
                                   or "not_enough_data" (< 2 figures, no posting salary)
                                                      │
                         LLM: SalaryExplanation (factors, comparison, tips) ─► SalaryCache
```
The Salary tab calls `services.salary.run_estimate()`; the copilot calls the tool, which reads
the cache first and runs the same `estimate_for_job()` on a miss or with `refresh=true`. Both
log tokens under `estimate_salary`.

## How to try it (exact commands / clicks)
```bash
uv run pytest tests/test_salary.py -v      # 41 tests, offline
uv run streamlit run app/main.py
```
1. Make sure `.env` has `TAVILY_API_KEY` and `OPENROUTER_API_KEY`.
2. 🧠 Memory → profile → set "Salary expectations", e.g. `55-65k EUR`.
3. Board → ➕ Add job → sample "Good fit" → wait for the fit analysis.
4. Open the job → **💰 Salary** → **💰 Estimate salary**. Open "Sources and data points".
5. Back on the Board: the card has a 💰 badge.
6. 💬 Copilot chat: "What salary should I ask for at Lumen Health?"
7. Developer settings → cost table: a row `estimate_salary`.

## Decisions and alternatives
- **LLM extracts, Python calculates** (D80): the range is traceable and tested; the LLM never
  produces a number we show.
- **Refuse to guess** (D81): "not enough data" beats a confident wrong number.
- **New table, not a column** (D82): `create_all` can't add columns to an existing database.

## Problems I hit and how I solved them
- **The first live run counted a "Junior Python Developer" figure for a mid-level job** and the
  range was very wide (45k–111k). Fixed: relevance now compares the seniority level, and the
  range uses only relevant figures when there are at least 2.
- **Same-source checking needs number parsing.** "55.000", "55,000", "55k", "60 000" and
  "25,50" all mean different things; `numbers_in()` treats groups of 3 digits after a
  separator as thousands and anything else as decimals, with tests for each form.
- **Avoiding an import cycle:** the tool needs the job's skills from the workflow state, and
  the applications service imports the company service. The skills are read with a local
  import in `_job_skills()`, and the board badge reads the cache straight from the repo.

## Possible review questions (with short answers)
**Why not just ask the LLM for a salary range?** It can't show where the number comes from,
it's bad at maths, and it guesses when data is missing. D80.

**What if the LLM invents a figure?** It's dropped: the URL must be one of our results and the
number must be in that result's text (`check_point`). Tested.

**How do you handle monthly, hourly, net, CHF?** `to_yearly_gross`: ×12, ×2,080, ÷0.62 for
net, fixed exchange rates. Approximate on purpose; shown per data point in the tab.

**What does it cost?** 2–3 Tavily searches plus two small LLM calls: about $0.0006 in tokens
per estimate, cached until Refresh.
