# What: company research for the Job workspace's Company tab: web search with the
#       `research_company` tool's search function plus a dated news search (only the last 12
#       months, checked in Python), then an LLM brief (structured output) with the sources
#       listed, cached per job.
# Why: the brief helps tailor the letter and prepare for the interview. Caching it in the Store
#      means a refresh or restart doesn't spend Tavily and LLM credits again.
#      Its tokens are logged as the "company_research" action.
# Used by: app/ui/pages/2_job_workspace.py, app/services/applications.py (delete_job).

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from langchain_core.language_models import BaseChatModel

from app.agent.llm import get_llm, invoke_structured
from app.agent.prompts import company_brief_messages
from app.agent.state import CompanyBrief
from app.db import repo
from app.memory import store
from app.services.observability import track_run
from app.tools.company import (
    NEWS_MAX_AGE_DAYS,
    WebResult,
    format_results,
    search_company,
    search_news,
)
from app.utils.errors import ExternalServiceError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)


@dataclass
class CompanyResearch:
    """The brief plus the web pages it is based on."""

    company: str
    brief: CompanyBrief
    sources: list[WebResult]
    focus: str | None
    researched_at: datetime
    # News from the last 12 months, newest first; undated items last ("date unknown").
    # None for research saved before the news search existed.
    news: list[WebResult] | None = None


def research_for_job(
    job_id: int, focus: str | None = None, llm: BaseChatModel | None = None
) -> CompanyResearch:
    """Research the job's company, save the result and return it.

    Raises:
        ConfigError: no Tavily or OpenRouter key.
        ExternalServiceError: no web results, or Tavily failed.
        LLMServiceError / StructuredOutputError: the brief couldn't be written.
    """
    with repo.session_scope() as session:
        job = repo.get_job(session, job_id)
    today = datetime.now(UTC).date()
    results = search_company(job.company, focus)
    news = search_news(job.company, today)
    if not results and not news:
        raise ExternalServiceError(
            f"No results for {job.company}",
            user_message=f"I found nothing on the web about {job.company}.",
        )
    # Only dated news goes to the model: an undated item could be old, so talking points
    # never rest on it. It is still shown in the tab, marked "date unknown".
    dated_news = [n for n in news if n.published]
    brief = invoke_structured(
        llm or get_llm(),
        CompanyBrief,
        company_brief_messages(
            job.company,
            job.title,
            format_results(results),
            format_results(dated_news),
            today.isoformat(),
        ),
    )
    brief = brief.model_copy(
        update={"talking_points": drop_stale_points(brief.talking_points, today)}
    )
    research = CompanyResearch(
        company=job.company,
        brief=brief,
        # Sources are the search results themselves, attached in Python, so the links shown
        # are real pages and never URLs the model made up.
        sources=results,
        focus=focus,
        researched_at=datetime.now(UTC),
        news=news,
    )
    store.save_company_research(job_id, _to_dict(research))
    return research


def run_research(job_id: int, focus: str | None = None) -> tuple[CompanyResearch | None, str]:
    """Like `research_for_job`, but never raises: returns (research, "") or (None, message)."""
    try:
        with track_run("company_research", job_id=job_id):
            return research_for_job(job_id, focus or None), ""
    except Exception as exc:
        log_exception(logger, "Company research failed", job_id=job_id)
        return None, friendly_message(exc)


def saved_research(job_id: int) -> CompanyResearch | None:
    """The cached research for a job, or None if it hasn't been researched yet."""
    data = store.get_company_research(job_id)
    if not data:
        return None
    return CompanyResearch(
        company=data["company"],
        brief=CompanyBrief.model_validate(data["brief"]),
        sources=[WebResult(**s) for s in data["sources"]],
        focus=data.get("focus"),
        researched_at=datetime.fromisoformat(data["researched_at"]),
        news=[WebResult(**n) for n in data["news"]] if "news" in data else None,
    )


def forget_research(job_id: int) -> None:
    """Delete the cached research (when the job is deleted)."""
    store.delete_company_research(job_id)


def _to_dict(research: CompanyResearch) -> dict:
    return {
        "company": research.company,
        "brief": research.brief.model_dump(mode="json"),
        "sources": [vars(s) for s in research.sources],
        "focus": research.focus,
        "researched_at": research.researched_at.isoformat(),
        "news": [vars(n) for n in research.news or []],
    }


def drop_stale_points(points: list[str], today: date) -> list[str]:
    """Drop talking points that name a year before the 12-month news window.

    A safety net for the prompt rule: in October 2026 the window starts in October 2025, so
    "their 2023 Series A" is dropped and "their 2025 launch" or "their AI assistant" is kept.
    """
    oldest_year = (today - timedelta(days=NEWS_MAX_AGE_DAYS)).year
    return [
        point
        for point in points
        if all(int(year) >= oldest_year for year in re.findall(r"\b(?:19|20)\d{2}\b", point))
    ]
