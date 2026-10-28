# What: tests of the copilot chat graph (app/agent/copilot_graph.py) with a scripted
#       tool-calling model and the real tracker tools on a temporary database.
# Why: checks the ReAct loop (agent → tools → agent), dynamic tool binding from the toggles,
#      the approval pause before write tools (approve and decline), disabled tools, the input
#      guard, and the Phase 6 "done when" scenario.
# Used by: pytest.

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agent.copilot_graph import (
    DECLINED_MESSAGE,
    TOO_LONG_MESSAGE,
    build_copilot_graph,
    chat_config,
    recent_messages,
)
from app.db import repo
from app.utils.errors import FetchError
from tests.copilot_fakes import (
    ALL_ON,
    calls,
    make_copilot_deps,
    research_and_move_script,
    two_jobs,
)
from tests.fakes import FakeToolLLM

DONE_WHEN = "Research the company for my newest job and move it to Applied"


def _chat(llm, text: str, enabled=ALL_ON, extra_tools=None):
    graph = build_copilot_graph(make_copilot_deps(llm, extra_tools), InMemorySaver())
    config = chat_config("chat-000000000001")
    graph.invoke({"messages": [HumanMessage(text)], "enabled_tools": list(enabled)}, config)
    return graph, config


def _status(job_id: int) -> str:
    with repo.session_scope() as session:
        return repo.get_job(session, job_id).status


def _tool_names(messages) -> list[str]:
    return [m.name for m in messages if isinstance(m, ToolMessage)]


def test_plain_answer_needs_no_tools() -> None:
    llm = FakeToolLLM([AIMessage("Hi! How can I help?")])

    graph, config = _chat(llm, "hello", enabled=["get_applications"])

    state = graph.get_state(config)
    assert state.next == ()
    assert state.values["messages"][-1].content == "Hi! How can I help?"
    assert llm.bound == [["get_applications"]]  # only the enabled tool was bound


def test_system_prompt_has_profile_date_and_tools() -> None:
    llm = FakeToolLLM([AIMessage("ok")])

    _chat(llm, "hello", enabled=["search_my_experience"])

    system = llm.calls[0][0].content
    assert "Alex Novak" in system and "2026-10-28" in system
    assert "Available tools: search_my_experience" in system


def test_research_and_move_asks_before_the_tracker_update(app_db) -> None:
    _, new = two_jobs()
    llm = FakeToolLLM(research_and_move_script(new))

    graph, config = _chat(llm, DONE_WHEN)

    # Paused before the write: two read tools ran, the job is unchanged.
    state = graph.get_state(config)
    assert state.next == ("approve",)
    assert _tool_names(state.values["messages"]) == ["get_applications", "research_company"]
    payload = state.tasks[0].interrupts[0].value
    assert payload["type"] == "tool_approval"
    assert payload["actions"][0]["summary"] == (
        f"Job #{new} (Junior AI Engineer at Lumen Health): move to Applied"
    )
    assert _status(new) == "saved"

    graph.invoke(Command(resume={"approved": True}), config)

    state = graph.get_state(config)
    assert state.next == ()
    assert _status(new) == "applied"
    assert _tool_names(state.values["messages"])[-1] == "update_tracker"
    assert "moved the job to Applied" in state.values["messages"][-1].content


def test_declined_update_changes_nothing(app_db) -> None:
    _, new = two_jobs()
    script = research_and_move_script(new)[:3] + [AIMessage("OK, I left it as it is.")]
    graph, config = _chat(FakeToolLLM(script), DONE_WHEN)

    graph.invoke(Command(resume={"approved": False}), config)

    messages = graph.get_state(config).values["messages"]
    assert _status(new) == "saved"
    declined = [m for m in messages if isinstance(m, ToolMessage) and m.name == "update_tracker"]
    assert declined[0].content == DECLINED_MESSAGE
    assert messages[-1].content == "OK, I left it as it is."


def test_invalid_approval_answer_is_rejected(app_db) -> None:
    _, new = two_jobs()
    graph, config = _chat(FakeToolLLM(research_and_move_script(new)), DONE_WHEN)

    with pytest.raises(ValueError):
        graph.invoke(Command(resume={"approved": "maybe"}), config)


def test_disabled_tool_is_not_run(app_db) -> None:
    _, new = two_jobs()
    llm = FakeToolLLM(
        [calls(("get_applications", {})), AIMessage("The tracker tool is switched off.")]
    )

    graph, config = _chat(llm, "list my jobs", enabled=["search_my_experience"])

    result = graph.get_state(config).values["messages"][-2]
    assert isinstance(result, ToolMessage) and result.status == "error"
    assert "not a valid tool" in result.content
    assert llm.bound[0] == ["search_my_experience"]


def test_tool_errors_reach_the_model_as_friendly_text() -> None:
    from langchain_core.tools import tool

    @tool("fetch_job_posting", description="fake")
    def broken(url_or_text: str) -> str:
        raise FetchError("login wall")

    llm = FakeToolLLM(
        [calls(("fetch_job_posting", {"url_or_text": "https://x"})), AIMessage("Sorry.")]
    )
    graph, config = _chat(
        llm, "read this", enabled=["fetch_job_posting"], extra_tools={"fetch_job_posting": broken}
    )

    result = graph.get_state(config).values["messages"][-2]
    assert result.content == (
        "Tool error: I couldn't read that page. Please paste the job text instead."
    )


def test_too_long_message_is_answered_without_the_llm() -> None:
    llm = FakeToolLLM([])

    graph, config = _chat(llm, "x" * 5000)

    assert graph.get_state(config).values["messages"][-1].content == TOO_LONG_MESSAGE
    assert llm.calls == []


def test_recent_messages_cut_at_a_user_message() -> None:
    history = [HumanMessage("q1"), calls(("get_applications", {})), AIMessage("a1")] * 5

    recent = recent_messages(history, limit=4)

    assert isinstance(recent[0], HumanMessage)
    assert len(recent) == 3
