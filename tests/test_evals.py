# What: offline tests for the evaluation suite in evals/: the datasets are valid, the plain-Python
#       scoring (precision/recall, score spread, ranking) is right, the drafting case reaches
#       the real drafting node with only approved claims, the DeepEval judge adapter works with
#       a fake model, our faithfulness prompt is the one DeepEval uses, and the report keeps the
#       hand-written parts of docs/EVALUATION.md.
# Why: the real evaluation costs money and runs rarely (evals/run_evals.py). These checks make
#      sure a broken dataset, formula or report template is caught by plain `pytest`.
# Used by: pytest.

import pytest
from pydantic import BaseModel

from app.agent.state import Drafts
from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE
from evals.data import (
    LabelledRequirement,
    load_chat_questions,
    load_drafting_cases,
    load_labelled_postings,
)
from evals.judge import CANDIDATE_CLAIMS_PROMPT, OpenRouterJudge, make_judge, make_metrics
from evals.report import END_MARKER, START_MARKER, EvalRun, render_results, write_report
from evals.runners import (
    ChatResult,
    DraftResult,
    MetricResult,
    drafting_state,
    faithfulness_context,
    write_drafts,
)
from evals.scoring import (
    ScoreSpread,
    matches_label,
    micro_average,
    ranking_holds,
    score_extraction,
)
from tests.fakes import FakeStructuredLLM

SQL = LabelledRequirement(label="Advanced SQL", keywords=["sql"])
PYTHON = LabelledRequirement(label="Python", keywords=["python"])
GERMAN = LabelledRequirement(label="German C1", keywords=["german", "deutsch"])

# ---------------------------------------------------------------- datasets


def test_labelled_postings_are_complete() -> None:
    postings = load_labelled_postings()
    assert 8 <= len(postings) <= 10
    assert len({p.id for p in postings}) == len(postings)
    for posting in postings:
        assert len(posting.text) > 300, posting.id  # the file exists and is a real posting
        assert posting.must


def test_drafting_cases_have_approved_and_removed_claims() -> None:
    cases = load_drafting_cases()
    assert len(cases) == 5
    for case in cases:
        assert case.approved and case.removed
        assert case.posting_text
        assert not set(case.approved) & set(case.removed)


def test_chat_questions_load() -> None:
    assert len(load_chat_questions()) >= 5


# ---------------------------------------------------------------- extraction scoring


def test_matches_label_ignores_case_and_pads_edges() -> None:
    assert matches_label("Strong SQL skills", SQL)
    assert not matches_label("Strong Python", SQL)
    r_label = LabelledRequirement(label="R", keywords=[" r "])
    assert matches_label("R", r_label)  # padding lets a one-letter keyword match alone
    assert not matches_label("React", r_label)


def test_split_requirements_are_all_correct() -> None:
    # "SQL and Python" split into two items: both match a label, nothing is extra.
    score = score_extraction("job", [SQL, PYTHON], ["Good SQL", "Python 3", "Advanced SQL"])
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.extra_predictions == []


def test_extra_missed_and_demoted_requirements() -> None:
    score = score_extraction(
        "job", [SQL, PYTHON, GERMAN], ["SQL", "Team player"], predicted_nices=["Some German"]
    )
    assert score.precision == 0.5  # "Team player" is not a labelled must-have
    assert score.recall == pytest.approx(1 / 3)
    assert score.extra_predictions == ["Team player"]
    assert score.missed_labels == ["Python", "German C1"]
    assert score.demoted_labels == ["German C1"]  # found, but with the wrong importance


def test_no_predictions_means_full_precision_zero_recall() -> None:
    score = score_extraction("job", [SQL], [])
    assert score.precision == 1.0
    assert score.recall == 0.0
    assert score.f1 == 0.0


def test_micro_average_counts_every_requirement_once() -> None:
    a = score_extraction("a", [SQL], ["SQL", "Fluff"])  # 1 of 2 right, 1 of 1 found
    b = score_extraction("b", [SQL, PYTHON], ["SQL"])  # 1 of 1 right, 1 of 2 found
    precision, recall, f1 = micro_average([a, b])
    assert precision == pytest.approx(2 / 3)
    assert recall == pytest.approx(2 / 3)
    assert f1 == pytest.approx(2 / 3)


# ---------------------------------------------------------------- fit-score consistency


def test_score_spread() -> None:
    spread = ScoreSpread("job", [70, 74, 72])
    assert spread.mean == 72
    assert spread.range == 4
    assert spread.stdev == pytest.approx(1.633, abs=0.001)
    assert ScoreSpread("job", [80]).stdev == 0.0


def test_ranking_needs_every_run_in_order() -> None:
    good, partial, poor = (
        ScoreSpread("g", [80, 85]),
        ScoreSpread("p", [50, 60]),
        ScoreSpread("x", [20, 30]),
    )
    assert ranking_holds([good, partial, poor])
    # The averages are still in order, but one partial run beat one good run.
    assert not ranking_holds([ScoreSpread("g", [80, 85]), ScoreSpread("p", [50, 81]), poor])
    assert not ranking_holds([good, ScoreSpread("p", []), poor])


# ---------------------------------------------------------------- drafting runner


def test_drafting_case_reaches_the_real_node_without_removed_claims() -> None:
    case = load_drafting_cases()[0]
    llm = FakeStructuredLLM(
        {
            Drafts: Drafts(
                cv_bullets=["- Built DocuMind"],
                cover_letter="Dear team,\n\nI built DocuMind.",
                used_claim_ids=["C1", "X1"],
            )
        }
    )
    result = write_drafts(llm, case, DEMO_PROFILE, DEMO_PREFERENCES)

    assert result.error == ""
    assert result.cv_bullets == ["Built DocuMind"]  # the node's own clean-up ran
    assert result.words == 5
    assert not result.removed_claim_leaked
    prompt = "\n".join(str(m.content) for m in llm.calls[0][1])
    assert case.approved[0] in prompt
    assert case.removed[0] not in prompt
    assert case.gaps[0].honest_positioning in prompt


def test_removed_claim_in_the_letter_is_reported() -> None:
    case = load_drafting_cases()[0]
    leaked = Drafts(cv_bullets=[], cover_letter=f"Also: {case.removed[0]}.", used_claim_ids=[])
    result = write_drafts(FakeStructuredLLM({Drafts: leaked}), case, DEMO_PROFILE, DEMO_PREFERENCES)
    assert result.removed_claim_leaked


def test_drafting_state_marks_claims() -> None:
    state = drafting_state(load_drafting_cases()[1])
    decisions = {c.user_decision for c in state["claims"]}
    assert decisions == {"approved", "removed"}
    assert [m.status for m in state["fit"].matches] == ["missing", "missing", "missing"]


def test_faithfulness_context_leaves_out_removed_claims() -> None:
    case = load_drafting_cases()[0]
    context = "\n".join(faithfulness_context(case, DEMO_PROFILE))
    assert all(claim in context for claim in case.approved)
    assert case.removed[0] not in context
    assert "Alex Novak is applying for the Junior AI Engineer" in context


# ---------------------------------------------------------------- the DeepEval judge


class _Verdict(BaseModel):
    verdict: str


class _FakeJudgeLLM:
    """Answers structured requests with a fixed object, or fails them, and text with "{}"."""

    def __init__(self, structured_fails: bool = False) -> None:
        self.structured_fails = structured_fails

    def with_structured_output(self, schema):
        fails = self.structured_fails

        class _Runnable:
            def invoke(self, prompt):
                if fails:
                    raise ValueError("schema not supported")
                return schema(verdict="yes")

        return _Runnable()

    def invoke(self, prompt):
        class _Message:
            content = '{"verdict": "no"}'

        return _Message()


def test_judge_returns_schema_objects() -> None:
    judge = OpenRouterJudge(_FakeJudgeLLM(), "openai/gpt-4.1-mini")
    assert judge.generate("prompt", schema=_Verdict) == _Verdict(verdict="yes")
    assert judge.get_model_name() == "openai/gpt-4.1-mini (via OpenRouter)"


def test_judge_falls_back_to_json_text() -> None:
    judge = OpenRouterJudge(_FakeJudgeLLM(structured_fails=True), "m")
    assert judge.generate("prompt", schema=_Verdict) == '{"verdict": "no"}'
    assert judge.generate("prompt") == '{"verdict": "no"}'


def test_faithfulness_uses_our_candidate_facts_prompt() -> None:
    metrics = make_metrics(make_judge(_FakeJudgeLLM(), "m"))
    assert set(metrics) == {"faithfulness", "answer_relevancy", "tailoring"}
    # The override hook is the fragile part of DeepEval's API, so check the real render path.
    prompt = metrics["faithfulness"]._get_prompt("generate_claims", actual_output="LETTER")
    assert prompt == CANDIDATE_CLAIMS_PROMPT.format(actual_output="LETTER")
    assert "opinions, motivation" in prompt


# ---------------------------------------------------------------- the report


def _sample_run() -> EvalRun:
    good = MetricResult(score=0.95, passed=True, reason="All backed.")
    return EvalRun(
        started_at="2026-10-29 12:00",
        app_model="openai/gpt-4o-mini",
        judge_model="openai/gpt-4.1-mini",
        prompt_version="test",
        extraction=[score_extraction("job_a", [SQL, PYTHON], ["SQL", "Fluff | pipe"])],
        drafting=[
            DraftResult("case_a", "letter", [], 120, False, good, good),
            DraftResult("case_b", error="StructuredOutputError: cut off"),
        ],
        chat=[ChatResult("Q?", "A.", ["search_my_experience"], good)],
        consistency=[ScoreSpread("good", [80, 82]), ScoreSpread("poor", [30, 30])],
        costs={"extraction (app)": {"tokens": 1234, "cost_usd": 0.0012}},
    )


def test_render_results_has_every_section() -> None:
    text = render_results(_sample_run())
    for heading in ("### Summary", "per posting", "per case", "per question", "consistency"):
        assert heading in text
    assert "0.50 / 0.50 / 0.50 ❌" in text  # precision / recall / F1 below target
    assert "Drafting failed: StructuredOutputError" in text
    assert "Fluff / pipe" in text  # a pipe would break the Markdown table
    assert "| Fit score | Every run ranks good > partial > poor | ✅ |" in text
    assert "1,234" in text


def test_skipped_parts_are_left_out() -> None:
    run = _sample_run()
    run.drafting = run.chat = None
    text = render_results(run)
    assert "Drafting" not in text
    assert "Copilot chat" not in text


def test_write_report_keeps_the_hand_written_text(tmp_path) -> None:
    doc = tmp_path / "EVALUATION.md"
    doc.write_text(f"# Evaluation\nIntro.\n{START_MARKER}\nold results\n{END_MARKER}\nAnalysis.\n")
    json_path = tmp_path / "results" / "latest.json"

    write_report(_sample_run(), doc, json_path)

    text = doc.read_text()
    assert text.startswith("# Evaluation\nIntro.\n")
    assert text.endswith(f"{END_MARKER}\nAnalysis.\n")
    assert "old results" not in text
    assert "### Summary" in text
    assert '"extraction_micro"' in json_path.read_text()


def test_write_report_appends_without_markers(tmp_path) -> None:
    doc = tmp_path / "EVALUATION.md"
    doc.write_text("# Evaluation\n")
    write_report(_sample_run(), doc, tmp_path / "latest.json")
    assert doc.read_text().count(START_MARKER) == 1
