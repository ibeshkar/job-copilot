# What: the `research_company` tool: searches the web (Tavily) for a company's products,
#       culture, tech stack and news, and returns the snippets marked as untrusted content.
# Why: tailoring an application needs facts about the company. Web text can contain anything,
#      including prompt injections, so it is always wrapped in <untrusted_web_content> tags.
# Used by: app/tools/registry.py (copilot chat), app/services/company.py (Company tab).

from dataclasses import dataclass

from langchain_core.tools import ToolException, tool
from langchain_tavily import TavilySearch
from pydantic import BaseModel, Field

from app.agent.prompts import TOOL_DESCRIPTIONS, wrap
from app.config import get_settings
from app.utils.errors import ConfigError, ExternalServiceError

MAX_RESULTS = 5
SNIPPET_CHARS = 600
NO_KEY_MESSAGE = "Company research needs a Tavily API key. Set TAVILY_API_KEY in .env."


@dataclass
class WebResult:
    """One web search hit."""

    title: str
    url: str
    content: str


class ResearchCompanyArgs(BaseModel):
    company: str = Field(description="Company name, e.g. 'Lumen Health'")
    focus: str | None = Field(
        default=None, description="Optional topic, e.g. 'tech stack' or 'recent news'"
    )


def _tavily_search(query: str, max_results: int) -> dict:
    """Call Tavily and return its raw answer. Tests replace this function."""
    key = get_settings().tavily_api_key
    if key is None:
        raise ConfigError("TAVILY_API_KEY is not set", user_message=NO_KEY_MESSAGE)
    search = TavilySearch(
        tavily_api_key=key.get_secret_value(), max_results=max_results, topic="general"
    )
    try:
        answer = search.invoke({"query": query})
    except ToolException:
        return {"results": []}  # Tavily found nothing
    if isinstance(answer, dict) and "error" in answer:
        raise ExternalServiceError(f"Tavily failed: {answer['error']}")
    return answer


def search_company(company: str, focus: str | None = None) -> list[WebResult]:
    """Search the web for a company. Returns at most MAX_RESULTS results.

    Raises:
        ConfigError: no Tavily key.
        ExternalServiceError: Tavily failed.
    """
    topic = focus or "products customers culture tech stack recent news"
    raw = _tavily_search(f'"{company}" company {topic}', MAX_RESULTS)
    return [
        WebResult(
            title=str(r.get("title", "")),
            url=str(r.get("url", "")),
            content=str(r.get("content", ""))[:SNIPPET_CHARS],
        )
        for r in raw.get("results", [])[:MAX_RESULTS]
    ]


def format_results(results: list[WebResult]) -> str:
    """Numbered results, e.g. "[1] Title (https://…)\\nsnippet"."""
    return "\n\n".join(f"[{i}] {r.title} ({r.url})\n{r.content}" for i, r in enumerate(results, 1))


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
