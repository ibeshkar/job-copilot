# What: the `search_jobs` tool: finds open job ads on the Arbeitnow job board by keywords,
#       location, remote and visa sponsorship.
# Why: "What should I apply to next?" needs real openings. Arbeitnow's API is free, needs no
#      key and focuses on Germany and the EU, so the tool works out of the box. The API has no
#      search parameters, so we download the newest ads and filter them here (DECISIONS.md D79).
# Used by: app/tools/registry.py (copilot chat).

import html
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.agent.prompts import TOOL_DESCRIPTIONS, wrap
from app.utils.errors import ExternalServiceError

ARBEITNOW_URL = "https://www.arbeitnow.com/api/job-board-api"
TIMEOUT_SECONDS = 10
MAX_PAGES = 2  # 300 ads per page, newest first; enough to filter without hammering the API
MAX_LIMIT = 20
DESCRIPTION_CHARS = 300
# Arbeitnow updates hourly and rate-limits hard (HTTP 429), so pages are reused for a while.
CACHE_SECONDS = 600
DOWN_MESSAGE = (
    "The Arbeitnow job board isn't reachable right now. Please try again in a few minutes."
)


@dataclass
class JobAd:
    """One job ad from Arbeitnow, cleaned up."""

    title: str
    company: str
    location: str
    remote: bool
    url: str
    description: str  # plain text, shortened to DESCRIPTION_CHARS
    tags: list[str] = field(default_factory=list)
    posted: str = ""  # "2026-10-20"


class SearchJobsArgs(BaseModel):
    keywords: str = Field(description="Job title or skills, e.g. 'junior AI engineer python'")
    location: str | None = Field(
        default=None, description="City, e.g. 'Berlin'. Leave empty for all of Germany/EU."
    )
    remote_only: bool = Field(default=False, description="Only remote jobs")
    visa_sponsorship: bool = Field(default=False, description="Only jobs that sponsor a visa")
    limit: int = Field(default=10, ge=1, le=MAX_LIMIT, description="How many ads to return")


def _client() -> httpx.Client:
    """The HTTP client. Tests replace this with one that has a mock transport."""
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def _is_transient(exc: BaseException) -> bool:
    """Worth retrying: timeouts, connection errors, rate limits and server errors."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429 or exc.response.status_code >= 500
    return isinstance(exc, httpx.TransportError)


@retry(
    retry=retry_if_exception(_is_transient),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),  # 1s, 2s, then give up
    reraise=True,
)
def _get(params: dict) -> dict:
    """One GET request to Arbeitnow, retried on transient errors."""
    with _client() as client:
        response = client.get(ARBEITNOW_URL, params=params)
        response.raise_for_status()
        return response.json()


_cache: dict[tuple, tuple[float, dict]] = {}  # (page, visa) → (time fetched, answer)


def _fetch_page(page: int, visa_sponsorship: bool) -> dict:
    """One page of ads (300, newest first), from the cache if it's recent enough."""
    key = (page, visa_sponsorship)
    if (hit := _cache.get(key)) and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    params: dict = {"page": page}
    if visa_sponsorship:
        params["visa_sponsorship"] = "true"
    data = _get(params)
    _cache[key] = (time.monotonic(), data)
    return data


def _plain_text(markup: str) -> str:
    """HTML description → one line of plain text, shortened."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", markup))
    text = " ".join(text.split())
    return text if len(text) <= DESCRIPTION_CHARS else text[:DESCRIPTION_CHARS].rstrip() + "…"


def _posted(timestamp: object) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp), UTC).date().isoformat()  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return ""


def _to_ad(raw: dict) -> JobAd:
    return JobAd(
        title=str(raw.get("title", "")),
        company=str(raw.get("company_name") or "unknown"),
        location=str(raw.get("location") or "unknown"),
        remote=bool(raw.get("remote")),
        url=str(raw.get("url", "")),
        description=_plain_text(str(raw.get("description", ""))),
        tags=[str(t) for t in raw.get("tags") or []],
        posted=_posted(raw.get("created_at")),
    )


def _score(ad: JobAd, terms: list[str]) -> tuple[int, int]:
    """How well an ad matches: (terms in the title or tags, terms found anywhere).

    Example: "python developer" vs. "Senior .NET Developer" whose text mentions Python → (1, 2).
    Returns (0, 0) when no term is in the title or tags: a passing mention isn't enough.
    """
    headline = f"{ad.title} {' '.join(ad.tags)}".lower()
    text = f"{headline} {ad.description.lower()}"
    patterns = [rf"\b{re.escape(t)}\b" for t in terms]
    in_headline = sum(bool(re.search(p, headline)) for p in patterns)
    if not in_headline:
        return (0, 0)
    return (in_headline, sum(bool(re.search(p, text)) for p in patterns))


def search_jobs(
    keywords: str,
    location: str | None = None,
    remote_only: bool = False,
    visa_sponsorship: bool = False,
    limit: int = 10,
) -> list[JobAd]:
    """Search Arbeitnow for job ads, best matches first.

    The API returns the newest ads without search, so we fetch up to MAX_PAGES pages and
    filter by keywords, location and remote here. Ads with more keywords in the title or tags
    come first, then more keywords in the text, then newer ones. Visa sponsorship isn't in the
    ad data, so that filter is the API's own `visa_sponsorship` parameter. If a later page
    fails, the ads from the earlier pages are still returned.

    Example: search_jobs("python developer", location="Berlin", limit=5)

    Raises:
        ExternalServiceError: the first page failed or timed out, even after retries.
    """
    terms = keywords.lower().split()
    place = (location or "").strip().lower()
    limit = max(1, min(limit, MAX_LIMIT))
    scored: list[tuple[tuple[int, int], JobAd]] = []

    for page in range(1, MAX_PAGES + 1):
        try:
            data = _fetch_page(page, visa_sponsorship)
        except (httpx.HTTPError, ValueError) as exc:
            if page > 1:
                break  # keep what the earlier pages found
            raise ExternalServiceError(
                f"Arbeitnow failed: {exc}", user_message=DOWN_MESSAGE
            ) from exc

        for raw in data.get("data") or []:
            ad = _to_ad(raw)
            if remote_only and not ad.remote:
                continue
            if place and place not in ad.location.lower():
                continue
            score = _score(ad, terms)
            if score[0]:
                scored.append((score, ad))
        if not (data.get("links") or {}).get("next"):
            break

    scored.sort(key=lambda pair: (pair[0], pair[1].posted), reverse=True)
    return [ad for _, ad in scored[:limit]]


def format_ads(ads: list[JobAd]) -> str:
    """Numbered ads, e.g. "[1] Title at Company (Berlin, remote), posted 2026-10-20 ..."."""
    blocks = []
    for i, ad in enumerate(ads, 1):
        where = f"{ad.location}, remote" if ad.remote else ad.location
        tags = f"\nTags: {', '.join(ad.tags)}" if ad.tags else ""
        blocks.append(
            f"[{i}] {ad.title} at {ad.company} ({where}), posted {ad.posted}\n"
            f"{ad.url}{tags}\n{ad.description}"
        )
    return "\n\n".join(blocks)


def no_results_message(keywords: str, location: str | None) -> str:
    where = f" in {location}" if location else ""
    return (
        f"No job ads on Arbeitnow match '{keywords}'{where}. Try fewer or broader keywords, "
        "drop the location, or turn off the remote/visa filters. Arbeitnow mostly lists jobs "
        "in Germany and the EU."
    )


@tool("search_jobs", description=TOOL_DESCRIPTIONS["search_jobs"], args_schema=SearchJobsArgs)
def search_jobs_tool(
    keywords: str,
    location: str | None = None,
    remote_only: bool = False,
    visa_sponsorship: bool = False,
    limit: int = 10,
) -> str:
    ads = search_jobs(keywords, location, remote_only, visa_sponsorship, limit)
    if not ads:
        return no_results_message(keywords, location)
    # Job ads are written by strangers: data, never instructions.
    return wrap("untrusted_web_content", format_ads(ads))
