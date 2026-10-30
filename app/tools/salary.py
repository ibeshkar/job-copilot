# What: the `estimate_salary` tool and the pipeline behind the 💰 Salary tab: 2-3 web searches
#       for salary data, an LLM that only *extracts* figures from the results, Python that
#       checks, normalises (yearly gross EUR), drops outliers and computes the range, then an
#       LLM explanation (factors, comparison with the user's expectation, negotiation tips).
#       The result is cached per job in the SalaryCache table.
# Why: a salary number is easy to hallucinate and costly to get wrong. The LLM reads messy web
#      text well but is bad at arithmetic and likes to fill gaps, so every number in the range
#      comes from a figure Python found in a real source, and with too little data we say so
#      instead of guessing (DECISIONS.md D80, D81).
# Used by: app/tools/registry.py (copilot chat), app/services/salary.py (Salary tab).

import re
import statistics
from datetime import UTC, datetime
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.llm import get_llm, invoke_structured
from app.agent.prompts import (
    TOOL_DESCRIPTIONS,
    salary_explanation_messages,
    salary_extraction_messages,
    wrap,
)
from app.agent.state import (
    JobPosting,
    NormalisedSalary,
    SalaryDataPoint,
    SalaryEstimate,
    SalaryExplanation,
    SalaryExtraction,
)
from app.config import get_settings
from app.db import repo
from app.memory import store
from app.services.observability import track_run
from app.tools.company import WebResult, format_results, web_search
from app.utils.errors import ConfigError, ExternalServiceError, GuardBlockedError
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

CURRENCY = "EUR"
# Approximate value of 1 unit in EUR (October 2026). Precise enough for a salary range;
# update it now and then.
EUR_RATES = {
    "EUR": 1.0, "CHF": 1.06, "GBP": 1.17, "USD": 0.92, "PLN": 0.23, "CZK": 0.04,
    "SEK": 0.087, "DKK": 0.134, "NOK": 0.085, "HUF": 0.0025,
}  # fmt: skip
PERIOD_FACTOR = {"yearly": 1, "monthly": 12, "hourly": 40 * 52}  # 40 h × 52 weeks
NET_TO_GROSS = 1 / 0.62  # rough: a single employee in Germany keeps about 62% of gross
PLAUSIBLE_YEARLY = (10_000, 400_000)  # EUR gross; outside this is a misread number
MIN_POINTS = 2  # fewer usable points (and no salary in the posting) → "not enough data"
OUTLIER_MIN_POINTS = 4  # Tukey's fences need a few points to mean anything
RESULTS_PER_SEARCH = 5
ROUND_TO = 500
ENTGELTATLAS_DOMAINS = ["arbeitsagentur.de"]
GERMAN_PLACES = (
    "germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne",
    "köln", "stuttgart", "düsseldorf", "leipzig", "dresden", "hannover", "nuremberg",
    "nürnberg", "bonn", "essen", "dortmund", "bremen", "karlsruhe", "mannheim",
)  # fmt: skip
POSTING_SOURCE = "job posting"
NO_KEY_MESSAGE = "Salary estimates need a Tavily API key. Set TAVILY_API_KEY in .env."
NOT_ANALYSED_MESSAGE = "Finish the job's fit analysis first: I need its title and location."
DISCLAIMER = "Estimate only, not financial advice."

# Words that say nothing about the role, ignored when comparing job titles.
_TITLE_NOISE = {
    "m", "w", "d", "f", "x", "and", "und", "or", "the", "of", "in", "for", "a",
    "senior", "junior", "mid", "lead", "level", "all", "genders",
}  # fmt: skip
# "55.000", "55,000", "55 000" (thousands) or "4500" / "25,50"; optional "k" / "Tsd".
_NUMBER = re.compile(
    r"(\d{1,3}(?:[.,'\u00a0 ]\d{3})+|\d+(?:[.,]\d+)?)\s*(k|tsd\.?|tausend)?\b", re.IGNORECASE
)
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,'\u00a0 ]\d{3})+")
# Seniority words in a source's role, grouped into the levels we compare.
_LEVEL_WORDS = {
    "junior": {"junior", "jr", "entry", "graduate", "trainee", "intern", "werkstudent"},
    "senior": {"senior", "sr", "lead", "principal", "staff", "head"},
}
_JOB_LEVEL = {
    "intern": "junior",
    "junior": "junior",
    "mid": "mid",
    "senior": "senior",
    "lead": "senior",
}
_CURRENCY_MARKS = {"USD": ("$", "usd"), "GBP": ("£", "gbp"), "CHF": ("chf",)}


# ---------------------------------------------------------------- pure Python: numbers


def numbers_in(text: str) -> list[float]:
    """Every number in a text, reading thousand separators and "k".

    Example: "45.000 - 55k € brutto, 25,50 €/h" → [45000.0, 55000.0, 25.5]
    """
    values = []
    for digits, thousand in _NUMBER.findall(text):
        if _THOUSANDS.fullmatch(digits):
            value = float(re.sub(r"[.,'\u00a0 ]", "", digits))
        else:
            value = float(digits.replace(",", "."))
        values.append(value * 1000 if thousand else value)
    return values


def to_yearly_gross(point: SalaryDataPoint) -> float | None:
    """The figure as yearly gross EUR, or None for an unknown currency.

    Example: 4,500 CHF monthly gross → 4500 × 12 × 1.06 = 57,240.
    """
    rate = EUR_RATES.get(point.currency.strip().upper())
    if rate is None:
        return None
    yearly = point.amount * PERIOD_FACTOR[point.period] * rate
    return yearly * NET_TO_GROSS if point.basis == "net" else yearly


def _title_words(text: str) -> set[str]:
    return set(re.findall(r"[a-zäöüß0-9+#]+", text.lower())) - _TITLE_NOISE


def _level(role: str) -> str | None:
    words = set(re.findall(r"[a-z]+", role.lower()))
    return next((level for level, marks in _LEVEL_WORDS.items() if words & marks), None)


def is_relevant(
    point: SalaryDataPoint, job_title: str, places: list[str], seniority: str = "unknown"
) -> bool:
    """True if the figure is for a similar role in the job's place, at the job's level.

    Similar role = a shared title word ("Python Developer" ~ "Backend Python Engineer").
    A figure without a level ("Python Developer") fits any level; "Junior …" doesn't fit a
    mid or senior job.
    """
    same_role = bool(_title_words(point.role) & _title_words(job_title))
    location = point.location.lower()
    same_place = bool(location) and any(place in location for place in places)
    level = _level(point.role)
    job_level = _JOB_LEVEL.get(seniority) or _level(job_title)
    same_level = level is None or job_level is None or level == job_level
    return same_role and same_place and same_level


def check_point(
    point: SalaryDataPoint,
    sources: dict[str, WebResult],
    posting_salary: str | None,
    job_title: str,
    places: list[str],
    seniority: str = "unknown",
) -> NormalisedSalary:
    """Check one extracted figure and normalise it. Dropped points keep a reason.

    A figure is only used if its source is one of our search results (or the posting) and
    the number really appears in that source's text, so the LLM can't invent data points.
    """
    from_posting = point.source_url.strip().lower() == POSTING_SOURCE
    result = None if from_posting else sources.get(point.source_url.strip())
    title = "Job posting" if from_posting else (result.title if result else "")

    def dropped(reason: str) -> NormalisedSalary:
        return NormalisedSalary(
            point=point, source_title=title, from_posting=from_posting, dropped_reason=reason
        )

    if from_posting:
        text = posting_salary or ""
    elif result is None:
        return dropped("source is not one of the search results")
    else:
        text = f"{result.title} {result.content}"
    if not any(abs(n - point.amount) <= point.amount * 0.01 for n in numbers_in(text)):
        return dropped("number not found in the source")
    yearly = to_yearly_gross(point)
    if yearly is None:
        return dropped(f"unknown currency {point.currency}")
    if not PLAUSIBLE_YEARLY[0] <= yearly <= PLAUSIBLE_YEARLY[1]:
        return dropped("implausible amount")
    return NormalisedSalary(
        point=point,
        source_title=title,
        yearly_gross=round(yearly),
        relevant=from_posting or is_relevant(point, job_title, places, seniority),
        from_posting=from_posting,
    )


def drop_outliers(points: list[NormalisedSalary]) -> list[NormalisedSalary]:
    """Mark market figures outside Tukey's fences (Q1 − 1.5·IQR, Q3 + 1.5·IQR) as outliers.

    Needs at least OUTLIER_MIN_POINTS usable figures; with fewer, the quartiles mean nothing
    and all are kept. The posting's own figures are never outliers: it's what the employer
    says it pays.
    """
    values = [p.yearly_gross for p in points if p.dropped_reason is None]
    if len(values) < OUTLIER_MIN_POINTS:
        return points
    q1, _, q3 = statistics.quantiles(values, n=4, method="inclusive")
    low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    return [
        p.model_copy(update={"dropped_reason": "outlier"})
        if p.dropped_reason is None and not p.from_posting and not low <= p.yearly_gross <= high
        else p
        for p in points
    ]


def salary_range(values: list[float]) -> tuple[int, int, int]:
    """(25th percentile, median, 75th percentile), rounded to 500.

    Example: [48000, 52000, 55000, 60000] → (51000, 53500, 56000)
    """

    def rounded(value: float) -> int:
        return int(round(value / ROUND_TO) * ROUND_TO)

    if len(values) == 1:
        return rounded(values[0]), rounded(values[0]), rounded(values[0])
    q1, median, q3 = statistics.quantiles(values, n=4, method="inclusive")
    return rounded(q1), rounded(median), rounded(q3)


def confidence(points: list[NormalisedSalary]) -> Literal["high", "medium", "low"]:
    """How much to trust the range, from the number of usable and relevant figures.

    high: 5+ figures for this role and place, or the posting's salary plus 3 relevant ones.
    medium: 3+ relevant figures, or the posting states a salary. low: anything less.
    """
    usable = [p for p in points if p.dropped_reason is None]
    relevant = sum(p.relevant and not p.from_posting for p in usable)
    has_posting = any(p.from_posting for p in usable)
    if relevant >= 5 or (has_posting and relevant >= 3):
        return "high"
    if relevant >= 3 or has_posting:
        return "medium"
    return "low"


def build_estimate(
    points: list[NormalisedSalary], posting_salary: str | None, queries: list[str]
) -> SalaryEstimate:
    """The range and confidence, or "not_enough_data". All numbers are computed here.

    The range uses only the relevant figures (same role, place and level) when there are at
    least MIN_POINTS of them, otherwise all usable figures (with a lower confidence).
    """
    points = drop_outliers(points)
    usable = [p for p in points if p.dropped_reason is None]
    has_posting = any(p.from_posting for p in usable)
    common = {"posting_salary": posting_salary, "data_points": points, "queries": queries}
    if len(usable) < MIN_POINTS and not has_posting:
        return SalaryEstimate(status="not_enough_data", **common)
    relevant = [p for p in usable if p.relevant]
    basis = relevant if len(relevant) >= MIN_POINTS else usable
    low, typical, high = salary_range([p.yearly_gross for p in basis])
    return SalaryEstimate(
        status="ok",
        low=low,
        typical=typical,
        high=high,
        confidence=confidence(points),
        **common,
    )


def parse_expectation(text: str | None) -> tuple[float, float] | None:
    """The user's expectation from the profile as (min, max) yearly gross EUR, or None.

    Examples: "55-65k EUR" → (55000, 65000); "4.500 € per month" → (54000, 54000).
    """
    if not text:
        return None
    values = numbers_in(text)
    if re.search(r"\d\s*k\b", text, re.IGNORECASE):  # "55-65k": the k belongs to both
        values = [v * 1000 if v < 1000 else v for v in values]
    lowered = text.lower()
    period = "yearly"
    if re.search(r"month|monat|/\s*m\b|p\.?m\.?", lowered):
        period = "monthly"
    elif re.search(r"hour|stunde|/\s*h\b", lowered):
        period = "hourly"
    currency = next(
        (code for code, marks in _CURRENCY_MARKS.items() if any(m in lowered for m in marks)),
        CURRENCY,
    )
    yearly = [
        y
        for v in values
        if (y := to_yearly_gross(SalaryDataPoint(
            amount=v, currency=currency, period=period, basis="gross",
            role="", location="", source_url="",
        ))) and PLAUSIBLE_YEARLY[0] <= y <= PLAUSIBLE_YEARLY[1]
    ]  # fmt: skip
    return (min(yearly), max(yearly)) if yearly else None


def expectation_position(
    expectation: tuple[float, float] | None, estimate: SalaryEstimate
) -> Literal["below", "within", "above", "unknown"]:
    """Where the middle of the user's expectation falls compared with the low–high range."""
    if expectation is None or estimate.status != "ok":
        return "unknown"
    middle = sum(expectation) / 2
    if middle < estimate.low:
        return "below"
    return "above" if middle > estimate.high else "within"


# ---------------------------------------------------------------- the job and the searches


def in_germany(location: str | None) -> bool:
    lowered = (location or "").lower()
    return any(place in lowered for place in GERMAN_PLACES)


def job_places(location: str | None) -> list[str]:
    """Lower-case place names a relevant figure may mention, e.g. ["berlin", "germany", …]."""
    places = [part.strip().lower() for part in (location or "").split(",") if part.strip()]
    if in_germany(location):
        places += ["germany", "deutschland"]
    return places


def clean_title(title: str) -> str:
    """The title without gender tags and brackets: "ML Engineer (m/w/d)" → "ML Engineer"."""
    return " ".join(re.sub(r"\(.*?\)|\[.*?\]", " ", title).split())


def salary_queries(job: JobPosting) -> list[tuple[str, list[str] | None]]:
    """2-3 searches as (query, only these sites or None). Germany adds the Entgeltatlas."""
    role = clean_title(job.title)
    place = job.location or "Germany"
    level = "" if job.seniority == "unknown" else f"{job.seniority} "
    year = datetime.now(UTC).year
    queries: list[tuple[str, list[str] | None]] = [
        (f"{level}{role} salary {place} {year}", None),
    ]
    if in_germany(job.location):
        queries += [
            (f"{role} Gehalt {place} brutto pro Jahr", None),
            (f"Entgeltatlas {role} Gehalt Median", ENTGELTATLAS_DOMAINS),
        ]
    else:
        queries.append((f"average {role} salary {place} gross per year", None))
    return queries


def run_searches(queries: list[tuple[str, list[str] | None]]) -> list[WebResult]:
    """Run the searches and merge the results (each URL once). One failed search is fine.

    Raises:
        ExternalServiceError: every search failed.
    """
    results: dict[str, WebResult] = {}
    failures = 0
    for query, domains in queries:
        try:
            found = web_search(query, RESULTS_PER_SEARCH, domains)
        except ExternalServiceError:
            failures += 1
            log_exception(logger, "Salary search failed", query=query)
            continue
        for result in found:
            results.setdefault(result.url, result)
    if failures == len(queries):
        raise ExternalServiceError(
            "All salary searches failed",
            user_message="Web search isn't available right now. Please try again later.",
        )
    return list(results.values())


def _job_posting(job_id: int) -> tuple[JobPosting, str]:
    """The job's structured posting and company. Raises if the analysis hasn't run yet."""
    with repo.session_scope() as session:
        row = repo.get_job(session, job_id)
    if not row.posting_json:
        raise GuardBlockedError(f"Job {job_id} has no posting yet", NOT_ANALYSED_MESSAGE)
    return JobPosting.model_validate_json(row.posting_json), row.company


def _job_skills(job_id: int) -> list[str]:
    """The skills the posting asks for (from the fit analysis), or [] if unavailable."""
    from app.services.applications import get_workflow_view  # avoids an import cycle

    try:
        requirements = get_workflow_view(job_id).requirements
    except Exception:  # skills only add context; the estimate works without them
        log_exception(logger, "Could not read the job's requirements", job_id=job_id)
        return []
    return [r.text for r in requirements if r.category == "skill"][:10]


# ---------------------------------------------------------------- the pipeline


def _job_block(job: JobPosting, company: str, skills: list[str]) -> str:
    languages = ", ".join(f"{lang.language} {lang.level}" for lang in job.language_requirements)
    return "\n".join(
        [
            f"Title: {job.title}",
            f"Company: {company}",
            f"Location: {job.location or 'unknown'} ({job.remote_policy})",
            f"Seniority: {job.seniority}",
            f"Languages: {languages or 'none stated'}",
            f"Skills: {', '.join(skills) or 'unknown'}",
            f"Salary in the posting: {job.salary_range or 'not stated'}",
        ]
    )


def _estimate_block(estimate: SalaryEstimate) -> str:
    usable = estimate.usable
    return (
        f"Yearly gross {estimate.currency}: low {estimate.low}, typical {estimate.typical}, "
        f"high {estimate.high}. Confidence: {estimate.confidence}. Based on {len(usable)} data "
        f"points, {sum(p.relevant for p in usable)} for this role and place."
    )


def _candidate_block(expectation: str | None, position: str) -> str:
    profile = store.get_profile()
    lines = [
        f"Expected salary: {expectation or 'not given'}",
        f"Expectation compared with the range: {position}",
    ]
    if profile:
        lines += [
            f"Seniority: {profile.seniority}",
            f"Languages: {', '.join(profile.languages) or 'not given'}",
        ]
    return "\n".join(lines)


def estimate_for_job(job_id: int, llm: BaseChatModel | None = None) -> SalaryEstimate:
    """Estimate the salary for a job, save it and return it.

    Steps: searches → LLM extracts figures → Python checks and normalises them, drops
    outliers and computes the range → LLM explains it. Without enough data, the result is
    "not_enough_data" and the explanation step is skipped.

    Raises:
        ConfigError: no Tavily or OpenRouter key.
        GuardBlockedError: the job hasn't been analysed yet.
        ExternalServiceError: every web search failed.
        LLMServiceError / StructuredOutputError: an LLM step failed.
    """
    settings = get_settings()
    if not settings.has_tavily_key:
        raise ConfigError("TAVILY_API_KEY is not set", user_message=NO_KEY_MESSAGE)
    job, company = _job_posting(job_id)
    llm = llm or get_llm()

    queries = salary_queries(job)
    results = run_searches(queries)
    points: list[SalaryDataPoint] = []
    if results or job.salary_range:
        job_line = f"{job.title} at {company}, {job.location or 'location unknown'}"
        extraction = invoke_structured(
            llm,
            SalaryExtraction,
            salary_extraction_messages(job_line, format_results(results), job.salary_range),
        )
        points = extraction.data_points

    sources = {r.url: r for r in results}
    places = job_places(job.location)
    checked = [
        check_point(p, sources, job.salary_range, job.title, places, job.seniority) for p in points
    ]
    estimate = build_estimate(checked, job.salary_range, [q for q, _ in queries])

    profile = store.get_profile()
    expectation_text = profile.salary_expectations if profile else None
    position = expectation_position(parse_expectation(expectation_text), estimate)
    estimate = estimate.model_copy(
        update={"expectation": expectation_text, "expectation_position": position}
    )
    if estimate.status == "ok":
        explanation = invoke_structured(
            llm,
            SalaryExplanation,
            salary_explanation_messages(
                _job_block(job, company, _job_skills(job_id)),
                _estimate_block(estimate),
                _candidate_block(expectation_text, position),
            ),
        )
        estimate = estimate.model_copy(update={"explanation": explanation})

    with repo.session_scope() as session:
        repo.save_salary_estimate(session, job_id, estimate.model_dump_json())
    return estimate


def saved_estimate(job_id: int) -> SalaryEstimate | None:
    """The cached estimate for a job, or None if there isn't one (or it can't be read)."""
    with repo.session_scope() as session:
        data = repo.get_salary_estimate(session, job_id)
    if data is None:
        return None
    try:
        return SalaryEstimate.model_validate_json(data)
    except ValueError:  # saved by an older version with another shape: estimate again
        log_exception(logger, "Saved salary estimate is unreadable", job_id=job_id)
        return None


# ---------------------------------------------------------------- the copilot tool


class EstimateSalaryArgs(BaseModel):
    job_id: int = Field(description="The job id from get_applications")
    refresh: bool = Field(
        default=False, description="Search again even if an estimate is saved (costs money)"
    )


def describe_estimate(job_id: int, estimate: SalaryEstimate) -> str:
    """The estimate as text for the copilot. Source titles come from the web: untrusted."""
    head = f"Salary estimate for job {job_id} ({estimate.estimated_at:%Y-%m-%d}):"
    usable = estimate.usable
    if estimate.status != "ok":
        posting = "the posting states no salary" if not estimate.posting_salary else ""
        return (
            f"{head} not enough data. I found {len(usable)} usable salary figure(s)"
            f"{', and ' + posting if posting else ''}, so I won't guess a range. "
            "Suggest the user checks the Entgeltatlas or asks the recruiter for the band."
        )
    lines = [
        head,
        f"- Range (yearly gross, {estimate.currency}): {estimate.low:,} – {estimate.high:,}, "
        f"typical {estimate.typical:,}.",
        f"- Confidence: {estimate.confidence} ({len(usable)} data points, "
        f"{sum(p.relevant for p in usable)} for this role and place).",
        f"- Salary in the posting: {estimate.posting_salary or 'not stated'}.",
        f"- User's expectation: {estimate.expectation or 'not given'} "
        f"({estimate.expectation_position} the range).",
    ]
    if estimate.explanation:
        e = estimate.explanation
        lines += [f"- Pushes pay up: {'; '.join(e.factors_up)}"]
        lines += [f"- Pushes pay down: {'; '.join(e.factors_down)}"]
        lines += [f"- Comparison: {e.expectation_comparison}"]
        lines += [f"- Negotiation tips: {'; '.join(e.negotiation_tips)}"]
    lines.append(f"- {DISCLAIMER} Full details are in the job's 💰 Salary tab.")
    sources = "\n".join(
        f"- {p.source_title or p.point.source_url} ({p.point.source_url})"
        for p in {p.point.source_url: p for p in usable if not p.from_posting}.values()
    )
    if sources:
        lines.append(wrap("untrusted_web_content", f"Sources:\n{sources}"))
    return "\n".join(lines)


@tool(
    "estimate_salary",
    description=TOOL_DESCRIPTIONS["estimate_salary"],
    args_schema=EstimateSalaryArgs,
)
def estimate_salary(job_id: int, refresh: bool = False) -> str:
    estimate = None if refresh else saved_estimate(job_id)
    if estimate is None:
        # Its own action in the usage log, also when it runs inside a chat turn.
        with track_run("estimate_salary", job_id=job_id):
            estimate = estimate_for_job(job_id)
    return describe_estimate(job_id, estimate)
