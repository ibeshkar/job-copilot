# What: tests for the pure fit functions: the score formula and the red-flag rules.
# Why: the score must be exactly reproducible (PROJECT_PLAN.md §5.2), and red flags must catch
#      the sample "poor fit" job's German C1 requirement.
# Used by: pytest.

import pytest

from app.agent.nodes.fit import (
    compute_fit_score,
    find_red_flags,
    needs_visa_sponsorship,
    parse_profile_languages,
)
from app.agent.state import JobPosting, LanguageRequirement, RedFlag, Requirement, RequirementMatch
from app.memory.store import Profile


def _req(rid: str, importance: str) -> Requirement:
    return Requirement(id=rid, text=rid, category="skill", importance=importance)


def _match(rid: str, status: str) -> RequirementMatch:
    return RequirementMatch(
        requirement_id=rid, status=status, evidence=[], explanation="", honest_positioning=None
    )


def _job(**overrides) -> JobPosting:
    base = dict(
        title="Engineer",
        company="X",
        location="Berlin",
        remote_policy="hybrid",
        seniority="junior",
        language_requirements=[],
        visa_sponsorship="unknown",
        salary_range=None,
        summary="",
        is_job_posting=True,
        raw_text="",
    )
    return JobPosting(**{**base, **overrides})


ALEX = Profile(
    name="Alex",
    seniority="junior",
    locations=["Berlin", "Remote (EU)"],
    work_authorisation="EU citizen, no visa needed in the EU",
    languages=["Czech native", "English C1", "German A2"],
)
BLOCKING = RedFlag(kind="language", message="", blocking=True)


def test_score_formula_example_from_docstring() -> None:
    reqs = [_req("R1", "must"), _req("R2", "must"), _req("R3", "nice")]
    matches = [_match("R1", "met"), _match("R2", "partial"), _match("R3", "missing")]

    # (3*1 + 3*0.5 + 1*0) / 7 = 0.643 → 64
    assert compute_fit_score(reqs, matches, []) == 64


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [(["met", "met"], 100), (["missing", "missing"], 0), (["partial", "met"], 63)],
)
def test_score_with_must_and_nice(statuses: list[str], expected: int) -> None:
    # Last case: (3*0.5 + 1*1) / 4 = 62.5, which rounds half up to 63.
    reqs = [_req("R1", "must"), _req("R2", "nice")]
    matches = [_match("R1", statuses[0]), _match("R2", statuses[1])]

    assert compute_fit_score(reqs, matches, []) == expected


def test_missing_match_counts_as_missing_and_empty_is_zero() -> None:
    assert (
        compute_fit_score([_req("R1", "must"), _req("R2", "must")], [_match("R1", "met")], []) == 50
    )
    assert compute_fit_score([], [], []) == 0


def test_blocking_flag_caps_score_at_40() -> None:
    reqs = [_req("R1", "must")]

    assert compute_fit_score(reqs, [_match("R1", "met")], [BLOCKING]) == 40
    assert compute_fit_score(reqs, [_match("R1", "partial")], [BLOCKING]) == 40
    assert compute_fit_score(reqs, [_match("R1", "missing")], [BLOCKING]) == 0


def test_parse_profile_languages() -> None:
    assert parse_profile_languages(["English C1", "German a2", "Czech native", "French"]) == {
        "english": "C1",
        "german": "A2",
        "czech": "native",
        "french": "B2",
    }
    assert parse_profile_languages(["Spanish fluent"]) == {"spanish": "C1"}


def test_german_c1_is_blocking_for_alex() -> None:
    job = _job(
        language_requirements=[LanguageRequirement(language="German", level="C1", required=True)]
    )

    flags = find_red_flags(job, ALEX)

    assert [(f.kind, f.blocking) for f in flags] == [("language", True)]


def test_language_one_level_below_is_a_warning_and_nice_to_have_is_ignored() -> None:
    job = _job(
        language_requirements=[
            LanguageRequirement(language="English", level="C2", required=True),
            LanguageRequirement(language="Spanish", level="B2", required=False),
        ]
    )

    assert [(f.kind, f.blocking) for f in find_red_flags(job, ALEX)] == [("language", False)]


def test_missing_language_is_blocking() -> None:
    job = _job(
        language_requirements=[
            LanguageRequirement(language="Polish", level="unknown", required=True)
        ]
    )

    assert find_red_flags(job, ALEX)[0].blocking


def test_visa_seniority_location_and_deal_breakers() -> None:
    needs_visa = ALEX.model_copy(update={"work_authorisation": "Needs visa sponsorship"})
    job = _job(visa_sponsorship="no", seniority="senior", remote_policy="onsite", location="Munich")

    kinds = {(f.kind, f.blocking) for f in find_red_flags(job, needs_visa, ["No on-site"])}

    assert kinds == {
        ("visa", True),
        ("seniority", False),
        ("location", False),
        ("deal_breaker", False),
    }


def test_good_fit_job_has_no_flags() -> None:
    job = _job(
        language_requirements=[
            LanguageRequirement(language="English", level="unknown", required=True)
        ]
    )

    assert find_red_flags(job, ALEX) == []
    assert find_red_flags(job, None) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("EU citizen, no visa needed in the EU", False),
        ("Needs visa sponsorship", True),
        ("Blue Card holder", False),
        ("I will need a sponsor", True),
        ("", False),
    ],
)
def test_needs_visa_sponsorship(text: str, expected: bool) -> None:
    assert needs_visa_sponsorship(text) is expected
