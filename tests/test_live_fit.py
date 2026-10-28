# What: live end-to-end check of the analysis graph on the 3 sample jobs with the real LLM.
# Why: the Phase 3 acceptance test: scores must rank good > partial > poor, and each run must
#      pause at fit review. Costs a few cents, so it only runs with `uv run pytest -m live`.
# Used by: pytest (live marker).

from pathlib import Path

import pytest

from app.config import get_settings

JOBS = sorted((Path(__file__).resolve().parent.parent / "data/samples/jobs").glob("*.md"))


@pytest.mark.live
def test_sample_jobs_rank_good_partial_poor(app_db) -> None:
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.agent.application_graph import get_application_graph, thread_config
    from app.memory.store import save_preferences, save_profile
    from app.services.onboarding import DEMO_PREFERENCES, DEMO_PROFILE, load_demo_documents

    save_profile(DEMO_PROFILE)
    save_preferences(DEMO_PREFERENCES)
    assert load_demo_documents().errors == {}
    get_application_graph.cache_clear()
    graph = get_application_graph()

    scores = []
    for i, path in enumerate(JOBS):
        config = thread_config(f"live-{i}")
        graph.invoke({"raw_input": path.read_text()}, config)
        state = graph.get_state(config)
        assert state.next == ("fit_review",), state.values.get("errors")
        scores.append(state.values["fit"].score)

    good, partial, poor = scores
    assert good > partial > poor, scores
    assert poor <= 40  # German C1 is a blocking red flag
