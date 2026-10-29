# What: live test of requirements extraction on the 10 labelled postings: precision and recall
#       of the must-have requirements, compared with the hand-made labels.
# Why: the same check as part 1 of run_evals.py, as a pass/fail test with the report targets.
#      Costs well under one cent: `uv run pytest evals/test_requirements_extraction.py -m live`.
# Used by: pytest (live marker).

import pytest

from evals.data import load_labelled_postings
from evals.report import EXTRACTION_TARGET
from evals.runners import run_extraction
from evals.scoring import micro_average


@pytest.mark.live
def test_must_haves_match_the_labels(app_llm) -> None:
    scores = run_extraction(app_llm, load_labelled_postings())
    precision, recall, _ = micro_average(scores)
    misses = {s.posting_id: s.missed_labels for s in scores if s.missed_labels}
    assert precision >= EXTRACTION_TARGET, [s.extra_predictions for s in scores]
    assert recall >= EXTRACTION_TARGET, misses
