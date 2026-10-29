# What: pytest setup for the live evaluation tests in evals/: a throwaway runtime folder, no
#       startup key check, no DeepEval telemetry, and a skip when there is no OpenRouter key.
# Why: like tests/conftest.py, the evals must never touch the user's real data in data/runtime/.
# Used by: evals/test_faithfulness.py, evals/test_requirements_extraction.py.

import os
import tempfile

# Set before any `app` module is imported, so settings point at the throwaway folder.
os.environ["RUNTIME_DIR"] = tempfile.mkdtemp(prefix="job-copilot-evals-")
os.environ["STARTUP_KEY_CHECK"] = "false"
os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "YES"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def app_llm():
    """The app's real chat model (the one the UI uses)."""
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.agent.llm import get_llm

    return get_llm()


@pytest.fixture(scope="session")
def metrics():
    """The DeepEval metrics with the default judge model from run_evals.py."""
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.agent.llm import get_llm
    from evals.judge import make_judge, make_metrics
    from evals.run_evals import DEFAULT_JUDGE

    llm = get_llm(model=DEFAULT_JUDGE, temperature=0.0, max_tokens=4000)
    return make_metrics(make_judge(llm, DEFAULT_JUDGE))
