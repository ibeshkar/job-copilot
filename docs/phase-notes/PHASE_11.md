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

## Follow-up: requirements list redesign on the Fit tab (UI only)
- **What changed:** the requirement list was one expander per requirement, a long wall of boxes
  where the status was only an emoji and every detail needed a click. Now: a summary ("Must-have
  met: X / Y", progress bar, met / partial / missing badges), a filter (All / Gaps only /
  Must-have only), one expander per status group (Missing and Partial open, Met closed, empty
  groups hidden) and one compact row per requirement with a details popover for evidence and
  the honest tip. D83.
- **UX reasoning:** prioritise gaps (that's where the decision is), progressive disclosure
  (decide from the row, open details only when needed), fewer clicks (no click to see the
  overall picture or the gaps).
- **Files:** `app/ui/components.py` (`requirements_section`, `requirements_summary`,
  `group_requirements`, `requirement_row`, `status_badge`, `importance_badge`; `fit_card` takes
  a `key`), `app/ui/pages/2_job_workspace.py` (passes `key=f"fit_{job_id}"`),
  `tests/test_ui_components.py` (new: grouping, filters, sort order, badges),
  `tests/test_ui_pages.py` (group labels, summary, filter).
- **Problem I hit:** with four columns per row, a phone screen stacked them into four blocks
  per requirement (icon, text, badge, button on separate lines). Two non-wrapping columns
  (`st.columns(..., wrap=False)`) keep each row on one line.
- **Not changed:** score, evidence, data, and the Continue / Add context / Not a fit actions.
- **Try it:** add the 3 sample jobs (Board → ➕ Add job → sample) and open each 🎯 Fit tab:
  - *Good fit*: almost everything met; ✅ Met is the big group but closed, so the few gaps
    are what you see first. "📄 Evidence" shows quotes with file names.
  - *Partial fit*: ❌ Missing and ⚠️ Partial open with "💡 Tip" / "📄 Details" buttons;
    **Gaps only** hides ✅ Met.
  - *Poor fit*: the blocking German C1 flag above, a low "Must-have met" bar, **Must-have
    only** hides the nice-to-haves.
- **Known follow-up:** the screenshots `docs/assets/fit_good.png` and `fit_poor.png` still show
  the old list; retake them with the D78 throwaway-runtime script.


## Follow-up: claim check redesign (UI only)
- **What changed:** each claim was a bordered card inside a bordered form, with an always-open
  text box, a badge and an "Evidence" expander; only ~4 claims fit on a screen. Now: a summary
  line, *Keep all backed* / *Untick unconfirmed*, one flat list with thin dividers ("⚠️ Needs your
  confirmation" first, then "✅ Backed by your documents"), one compact row per claim (checkbox,
  text, "📄 N sources" popover, ✏️ to edit with Save / Cancel) and a footer with "X of N claims
  selected" and *✍️ Write my application →*. D84.
- **UX reasoning:** no nested containers (more claims per screen, less noise), edit on demand
  (most claims are kept as they are), unconfirmed claims first (they're the real decisions),
  one clear primary action at the end (disabled when nothing is selected).
- **Files:** `app/ui/components.py` (claim check pieces and state helpers; `evidence_quotes`
  shared with the requirements popover; `claim_evidence` removed),
  `app/ui/pages/2_job_workspace.py` (`_claim_review` replaces the form),
  `tests/test_ui_components.py` (grouping, summary, row markdown), `tests/test_ui_pages.py`
  (summary and order, edit / Save, bulk buttons, refresh, disabled button, payload).
- **Problem I hit:** the old `st.form` can't contain normal buttons, so ✏️ / Save / Cancel were
  impossible inside it. The choices moved to `st.session_state`, defaulted from the graph's saved
  claims, so a refresh still shows the same claims with the default ticks.
- **Not changed:** graph, interrupt, and the resume payload.
- **Try it:** add the *Partial fit* sample job, choose *Continue to application*, open
  ✅ Claim check. Untick a claim (struck through, count drops), edit one with ✏️ (tag "edited"),
  open "📄 N sources", try both bulk buttons, untick everything (button disabled), refresh the
  browser, then write the application and check the Documents tab.
- **Known follow-up:** `docs/assets/claim_check.png` still shows the old cards; retake it with
  the D78 throwaway-runtime script.


## Follow-up: cover letter PDF export and a structured letter
- **What changed:** the drafting prompt returns a `CoverLetter` (recipient block, subject,
  salutation, 4–6 paragraphs, closing, sender) instead of one text block; Python checks the
  contact name against the posting and falls back to "Dear Hiring Team,". The Documents tab
  edits each part separately, shows a white "paper" preview, and offers **⬇️ PDF** (primary)
  and **⬇️ Word (.docx)** with the same layout (A4, 2.5 cm margins, 11 pt Liberation Sans /
  Arial, 18 pt bold name, grey contact line, recipient, date, bold subject). The letter fits on
  one page: spacing shrinks first, then the font. If an export fails, the button offers a .txt.
  The profile got optional email, phone, LinkedIn and city fields. D85.
- **New dependency:** `reportlab` (PDF). Font: Liberation Sans in `app/assets/fonts/` (OFL).
- **Files:** `app/agent/state.py` (`CoverLetter`, `Drafts` accepts old plain text),
  `app/agent/prompts.py`, `app/agent/nodes/drafting.py` (`finalise_letter`),
  `app/services/export.py` (rewritten: `letter_blocks`, `LetterLayout`, PDF, DOCX, .txt
  fallback), `app/services/applications.py` (`export_cover_letter`, `cover_letter_blocks`;
  `export_drafts` removed), `app/memory/store.py` + `app/ui/components.py` (contact fields),
  `app/ui/pages/2_job_workspace.py` (letter editor, preview, downloads), `evals/runners.py`,
  `tests/test_export.py` (rewritten) and the tests that used the letter as a string.
- **Problems I hit:** (1) `Drafts.cover_letter` was a string in ~30 places (fact-checker, edit
  diff, evals, tests); the new `CoverLetter.text` gives the old plain text where it's needed.
  (2) reportlab listed Helvetica in every PDF as the canvas's starting font even though no text
  used it; `initialFontName` fixes that. (3) Old drafts: the DB column and checkpoints keep
  plain text, so `Drafts` turns a string into body paragraphs without adding a second
  salutation or signature.
- **Try it:** use the demo persona, add the *Good fit* sample job, go through the claim check,
  then in 📝 Documents change the subject and a paragraph, watch the preview, and download both
  files. A sample from a real run is in `data/runtime/CoverLetter_Alex_Novak_LumenHealthGmbH.pdf`.
- **Known follow-up:** `docs/assets/documents.png` still shows the single text box; retake it.


## Follow-up: one helper for "input + button" rows (UI only)
- **What changed:** the Company tab's **🔄 Research again** button sat a few pixels below the
  Focus box because of a `markdown("&nbsp;")` spacer. The Board column + 🗑️ row had had the same
  bug before. New `action_row()` / `input_with_action()` in `app/ui/components.py` bottom-align
  the row, so the button and the input share top and bottom edges (both 40 px). The Company tab,
  the workspace header and the Memory page now use them. On the Memory page the "learned from…"
  caption moved under the row; inside the input column it pushed 💾 and 🗑️ down. Behaviour is
  unchanged: Enter in the Focus box only keeps the text, and the button runs the research. D86.
- **Files:** `app/ui/components.py`, `app/ui/pages/2_job_workspace.py`,
  `app/ui/pages/4_memory.py`, `tests/test_ui_components.py`, `docs/DECISIONS.md`.
- **Checked:** light and dark theme at 900 px and a 420 px window (Playwright + Chrome
  screenshots, element boxes measured: input and button both start at the same y, 40 px high).


## Follow-up: only recent news in Company research
- **What changed:** "Recent news" showed items from 2023 in a 2026 run. A separate Tavily news
  search (`topic="news"`, `time_range="year"`, current year in the query) now feeds the list,
  and Python drops anything older than 12 months by its `published_date`. Undated items are
  marked "date unknown"; with nothing recent the tab says "No recent news found in the last 12
  months". "Things you could mention" may only use the dated recent news (prompt rule), and
  points naming an older year are dropped in Python. D87.
- **Files:** `app/tools/company.py` (`search_news`, `parse_published`, `filter_recent`,
  `WebResult.published`, `topic` / `time_range` for `_tavily_search`), `app/services/company.py`
  (`CompanyResearch.news`, `drop_stale_points`), `app/agent/state.py` (`CompanyBrief` without
  `recent_news`), `app/agent/prompts.py` (brief prompt, `PROMPT_VERSION` 2026-10-30.2),
  `app/ui/components.py` (`news_line`), `app/ui/pages/2_job_workspace.py`,
  `tests/test_tools.py` (date parsing, 12-month filter, news query), `tests/test_company_service.py`,
  `tests/test_ui_components.py`, `tests/test_ui_pages.py`.
- **Try it:** `uv run pytest tests/test_tools.py tests/test_company_service.py -v`, then open a
  job → 🏢 Company → 🔄 Research again. Each news item shows its month; nothing is older than
  12 months.
- **Known follow-up:** research cached before this change shows "Research again to load news
  from the last 12 months" until it is refreshed.


## Follow-up: one progress box for every loading state, and no empty company name
- **What changed:** the running indicator of `st.status` boxes was hard to see and vanished on
  hover (Streamlit swaps the small spinner for the expand arrow; no CSS of ours involved). Now
  one helper, `progress_status` / `run_with_progress` (`app/ui/components.py`), shows every
  long-running action: the header says what runs now and how far it is ("⏳ Analysing fit…
  (4/5) Searching your documents…"), a progress bar for fixed runs, "✅ Fit analysis done" and
  collapsed on success, red with a friendly message on error. A search retry updates the
  "Searched your documents" line instead of adding new ones. A posting without a company name
  now gets "Unknown company" (prompt rule + fallback), and **✏️** in the workspace header fixes
  title or company. D88, D89.
- **Switched to the helper:** Board → Add job (fit analysis); Job workspace → continue / not a
  fit / re-check with context / retry analysis (🎯 Fit), claim check → drafts (✅ Claim check),
  accept, regenerate, save + learn from edits (📝 Documents), company research (🏢), salary
  estimate (💰), interview start / answer / end / retry (🎤); Get started → upload documents and
  demo persona; Help → help-bot answer. Not switched: Copilot chat (streams its answer).
- **Files:** `app/ui/components.py`, `app/ui/pages/0_onboarding.py`, `1_board.py`,
  `2_job_workspace.py`, `5_help.py`, `app/services/applications.py` (`_running_message`,
  `tasks` stream, `rename_job`, `save_analysis_to_job`), `app/services/interview.py`,
  `app/services/export.py`, `app/agent/state.py` (`UNKNOWN_COMPANY`, `UNKNOWN_TITLE`),
  `app/agent/prompts.py` (`PROMPT_VERSION` 2026-11-02.1), `app/agent/nodes/ingest.py`,
  `app/agent/nodes/drafting.py`
  (`letter_subject`), tests in `tests/test_applications_service.py`, `test_ui_components.py`,
  `test_export.py`, `test_interview.py`; docs `DECISIONS.md`, `CODE_TOUR.md`, `REVIEW_GUIDE.md`,
  `USER_GUIDE.md`.
- **Checked:** Playwright + Chrome on a demo page with the helper: light theme at 1100 px and
  dark theme at 360 px, header readable while hovered (the arrow replaces the spinner, the
  "⏳ … (4/5) …" text stays), no horizontal scroll, the box stays open while running and
  collapses to "✅ Fit analysis done".

