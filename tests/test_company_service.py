# What: tests for app/services/company.py (the Company tab's research), offline.
# Why: the brief must come from the web results only, list the real result URLs as sources,
#      show only news from the last 12 months, keep talking points off old events, be cached
#      per job, be removed with the job, and fail with a friendly message.
# Used by: pytest.

from datetime import date

from app.agent.state import CompanyBrief
from app.services import applications, company
from app.tools import company as company_tool
from app.tools.company import WebResult
from tests.copilot_fakes import two_jobs
from tests.fakes import FakeStructuredLLM

RESULTS = [WebResult("Lumen Health", "https://lumen.example", "Lumen builds AI for clinics.")]
NEWS = [
    WebResult("Lumen opens Vienna office", "https://news.example/vienna", "…", "2026-09-14"),
    WebResult("Lumen in the press", "https://news.example/press", "…", None),
]
BRIEF = CompanyBrief(
    summary="Lumen Health builds AI assistants for clinics.",
    products=["Clinic assistant"],
    tech_stack=["Python"],
    culture=[],
    talking_points=["Your RAG project fits their assistant."],
)


def test_research_is_saved_with_real_sources(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    searched = []
    monkeypatch.setattr(company, "search_company", lambda c, f: searched.append((c, f)) or RESULTS)
    monkeypatch.setattr(company, "search_news", lambda c, today: NEWS)
    llm = FakeStructuredLLM({CompanyBrief: BRIEF})

    research = company.research_for_job(job_id, "culture", llm=llm)

    assert research.brief == BRIEF
    assert searched == [("Lumen Health", "culture")]
    prompt = llm.calls[0][1][-1].content
    assert "<untrusted_web_content>" in prompt and "Junior AI Engineer" in prompt
    saved = company.saved_research(job_id)
    assert saved.brief == BRIEF
    assert [s.url for s in saved.sources] == ["https://lumen.example"]
    assert saved.news == NEWS


def test_only_dated_news_reaches_the_model(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company, "search_company", lambda c, f: RESULTS)
    monkeypatch.setattr(company, "search_news", lambda c, today: NEWS)
    llm = FakeStructuredLLM({CompanyBrief: BRIEF})

    company.research_for_job(job_id, llm=llm)

    prompt = llm.calls[0][1][-1].content
    news_block = prompt.split("<untrusted_recent_news>")[1]
    assert "Lumen opens Vienna office" in news_block and "published 2026-09-14" in news_block
    assert "Lumen in the press" not in prompt


def test_no_recent_news_is_said_to_the_model(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company, "search_company", lambda c, f: RESULTS)
    monkeypatch.setattr(company, "search_news", lambda c, today: [])
    llm = FakeStructuredLLM({CompanyBrief: BRIEF})

    research = company.research_for_job(job_id, llm=llm)

    assert "(no news from the last 12 months)" in llm.calls[0][1][-1].content
    assert company.saved_research(job_id).news == research.news == []


def test_talking_points_about_old_years_are_dropped() -> None:
    points = [
        "Their 2023 Series A shows strong growth.",
        "They opened a Vienna office in 2026.",
        "Their late-2025 launch of the clinic assistant.",
        "Your RAG project fits their assistant.",
    ]

    kept = company.drop_stale_points(points, date(2026, 10, 30))

    assert kept == points[1:]


def test_research_saved_before_the_news_search_still_loads(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    old = {
        "company": "Lumen Health",
        "brief": {**BRIEF.model_dump(), "recent_news": ["Raised a Series A (May 2023)"]},
        "sources": [vars(r) for r in RESULTS],
        "focus": None,
        "researched_at": "2026-01-02T10:00:00+00:00",
    }
    old["sources"][0].pop("published")
    company.store.save_company_research(job_id, old)

    saved = company.saved_research(job_id)

    assert saved.brief == BRIEF
    assert saved.news is None


def test_deleting_the_job_forgets_the_research(fake_app, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company, "search_company", lambda c, f: RESULTS)
    monkeypatch.setattr(company, "search_news", lambda c, today: [])
    company.research_for_job(job_id, llm=FakeStructuredLLM({CompanyBrief: BRIEF}))

    applications.delete_job(job_id)

    assert company.saved_research(job_id) is None


def test_errors_become_a_message(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company_tool, "_tavily_search", lambda q, n, **kw: {"results": []})

    research, message = company.run_research(job_id)

    assert research is None
    assert message == "I found nothing on the web about Lumen Health."
