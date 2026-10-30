# What: tests for app/services/copilot.py, the layer between the chat page and the graph.
# Why: the page draws whatever events this service yields, and rebuilds the chat from saved
#      state on every rerun. Errors must come out as one friendly event, never an exception.
# Used by: pytest.

from langchain_core.messages import AIMessage

from app.config import get_settings
from app.db import repo
from app.services import copilot
from tests.copilot_fakes import calls, research_and_move_script, two_jobs

THREAD = "chat-00000000000a"
ALL_TOGGLES = ["job_posting", "experience", "company", "job_search", "salary", "tracker"]


def _kinds(events) -> list[str]:
    return [e.kind for e in events]


def test_answer_streams_tool_calls_results_and_text(copilot_app) -> None:
    two_jobs()
    copilot_app([calls(("get_applications", {})), AIMessage("You have 2 jobs.")])

    events = list(copilot.send_message(THREAD, "How many jobs?", ["tracker"]))

    assert _kinds(events) == ["tool_call", "tool_result", "token"]
    assert events[0].text == "🗂️ Read and update my tracker"
    assert "Junior AI Engineer" in events[1].text
    assert events[2].text == "You have 2 jobs."


def test_history_is_rebuilt_from_the_checkpointer(copilot_app) -> None:
    two_jobs()
    copilot_app([calls(("get_applications", {})), AIMessage("You have 2 jobs.")])
    list(copilot.send_message(THREAD, "How many jobs?", ["tracker"]))

    turns = copilot.get_history(THREAD)

    assert [(t.role, t.text) for t in turns] == [
        ("user", "How many jobs?"),
        ("assistant", "You have 2 jobs."),
    ]
    use = turns[1].tools[0]
    assert (use.name, use.args) == ("get_applications", {})
    assert "Data Engineer" in use.result


def test_done_when_flow_through_the_service(copilot_app, monkeypatch) -> None:
    _, new = two_jobs()
    copilot_app(research_and_move_script(new))
    # The fake research tool needs no key, but the registry only enables "company" with one.
    monkeypatch.setattr(get_settings(), "tavily_api_key", "tvly-test")

    events = list(copilot.send_message(THREAD, "Research and move", ALL_TOGGLES))

    assert _kinds(events)[-1] == "approval"
    used = [e.tool for e in events if e.kind == "tool_call"]
    assert used == ["get_applications", "research_company", "update_tracker"]
    pending = copilot.pending_approval(THREAD)
    assert [p.summary for p in pending] == [
        f"Job #{new} (Junior AI Engineer at Lumen Health): move to Applied"
    ]

    after = list(copilot.answer_approval(THREAD, approved=True))

    assert _kinds(after) == ["tool_result", "token"]
    assert copilot.pending_approval(THREAD) == []
    with repo.session_scope() as session:
        assert repo.get_job(session, new).status == "applied"


def test_answer_approval_when_nothing_waits(copilot_app) -> None:
    copilot_app([])

    events = list(copilot.answer_approval(THREAD, approved=True))

    assert [(e.kind, e.text) for e in events] == [("error", copilot.NOT_WAITING_MESSAGE)]


def test_runaway_tool_loop_stops_with_a_friendly_message(copilot_app) -> None:
    copilot_app([calls(("get_applications", {})) for _ in range(20)])

    events = list(copilot.send_message(THREAD, "loop forever", ["tracker"]))

    assert events[-1].kind == "error"
    assert events[-1].text == copilot.RECURSION_MESSAGE


def test_llm_outage_is_one_friendly_error(copilot_app) -> None:
    from app.utils.errors import LLMServiceError

    def busy(messages):
        raise LLMServiceError("503")

    copilot_app([busy])

    events = list(copilot.send_message(THREAD, "hi", []))

    assert [(e.kind, e.text) for e in events] == [
        ("error", "The AI service is busy, please retry.")
    ]


def test_no_api_key(copilot_app, monkeypatch) -> None:
    copilot_app([])
    monkeypatch.setattr(get_settings(), "openrouter_api_key", None)

    events = list(copilot.send_message(THREAD, "hi", []))

    assert events[0].kind == "error" and "API key" in events[0].text


def test_new_thread_ids_are_unique() -> None:
    first, second = copilot.new_thread_id(), copilot.new_thread_id()

    assert first != second and first.startswith("chat-") and len(first) == 17


def test_answer_that_broke_off_can_be_retried(copilot_app) -> None:
    """No internet mid-answer: one friendly error, then Retry continues without a new message."""
    from app.utils.errors import NETWORK_MESSAGE, LLMServiceError

    def offline(messages):
        raise LLMServiceError("connection refused", user_message=NETWORK_MESSAGE)

    copilot_app([offline, AIMessage("Back online: you have 0 jobs.")])

    events = list(copilot.send_message(THREAD, "How many jobs?", []))
    assert [(e.kind, e.text) for e in events] == [("error", NETWORK_MESSAGE)]
    assert copilot.can_retry(THREAD)

    retried = list(copilot.retry(THREAD))

    assert [e.text for e in retried if e.kind == "token"] == ["Back online: you have 0 jobs."]
    assert not copilot.can_retry(THREAD)
    users = [t for t in copilot.get_history(THREAD) if t.role == "user"]
    assert len(users) == 1  # the question wasn't sent twice


def test_retry_when_nothing_broke(copilot_app) -> None:
    copilot_app([AIMessage("Hi!")])
    list(copilot.send_message(THREAD, "hi", []))

    assert not copilot.can_retry(THREAD)
    assert [e.kind for e in copilot.retry(THREAD)] == ["error"]
