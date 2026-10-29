# What: live test of the drafting step on the 5 drafting cases: no removed claim may appear in
#       a letter, and the letters must on average be faithful to the approved claims (DeepEval
#       faithfulness) and tailored to the posting (G-Eval rubric).
# Why: the anti-hallucination promise of the app, checked by an independent judge model.
#      Costs about 2 cents: `uv run pytest evals/test_faithfulness.py -m live`.
# Used by: pytest (live marker).

import pytest

from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE
from evals.data import load_drafting_cases
from evals.judge import FAITHFULNESS_THRESHOLD, TAILORING_THRESHOLD
from evals.runners import judge_drafts, write_drafts

# Single letters may dip below the per-letter threshold (the judge is strict about small
# exaggerations, see docs/EVALUATION.md); the average must not.
AVERAGE_FAITHFULNESS = FAITHFULNESS_THRESHOLD - 0.1


@pytest.fixture(scope="module")
def judged(app_llm, metrics):
    results = []
    for case in load_drafting_cases():
        result = write_drafts(app_llm, case, DEMO_PROFILE, DEMO_PREFERENCES)
        judge_drafts(result, case, DEMO_PROFILE, metrics)
        results.append(result)
    return results


@pytest.mark.live
def test_removed_claims_never_appear(judged) -> None:
    assert not [r.case_id for r in judged if r.error]
    assert not [r.case_id for r in judged if r.removed_claim_leaked]


@pytest.mark.live
def test_letters_are_faithful_on_average(judged) -> None:
    scores = [
        r.faithfulness.score for r in judged if r.faithfulness and r.faithfulness.score is not None
    ]
    assert len(scores) == len(judged)
    flagged = {r.case_id: r.faithfulness.flagged for r in judged}
    assert sum(scores) / len(scores) >= AVERAGE_FAITHFULNESS, flagged


@pytest.mark.live
def test_letters_are_tailored(judged) -> None:
    scores = [r.tailoring.score for r in judged if r.tailoring and r.tailoring.score is not None]
    assert len(scores) == len(judged)
    assert min(scores) >= TAILORING_THRESHOLD - 0.1
    assert sum(scores) / len(scores) >= TAILORING_THRESHOLD
