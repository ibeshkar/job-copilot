# What: a ready-made offline "world" for the copilot chat: helpers to script tool calls, a fake
#       research_company tool, and a builder for CopilotDeps with the real tracker tools.
# Why: the copilot graph, service and UI tests all need the same scripted conversations, e.g.
#      "research the company for my newest job and move it to Applied".
# Used by: tests/test_copilot_graph.py, tests/test_copilot_service.py, tests/test_ui_pages.py.

import itertools

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agent.copilot_graph import CopilotDeps
from app.db import repo
from app.memory.store import Preferences, Profile
from app.tools.registry import ALL_TOOLS, APPROVAL_TOOLS
from app.tools.tracker import describe_update
from tests.fakes import FakeToolLLM

_ids = itertools.count(1)
RESEARCH_RESULT = (
    "<untrusted_web_content>[1] Lumen Health builds AI for clinics.</untrusted_web_content>"
)


def calls(*pairs: tuple[str, dict]) -> AIMessage:
    """An AI message that asks for tools, e.g. calls(("get_applications", {}))."""
    return AIMessage(
        "",
        tool_calls=[{"name": n, "args": a, "id": f"call_{next(_ids)}"} for n, a in pairs],
    )


@tool("research_company", description="fake web research")
def fake_research_company(company: str, focus: str | None = None) -> str:
    return RESEARCH_RESULT


def make_copilot_deps(llm: FakeToolLLM, extra_tools: dict | None = None) -> CopilotDeps:
    tools = {**ALL_TOOLS, "research_company": fake_research_company, **(extra_tools or {})}
    return CopilotDeps(
        llm=llm,
        tools=tools,
        approval_tools=APPROVAL_TOOLS,
        describe_action=lambda name, args: (
            describe_update(**args) if name == "update_tracker" else name
        ),
        get_profile=lambda: Profile(name="Alex Novak", target_roles=["AI Engineer"]),
        get_preferences=Preferences,
        today=lambda: "2026-10-28",
    )


ALL_ON = list(ALL_TOOLS)


def two_jobs() -> tuple[int, int]:
    """An older and a newer job on the board; returns (old_id, new_id)."""
    with repo.session_scope() as session:
        old = repo.create_job(session, title="Data Engineer", company="Northwind").id
        new = repo.create_job(session, title="Junior AI Engineer", company="Lumen Health").id
    return old, new


def research_and_move_script(new_id: int) -> list:
    """The Phase 6 "done when": look up the board, research the company, move the job."""
    return [
        calls(("get_applications", {})),
        calls(("research_company", {"company": "Lumen Health"})),
        calls(("update_tracker", {"job_id": new_id, "status": "applied"})),
        AIMessage("Lumen Health builds AI for clinics. I moved the job to Applied."),
    ]
