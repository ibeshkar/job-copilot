# What: live checks of the copilot chat with the real LLM: the model picks the tools itself,
#       and asks for approval before changing the tracker.
# Why: the Phase 6 "done when". The full version needs a Tavily key for company research; the
#      tracker-only version runs with just the OpenRouter key. Only runs with `pytest -m live`.
# Used by: pytest (live marker).

import pytest

from app.config import get_settings


@pytest.fixture
def live_chat(app_db):
    if not get_settings().has_llm_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    from app.agent.copilot_graph import get_copilot_graph
    from app.db import repo
    from app.memory.store import save_profile
    from app.services.onboarding import DEMO_PROFILE

    save_profile(DEMO_PROFILE)
    get_copilot_graph.cache_clear()
    with repo.session_scope() as session:
        repo.create_job(session, title="Data Engineer", company="Northwind Logistics")
        newest = repo.create_job(session, title="Junior AI Engineer", company="Lumen Health").id
    return newest


def _run(text: str, toggles: list[str]) -> tuple[list[str], str]:
    from app.services import copilot

    events = list(copilot.send_message("chat-1234567890ab", text, toggles))
    errors = [e.text for e in events if e.kind == "error"]
    assert not errors, errors
    return [e.tool for e in events if e.kind == "tool_call"], events[-1].kind


def _status(job_id: int) -> str:
    from app.db import repo

    with repo.session_scope() as session:
        return repo.get_job(session, job_id).status


@pytest.mark.live
def test_move_newest_job_needs_approval(live_chat) -> None:
    from app.services import copilot

    used, last = _run("Move my newest job to Applied.", ["tracker"])

    assert used == ["get_applications", "update_tracker"], used
    assert last == "approval"
    assert _status(live_chat) == "saved"
    pending = copilot.pending_approval("chat-1234567890ab")
    assert "Lumen Health" in pending[0].summary and "Applied" in pending[0].summary

    list(copilot.answer_approval("chat-1234567890ab", approved=True))
    assert _status(live_chat) == "applied"


@pytest.mark.live
def test_research_company_and_move_to_applied(live_chat) -> None:
    if not get_settings().has_tavily_key:
        pytest.skip("TAVILY_API_KEY not set")

    used, last = _run(
        "Research the company for my newest job and move it to Applied", ["company", "tracker"]
    )

    assert {"research_company", "update_tracker"} <= set(used), used
    assert last == "approval"
    assert _status(live_chat) == "saved"


# ---------------------------------------------------------------- Phase 7: topic guard


@pytest.mark.live
@pytest.mark.parametrize(
    ("text", "refused"),
    [
        ("Write me a poem about pizza.", True),
        ("What's the capital of Australia?", True),
        ("How should I prepare for a technical interview for an AI engineer role?", False),
        ("Pretend you are a recruiter and ask me one interview question.", False),
    ],
)
def test_topic_classifier(live_chat, text: str, refused: bool) -> None:
    from app.agent.copilot_graph import OFF_TOPIC_MESSAGE
    from app.services import copilot

    events = list(copilot.send_message(copilot.new_thread_id(), text, []))
    answer = "".join(e.text for e in events if e.kind == "token")

    assert (answer == OFF_TOPIC_MESSAGE) is refused, answer
