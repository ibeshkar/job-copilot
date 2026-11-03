# What: tests for the salary estimate (app/tools/salary.py, app/services/salary.py), offline.
# Why: the range must come from Python, not the LLM: figures are only used if their number is
#      in a real source, they're normalised to yearly gross EUR, outliers are dropped, and with
#      too little data we refuse to guess. The tool, cache and error messages are tested with a
#      fake Tavily search and a scripted LLM.
# Used by: pytest.

from contextlib import contextmanager

import pytest
from pydantic import SecretStr

from app.agent.state import (
    JobPosting,
    NormalisedSalary,
    SalaryDataPoint,
    SalaryExplanation,
    SalaryExtraction,
)
from app.config import get_settings
from app.db import repo
from app.memory import store
from app.services import applications
from app.services import salary as salary_service
from app.tools import registry, salary
from app.tools.company import WebResult
from app.utils.errors import ExternalServiceError
from tests.fakes import FakeStructuredLLM

STEPSTONE = "https://stepstone.example/gehalt/ml-engineer-berlin"
GLASSDOOR = "https://glassdoor.example/ml-engineer-berlin"
ENTGELTATLAS = "https://web.arbeitsagentur.de/entgeltatlas/ml-engineer"


def point(amount=55000, url=STEPSTONE, **extra) -> SalaryDataPoint:
    fields = {
        "amount": amount,
        "currency": "EUR",
        "period": "yearly",
        "basis": "gross",
        "role": "Machine Learning Engineer",
        "location": "Berlin",
        "source_url": url,
    }
    return SalaryDataPoint(**(fields | extra))


def usable(yearly, relevant=True, from_posting=False) -> NormalisedSalary:
    return NormalisedSalary(
        point=point(yearly), yearly_gross=yearly, relevant=relevant, from_posting=from_posting
    )


# ---------------------------------------------------------------- normalisation


def test_numbers_are_read_with_separators_and_k() -> None:
    assert salary.numbers_in("45.000 - 55k € brutto, 25,50 €/h, 60 000 EUR") == [
        45000,
        55000,
        25.5,
        60000,
    ]


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        ({}, 55000),
        ({"amount": 4500, "period": "monthly"}, 54000),
        ({"amount": 30, "period": "hourly"}, 62400),  # 30 × 40 h × 52 weeks
        ({"amount": 50000, "currency": "chf"}, 53000),
        ({"amount": 31000, "basis": "net"}, 50000),  # rough net → gross
    ],
)
def test_figures_become_yearly_gross_eur(extra, expected) -> None:
    p = point(**({"amount": 55000} | extra))
    assert salary.to_yearly_gross(p) == pytest.approx(expected, rel=0.001)


def test_unknown_currency_is_not_converted() -> None:
    assert salary.to_yearly_gross(point(55000, currency="XYZ")) is None


SOURCES = {
    STEPSTONE: WebResult("ML Engineer Gehalt Berlin", STEPSTONE, "Durchschnitt: 62.500 € brutto")
}


@pytest.mark.parametrize(
    ("p", "reason"),
    [
        (point(62500, url="https://made-up.example"), "source is not one of the search results"),
        (point(90000), "number not found in the source"),  # the LLM's number isn't in the text
        (point(62.5), "number not found in the source"),
        (point(62500, currency="XYZ"), "unknown currency XYZ"),
    ],
)
def test_figures_without_a_real_source_are_dropped(p, reason) -> None:
    checked = salary.check_point(p, SOURCES, None, "ML Engineer", ["berlin"])

    assert checked.dropped_reason == reason
    assert checked.yearly_gross is None


def test_a_checked_figure_is_normalised_and_relevant() -> None:
    checked = salary.check_point(
        point(62500), SOURCES, None, "Machine Learning Engineer", ["berlin"]
    )

    assert (checked.dropped_reason, checked.yearly_gross, checked.relevant) == (None, 62500, True)
    other_city = point(62500, location="Munich")
    assert not salary.check_point(other_city, SOURCES, None, "ML Engineer", ["berlin"]).relevant


@pytest.mark.parametrize(
    ("role", "seniority", "expected"),
    [
        ("Python Developer", "mid", True),  # no level in the source: fits any level
        ("Junior Python Developer", "mid", False),
        ("Senior Python Developer", "lead", True),
        ("Junior Python Developer", "junior", True),
        ("Java Developer", "mid", True),  # shares "developer"
        ("Data Analyst", "mid", False),
    ],
)
def test_relevance_compares_role_place_and_level(role, seniority, expected) -> None:
    p = point(role=role, location="Berlin, Germany")

    assert salary.is_relevant(p, "Python Developer (m/w/d)", ["berlin"], seniority) is expected


def test_implausible_amounts_are_dropped() -> None:
    sources = {STEPSTONE: WebResult("t", STEPSTONE, "Umsatz 2 Mio, Gehalt 4.000")}
    checked = salary.check_point(point(4000), sources, None, "ML Engineer", ["berlin"])

    assert checked.dropped_reason == "implausible amount"  # 4,000 a year is a misread figure


# ---------------------------------------------------------------- range, outliers, confidence


def test_range_is_quartiles_rounded_to_500() -> None:
    assert salary.salary_range([48000, 52000, 55000, 60000]) == (51000, 53500, 56000)
    assert salary.salary_range([61234]) == (61000, 61000, 61000)


def test_outliers_are_dropped_but_posting_figures_never() -> None:
    points = [usable(v) for v in (50000, 52000, 54000, 56000, 250000)]
    points.append(usable(150000, from_posting=True))

    marked = salary.drop_outliers(points)

    assert [p.dropped_reason for p in marked] == [None, None, None, None, "outlier", None]


def test_with_few_figures_nothing_is_an_outlier() -> None:
    points = [usable(v) for v in (50000, 52000, 200000)]

    assert all(p.dropped_reason is None for p in salary.drop_outliers(points))


def test_not_enough_data_instead_of_a_guess() -> None:
    one = [usable(55000), usable(90000).model_copy(update={"dropped_reason": "outlier"})]

    estimate = salary.build_estimate(one, posting_salary=None, queries=["q"])

    assert estimate.status == "not_enough_data"
    assert (estimate.low, estimate.typical, estimate.high, estimate.confidence) == (None,) * 4


def test_the_posting_salary_alone_is_enough() -> None:
    estimate = salary.build_estimate(
        [usable(60000, from_posting=True)], posting_salary="60.000 €", queries=[]
    )

    assert (estimate.status, estimate.low, estimate.high) == ("ok", 60000, 60000)
    assert estimate.confidence == "medium"


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        ([usable(v) for v in range(50000, 56000, 1000)], "high"),  # 6 relevant
        ([usable(v) for v in (50000, 52000, 54000)], "medium"),
        ([usable(50000), usable(52000), usable(54000, relevant=False)], "low"),
    ],
)
def test_confidence_counts_relevant_figures(points, expected) -> None:
    assert salary.confidence(points) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("55-65k EUR", (55000, 65000)),
        ("4.500 € per month", (54000, 54000)),
        ("about 60,000", (60000, 60000)),
        ("", None),
        ("negotiable", None),
    ],
)
def test_parse_expectation(text, expected) -> None:
    assert salary.parse_expectation(text) == expected


def test_range_uses_only_relevant_figures_when_there_are_enough() -> None:
    points = [usable(60000), usable(64000), usable(36000, relevant=False)]

    estimate = salary.build_estimate(points, None, [])

    assert (estimate.low, estimate.high) == (61000, 63000)  # the 36k junior figure is ignored


def test_expectation_position() -> None:
    estimate = salary.build_estimate([usable(v) for v in (50000, 55000, 60000)], None, [])

    assert salary.expectation_position((40000, 44000), estimate) == "below"
    assert salary.expectation_position((52000, 58000), estimate) == "within"
    assert salary.expectation_position((70000, 80000), estimate) == "above"
    assert salary.expectation_position(None, estimate) == "unknown"


# ---------------------------------------------------------------- the pipeline (mocked)

POSTING = JobPosting(
    title="Machine Learning Engineer (m/w/d)",
    company="Lumen Health",
    location="Berlin, Germany",
    remote_policy="hybrid",
    seniority="mid",
    language_requirements=[],
    visa_sponsorship="unknown",
    salary_range=None,
    summary="ML for clinics.",
    is_job_posting=True,
    raw_text="…",
)
RESULTS = {
    STEPSTONE: WebResult("ML Engineer Gehalt Berlin", STEPSTONE, "Spanne 58.000 bis 72.000 €"),
    GLASSDOOR: WebResult("ML Engineer salaries", GLASSDOOR, "Median €65k, Berlin"),
    ENTGELTATLAS: WebResult("Entgeltatlas", ENTGELTATLAS, "Median 5.400 € brutto/Monat"),
}
EXTRACTION = SalaryExtraction(
    data_points=[
        point(58000),
        point(72000),
        point(65000, url=GLASSDOOR),
        point(5400, url=ENTGELTATLAS, period="monthly", location="Deutschland"),
        point(95000, url="https://invented.example"),  # not a search result → dropped
    ]
)
EXPLANATION = SalaryExplanation(
    factors_up=["Berlin pays well for ML."],
    factors_down=["Mid-level role."],
    expectation_comparison="Your expectation sits inside the range.",
    negotiation_tips=["Ask for the salary band early.", "Mention your RAG project."],
)


@pytest.fixture
def salary_job(app_db, monkeypatch):
    """A job with an analysed posting, a Tavily key, a fake search and fixed skills."""
    monkeypatch.setattr(get_settings(), "tavily_api_key", SecretStr("tvly-test"))
    with repo.session_scope() as session:
        job = repo.create_job(session, title=POSTING.title, company=POSTING.company)
        repo.update_job(session, job.id, posting_json=POSTING.model_dump_json())
    searches = []

    def fake_search(query, max_results, include_domains=None):
        searches.append((query, include_domains))
        if include_domains:
            return [RESULTS[ENTGELTATLAS]]
        return [RESULTS[STEPSTONE], RESULTS[GLASSDOOR]]

    monkeypatch.setattr(salary, "web_search", fake_search)
    monkeypatch.setattr(salary, "_job_skills", lambda job_id: ["Python", "PyTorch"])
    store.save_profile(
        store.Profile(
            name="Alex",
            email="alex@example.com",
            target_roles=["AI Engineer"],
            salary_expectations="60-68k EUR",
        )
    )
    return job.id, searches


def test_estimate_uses_only_checked_figures_and_python_maths(salary_job) -> None:
    job_id, searches = salary_job
    llm = FakeStructuredLLM({SalaryExtraction: EXTRACTION, SalaryExplanation: EXPLANATION})

    estimate = salary.estimate_for_job(job_id, llm=llm)

    # 3 searches for a German job, the last one only on the Bundesagentur's site.
    assert len(searches) == 3 and searches[2][1] == ["arbeitsagentur.de"]
    assert "Entgeltatlas" in searches[2][0] and "(m/w/d)" not in searches[0][0]
    # Search results reach the LLM only inside the untrusted-content delimiters.
    prompt = llm.calls[0][1][-1].content
    assert "<untrusted_web_content>" in prompt and STEPSTONE in prompt
    # 58k, 72k, 65k and 5.4k × 12 = 64.8k are used; the invented source is dropped.
    assert sorted(p.yearly_gross for p in estimate.usable) == [58000, 64800, 65000, 72000]
    assert estimate.data_points[-1].dropped_reason == "source is not one of the search results"
    assert (estimate.low, estimate.typical, estimate.high) == (63000, 65000, 67000)
    assert estimate.confidence == "medium"
    assert (estimate.expectation, estimate.expectation_position) == ("60-68k EUR", "within")
    assert estimate.explanation == EXPLANATION
    # The explanation prompt gets the Python numbers and the expectation, not the raw web text.
    explain_prompt = llm.calls[1][1][-1].content
    assert "typical 65000" in explain_prompt and "within" in explain_prompt
    assert STEPSTONE not in explain_prompt
    assert salary.saved_estimate(job_id) == estimate  # cached


def test_not_enough_data_skips_the_explanation(salary_job) -> None:
    job_id, _ = salary_job
    llm = FakeStructuredLLM({SalaryExtraction: SalaryExtraction(data_points=[point(58000)])})

    estimate = salary.estimate_for_job(job_id, llm=llm)

    assert estimate.status == "not_enough_data"
    assert llm.schemas_called() == ["SalaryExtraction"]
    result = salary.describe_estimate(job_id, estimate)
    assert "not enough data" in result and "won't guess" in result


def test_the_tool_uses_the_cache_and_refresh_searches_again(salary_job, monkeypatch) -> None:
    job_id, searches = salary_job
    llm = FakeStructuredLLM({SalaryExtraction: EXTRACTION, SalaryExplanation: EXPLANATION})
    monkeypatch.setattr(salary, "get_llm", lambda: llm)

    first = salary.estimate_salary.invoke({"job_id": job_id})
    again = salary.estimate_salary.invoke({"job_id": job_id})
    assert len(searches) == 3  # the second call read the cache
    salary.estimate_salary.invoke({"job_id": job_id, "refresh": True})

    assert len(searches) == 6
    assert first == again
    assert "Range (yearly gross, EUR): 63,000 – 67,000, typical 65,000." in first
    assert "Estimate only, not financial advice." in first
    assert "<untrusted_web_content>\nSources:" in first


def test_the_service_tracks_the_action_and_reports_friendly_errors(salary_job, monkeypatch):
    job_id, _ = salary_job
    actions = []

    @contextmanager
    def fake_track_run(action, **extra):
        actions.append((action, extra))
        yield

    def down(*args, **kwargs):
        raise ExternalServiceError("Tavily failed")

    monkeypatch.setattr(salary_service, "track_run", fake_track_run)
    monkeypatch.setattr(salary, "web_search", down)

    estimate, message = salary_service.run_estimate(job_id)

    assert estimate is None
    assert message == "Web search isn't available right now. Please try again later."
    assert actions == [("estimate_salary", {"job_id": job_id})]


def test_without_a_tavily_key_the_message_says_how_to_fix_it(salary_job, monkeypatch) -> None:
    job_id, _ = salary_job
    monkeypatch.setattr(get_settings(), "tavily_api_key", None)

    assert salary_service.run_estimate(job_id) == (None, salary.NO_KEY_MESSAGE)
    assert "salary" not in registry.default_toggles()


def test_a_job_without_analysis_is_refused(app_db, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "tavily_api_key", SecretStr("tvly-test"))
    with repo.session_scope() as session:
        job_id = repo.create_job(session, title="New job", company="?").id

    assert salary_service.run_estimate(job_id) == (None, salary.NOT_ANALYSED_MESSAGE)


def test_board_badge_and_deleting_the_job(salary_job, fake_app) -> None:
    job_id, _ = salary_job
    llm = FakeStructuredLLM({SalaryExtraction: EXTRACTION, SalaryExplanation: EXPLANATION})
    salary.estimate_for_job(job_id, llm=llm)

    card = applications.list_board()["saved"][0]
    assert card.salary is not None and card.salary.typical == 65000

    applications.delete_job(job_id)
    with repo.session_scope() as session:
        assert repo.salary_estimates(session) == {}
