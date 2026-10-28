# What: the `search_jobs` tool: finds open job ads on Adzuna by keywords, location and country.
# Why: "What should I apply to next?" needs real openings. Adzuna has a free API and covers
#      Germany and many other EU countries. The tool is off unless both Adzuna keys are set.
# Used by: app/tools/registry.py (copilot chat).

from dataclasses import dataclass

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import TOOL_DESCRIPTIONS, wrap
from app.config import get_settings
from app.utils.errors import ConfigError, ExternalServiceError, GuardBlockedError

ADZUNA_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/1"
# Countries Adzuna supports. Checking this also stops odd values ending up in the URL path.
COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb",
    "in", "it", "mx", "nl", "nz", "pl", "sg", "us", "za",
}  # fmt: skip
RESULTS = 5
TIMEOUT_SECONDS = 10
NO_KEYS_MESSAGE = "Job search needs Adzuna keys. Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env."


@dataclass
class JobAd:
    """One job ad from Adzuna."""

    title: str
    company: str
    location: str
    salary: str
    url: str
    created: str


class SearchJobsArgs(BaseModel):
    keywords: str = Field(description="Job title or skills, e.g. 'junior AI engineer python'")
    location: str | None = Field(default=None, description="City or region, e.g. 'Berlin'")
    country: str = Field(default="de", description="2-letter country code, e.g. de, gb, nl")


def _get(url: str, params: dict) -> dict:
    """One GET request to Adzuna. Tests replace this function."""
    response = httpx.get(url, params=params, timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def _salary(ad: dict) -> str:
    low, high = ad.get("salary_min"), ad.get("salary_max")
    if not low and not high:
        return "not stated"
    return f"{int(low or high):,}–{int(high or low):,} per year"


def search_adzuna(keywords: str, location: str | None = None, country: str = "de") -> list[JobAd]:
    """Search Adzuna for job ads.

    Raises:
        ConfigError: the Adzuna keys are missing.
        GuardBlockedError: unsupported country code.
        ExternalServiceError: Adzuna failed or timed out.
    """
    settings = get_settings()
    if not settings.has_adzuna_keys:
        raise ConfigError("Adzuna keys not set", user_message=NO_KEYS_MESSAGE)
    country = country.strip().lower()
    if country not in COUNTRIES:
        message = f"Adzuna doesn't cover '{country}'. Try one of: {', '.join(sorted(COUNTRIES))}."
        raise GuardBlockedError(message, user_message=message)

    params = {
        "app_id": settings.adzuna_app_id,
        "app_key": settings.adzuna_app_key.get_secret_value(),
        "what": keywords,
        "results_per_page": RESULTS,
        "content-type": "application/json",
    }
    if location:
        params["where"] = location
    try:
        data = _get(ADZUNA_URL.format(country=country), params)
    except (httpx.HTTPError, ValueError) as exc:
        raise ExternalServiceError(f"Adzuna failed: {exc}") from exc
    return [
        JobAd(
            title=ad.get("title", ""),
            company=(ad.get("company") or {}).get("display_name", "unknown"),
            location=(ad.get("location") or {}).get("display_name", "unknown"),
            salary=_salary(ad),
            url=ad.get("redirect_url", ""),
            created=str(ad.get("created", ""))[:10],
        )
        for ad in data.get("results", [])[:RESULTS]
    ]


@tool("search_jobs", description=TOOL_DESCRIPTIONS["search_jobs"], args_schema=SearchJobsArgs)
def search_jobs(keywords: str, location: str | None = None, country: str = "de") -> str:
    ads = search_adzuna(keywords, location, country)
    if not ads:
        return f"No job ads found for '{keywords}'."
    lines = [
        f"- {a.title} at {a.company} ({a.location}), salary {a.salary}, posted {a.created}: {a.url}"
        for a in ads
    ]
    return wrap("untrusted_web_content", "\n".join(lines))
