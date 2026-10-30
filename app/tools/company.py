# What: the `research_company` tool: searches the web (Tavily) for a company's products,
#       culture, tech stack and news, and returns the snippets marked as untrusted content.
#       Also `search_news()`: a dated news search whose items older than 12 months are dropped
#       in Python (`filter_recent`), so old news is never shown as "recent".
# Why: tailoring an application needs facts about the company. Web text can contain anything,
#      including prompt injections, so it is always wrapped in <untrusted_web_content> tags.
# Used by: app/tools/registry.py (copilot chat), app/services/company.py (Company tab),
#          app/tools/salary.py (salary searches through `web_search`).

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime

from langchain_core.tools import ToolException, tool
from langchain_tavily import TavilySearch
from pydantic import BaseModel, Field

from app.agent.prompts import TOOL_DESCRIPTIONS, wrap
from app.config import get_settings
from app.utils.errors import ConfigError, ExternalServiceError

MAX_RESULTS = 5
SNIPPET_CHARS = 600
NEWS_MAX_AGE_DAYS = 365
NO_KEY_MESSAGE = "Company research needs a Tavily API key. Set TAVILY_API_KEY in .env."


@dataclass
class WebResult:
    """One web search hit."""

    title: str
    url: str
    content: str
    published: str | None = None  # ISO date ("2026-05-02"), only from the news search


class ResearchCompanyArgs(BaseModel):
    company: str = Field(description="Company name, e.g. 'Lumen Health'")
    focus: str | None = Field(
        default=None, description="Optional topic, e.g. 'tech stack' or 'recent news'"
    )


def _tavily_search(
    query: str,
    max_results: int,
    include_domains: list[str] | None = None,
    topic: str = "general",
    time_range: str | None = None,
) -> dict:
    """Call Tavily and return its raw answer. Tests replace this function."""
    key = get_settings().tavily_api_key
    if key is None:
        raise ConfigError("TAVILY_API_KEY is not set", user_message=NO_KEY_MESSAGE)
    search = TavilySearch(
        tavily_api_key=key.get_secret_value(),
        max_results=max_results,
        topic=topic,
        time_range=time_range,
    )
    payload: dict = {"query": query}
    if include_domains:
        payload["include_domains"] = include_domains
    try:
        answer = search.invoke(payload)
    except ToolException:
        return {"results": []}  # Tavily found nothing
    if isinstance(answer, dict) and "error" in answer:
        raise ExternalServiceError(f"Tavily failed: {answer['error']}")
    return answer


def web_search(
    query: str, max_results: int = MAX_RESULTS, include_domains: list[str] | None = None
) -> list[WebResult]:
    """Search the web with Tavily, optionally only on some sites, e.g. ["arbeitsagentur.de"].

    Raises:
        ConfigError: no Tavily key.
        ExternalServiceError: Tavily failed.
    """
    if include_domains:
        raw = _tavily_search(query, max_results, include_domains)
    else:
        raw = _tavily_search(query, max_results)
    return [
        WebResult(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            content=str(r.get("content", ""))[:SNIPPET_CHARS],
        )
        for r in raw.get("results", [])[:max_results]
    ]


def search_company(company: str, focus: str | None = None) -> list[WebResult]:
    """Search the web for a company. Returns at most MAX_RESULTS results.

    Raises:
        ConfigError: no Tavily key.
        ExternalServiceError: Tavily failed.
    """
    topic = focus or "products customers culture tech stack"
    return web_search(f'"{company}" company {topic}', MAX_RESULTS)


def parse_published(value: object) -> date | None:
    """Tavily's `published_date` as a date, or None if missing or unreadable.

    Example: "Tue, 08 Oct 2026 14:00:00 GMT" or "2026-10-08T14:00:00Z" → date(2026, 10, 8).
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def filter_recent(
    results: list[WebResult], today: date, max_age_days: int = NEWS_MAX_AGE_DAYS
) -> list[WebResult]:
    """Drop news older than `max_age_days`. Undated items stay; the UI marks them "date unknown".

    A date more than a day in the future is treated as unreadable (published = None).
    """
    cutoff = today - timedelta(days=max_age_days)
    kept = []
    for result in results:
        if result.published is None:
            kept.append(result)
            continue
        published = date.fromisoformat(result.published)
        if published > today + timedelta(days=1):
            kept.append(WebResult(result.title, result.url, result.content, None))
        elif published >= cutoff:
            kept.append(result)
    return kept


def search_news(company: str, today: date | None = None) -> list[WebResult]:
    """News about the company from the last 12 months, newest first, undated items last.

    Tavily is asked for news from the past year (topic "news", time_range "year") and the
    query names the current year; the 12-month check is still done here, because the search
    filter is a hint, not a guarantee.

    Raises:
        ConfigError: no Tavily key.
        ExternalServiceError: Tavily failed.
    """
    today = today or datetime.now(UTC).date()
    raw = _tavily_search(
        f'"{company}" company news {today.year}', MAX_RESULTS, topic="news", time_range="year"
    )
    results = []
    for r in raw.get("results", [])[:MAX_RESULTS]:
        published = parse_published(r.get("published_date"))
        results.append(
            WebResult(
                title=str(r.get("title", "")),
                url=str(r.get("url", "")),
                content=str(r.get("content", ""))[:SNIPPET_CHARS],
                published=published.isoformat() if published else None,
            )
        )
    recent = filter_recent(results, today)
    dated = sorted((r for r in recent if r.published), key=lambda r: r.published, reverse=True)
    return dated + [r for r in recent if r.published is None]


def format_results(results: list[WebResult]) -> str:
    """Numbered results, e.g. "[1] Title (https://…, published 2026-05-02)\\nsnippet"."""
    return "\n\n".join(
        f"[{i}] {r.title} ({r.url}{f', published {r.published}' if r.published else ''})"
        f"\n{r.content}"
        for i, r in enumerate(results, 1)
    )


@tool(
    "research_company",
    description=TOOL_DESCRIPTIONS["research_company"],
    args_schema=ResearchCompanyArgs,
)
def research_company(company: str, focus: str | None = None) -> str:
    results = search_company(company, focus)
    if not results:
        return f"No web results found for {company}."
    return wrap("untrusted_web_content", format_results(results))
