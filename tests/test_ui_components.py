# What: tests for the pure helpers behind the Fit tab's requirements list and the claim check
#       list (app/ui/components.py).
# Why: the grouping, filter and sort order decide what the user sees first: gaps before
#      matches, must-haves before nice-to-haves, unconfirmed claims before backed ones, and no
#      empty groups.
# Used by: pytest.

from app.agent.state import Claim, FitReport, Requirement, RequirementMatch
from app.tools.company import WebResult
from app.ui.components import (
    claim_markdown,
    claims_summary,
    group_claims,
    group_requirements,
    match_rows,
    news_line,
    progress_label,
    status_badge,
    step_topic,
)


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


def _claim(cid: str, supported: bool) -> Claim:
    return Claim(id=cid, text=f"claim {cid}", evidence_chunk_ids=[], supported=supported)


def test_claim_groups_are_unconfirmed_first_and_never_empty() -> None:
    claims = [_claim("C1", True), _claim("C2", False), _claim("C3", True)]

    groups = group_claims(claims)

    assert [(s, [c.id for c in cs]) for s, cs in groups] == [(False, ["C2"]), (True, ["C1", "C3"])]
    assert [s for s, _ in group_claims(claims[:1])] == [True]


def test_claims_summary() -> None:
    assert claims_summary([_claim("C1", True), _claim("C2", False)]) == (
        "**2 claims** · ✅ 1 backed by documents · ⚠️ 1 needs your confirmation"
    )


def test_claim_markdown() -> None:
    assert claim_markdown("Built X", kept=True, edited=False) == "Built X"
    assert claim_markdown("Built X", kept=True, edited=True) == "Built X :blue-badge[edited]"
    assert claim_markdown("Built X", kept=False, edited=False) == ":gray[~~Built X~~]"


# ---------------------------------------------------------------- input + button rows


def test_input_with_action_is_bottom_aligned_and_returns_the_text() -> None:
    from streamlit.proto.Block_pb2 import Block
    from streamlit.testing.v1 import AppTest

    def script() -> None:
        import streamlit as st

        from app.ui.components import input_with_action

        text, clicked = input_with_action("Focus (optional)", "Go", key="focus")
        st.write(f"{text}|{clicked}")

    at = AppTest.from_function(script).run()
    bottom = Block.Column.VerticalAlignment.BOTTOM
    assert [c.proto.vertical_alignment for c in at.columns] == [bottom, bottom]
    assert at.text_input[0].label == "Focus (optional)"  # the label stays visible

    at.text_input[0].set_value("funding").run()  # Enter: commits the text, no action
    assert at.markdown[-1].value == "funding|False"
    at.button[0].click().run()
    assert at.markdown[-1].value == "funding|True"


def test_no_page_lines_up_buttons_with_spacers() -> None:
    from pathlib import Path

    spacers = ['markdown("&nbsp;")', 'write("")', "<br>&nbsp;", "margin-top"]
    for path in Path("app/ui").rglob("*.py"):
        source = path.read_text()
        assert not [s for s in spacers if s in source], f"{path}: use action_row() instead"


def test_news_line_shows_month_or_date_unknown() -> None:
    dated = WebResult("Lumen opens Vienna office", "https://news.example/v", "", "2026-09-14")
    undated = WebResult("", "https://news.example/u", "", None)

    assert news_line(dated) == "[Lumen opens Vienna office](https://news.example/v) · Sep 2026"
    assert news_line(undated) == (
        "[https://news.example/u](https://news.example/u) · :gray[date unknown]"
    )


def test_step_topic_groups_repeated_steps() -> None:
    assert step_topic("Searched your documents: evidence for 7 requirements") == (
        "Searched your documents"
    )
    assert step_topic("Checked your input") == "Checked your input"
    assert step_topic("Fact check: passed") == step_topic("Fact check: found 2 problems")


def test_progress_label_shows_the_step_number() -> None:
    running = progress_label("Analysing fit", "Searching your documents…", 4, 5)
    assert running == "⏳ Analysing fit… (4/5) Searching your documents…"
    assert progress_label("Writing your drafts", "Writing the drafts…", 1, None) == (
        "⏳ Writing your drafts… Writing the drafts…"
    )
    assert progress_label("Analysing fit", "Scoring the fit…", 7, 5).startswith(
        "⏳ Analysing fit… (5/5)"
    )
