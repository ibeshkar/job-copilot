# What: tests for app/services/company.py (the Company tab's research), offline.
# Why: the brief must come from the web results only, list the real result URLs as sources,
#      be cached per job, be removed with the job, and fail with a friendly message.
# Used by: pytest.

from app.agent.state import CompanyBrief
from app.services import applications, company
from app.tools import company as company_tool
from app.tools.company import WebResult
from tests.copilot_fakes import two_jobs
from tests.fakes import FakeStructuredLLM

RESULTS = [WebResult("Lumen Health", "https://lumen.example", "Lumen builds AI for clinics.")]
BRIEF = CompanyBrief(
    summary="Lumen Health builds AI assistants for clinics.",
    products=["Clinic assistant"],
    tech_stack=["Python"],
    culture=[],
    recent_news=[],
    talking_points=["Your RAG project fits their assistant."],
)


def test_research_is_saved_with_real_sources(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    searched = []
    monkeypatch.setattr(company, "search_company", lambda c, f: searched.append((c, f)) or RESULTS)
    llm = FakeStructuredLLM({CompanyBrief: BRIEF})

    research = company.research_for_job(job_id, "culture", llm=llm)

    assert research.brief == BRIEF
    assert searched == [("Lumen Health", "culture")]
    prompt = llm.calls[0][1][-1].content
    assert "<untrusted_web_content>" in prompt and "Junior AI Engineer" in prompt
    saved = company.saved_research(job_id)
    assert saved.brief == BRIEF
    assert [s.url for s in saved.sources] == ["https://lumen.example"]


def test_deleting_the_job_forgets_the_research(fake_app, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company, "search_company", lambda c, f: RESULTS)
    company.research_for_job(job_id, llm=FakeStructuredLLM({CompanyBrief: BRIEF}))

    applications.delete_job(job_id)

    assert company.saved_research(job_id) is None


def test_errors_become_a_message(app_db, monkeypatch) -> None:
    _, job_id = two_jobs()
    monkeypatch.setattr(company_tool, "_tavily_search", lambda q, n: {"results": []})

    research, message = company.run_research(job_id)

    assert research is None
    assert message == "I found nothing on the web about Lumen Health."
