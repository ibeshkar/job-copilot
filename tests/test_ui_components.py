# What: tests for the pure helpers behind the Fit tab's requirements list (app/ui/components.py).
# Why: the grouping, filter and sort order decide what the user sees first: gaps before
#      matches, must-haves before nice-to-haves, and no empty groups.
# Used by: pytest.

from app.agent.state import FitReport, Requirement, RequirementMatch
from app.ui.components import group_requirements, match_rows, status_badge


def _req(rid: str, importance: str) -> Requirement:
    return Requirement(id=rid, text=f"req {rid}", category="skill", importance=importance)


def _match(rid: str, status: str) -> RequirementMatch:
    return RequirementMatch(
        requirement_id=rid, status=status, evidence=[], explanation="", honest_positioning=None
    )


REQUIREMENTS = [_req("R1", "nice"), _req("R2", "must"), _req("R3", "must"), _req("R4", "nice")]
FIT = FitReport(
    matches=[_match("R1", "missing"), _match("R2", "met"), _match("R3", "missing")],
    score=50,
    red_flags=[],
    strengths_summary="",
    questions_to_ask_employer=[],
)


def _ids(groups) -> dict[str, list[str]]:
    return {status: [r.id for r, _ in rows] for status, rows in groups.items()}


def test_rows_skip_requirements_without_a_match() -> None:
    assert [r.id for r, _ in match_rows(REQUIREMENTS, FIT)] == ["R1", "R2", "R3"]


def test_groups_are_gaps_first_must_first_and_never_empty() -> None:
    groups = group_requirements(match_rows(REQUIREMENTS, FIT))

    assert list(groups) == ["missing", "met"]  # no "partial" group: it would be empty
    assert _ids(groups) == {"missing": ["R3", "R1"], "met": ["R2"]}


def test_filters() -> None:
    rows = match_rows(REQUIREMENTS, FIT)

    assert _ids(group_requirements(rows, "gaps")) == {"missing": ["R3", "R1"]}
    assert _ids(group_requirements(rows, "must")) == {"missing": ["R3"], "met": ["R2"]}


def test_status_badge() -> None:
    assert status_badge("missing", 2) == ":red-badge[❌ 2 missing]"
    assert status_badge("met") == ":green-badge[✅ Met]"
