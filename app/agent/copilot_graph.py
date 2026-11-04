# What: the copilot chat agent (docs/ARCHITECTURE.md §3.2), a custom ReAct graph:
#       input_guard (Python rules + LLM topic classifier) → agent (LLM + enabled tools) ⇄ tools,
#       with an `approve` pause before any tool that writes data (update_tracker).
# Why: chat questions are open-ended, so here the LLM decides which tools to call and in which
#      order (ReAct: reason → act → observe → repeat). The graph is written by hand instead of
#      using the prebuilt agent, so the loop, the dynamic tool binding and the approval step are
#      visible and testable.
# Used by: app/services/copilot.py, tests/test_copilot_graph.py.

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from functools import lru_cache, partial
from typing import Annotated, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt
from pydantic import BaseModel

from app.agent.llm import invoke_chat, invoke_structured
from app.agent.prompts import copilot_system_message, topic_guard_messages
from app.agent.state import TopicCheck
from app.guards.input_guard import TOO_LONG_CHAT, check_chat_message
from app.memory.store import Preferences, Profile
from app.utils.errors import AppError, StructuredOutputError
from app.utils.logging import get_logger

logger = get_logger(__name__)

MAX_HISTORY_MESSAGES = 30  # older turns are not sent to the model (cost)
RECURSION_LIMIT = 12  # max graph steps per message: stops runaway tool loops (§6.2)
TOO_LONG_MESSAGE = TOO_LONG_CHAT
OFF_TOPIC_MESSAGE = (
    "I can only help with your job search: your applications, companies, job ads, interviews "
    "and your own experience. What would you like to know about those?"
)
DECLINED_MESSAGE = "The user declined this action. Don't try it again unless they ask."
SKIPPED_MESSAGE = "Not run, because the user declined an action in the same step."


class CopilotState(TypedDict, total=False):
    """One chat thread. The checkpointer saves it, so the conversation survives reruns."""

    # `add_messages` appends new messages (and replaces ones with the same id).
    messages: Annotated[list[AnyMessage], add_messages]
    enabled_tools: list[str]  # tool names switched on in the sidebar for this message


class ToolApprovalDecision(BaseModel):
    """The resume value of the approval pause."""

    approved: bool


@dataclass
class CopilotDeps:
    """What the chat graph needs from the outside world (fakes in tests)."""

    llm: BaseChatModel | None
    tools: dict[str, BaseTool]  # every tool the copilot could use, by name
    approval_tools: frozenset[str]  # tools that need the user's OK first
    describe_action: Callable[[str, dict], str]  # plain-English summary for the approval card
    get_profile: Callable[[], Profile | None]
    get_preferences: Callable[[], Preferences]
    today: Callable[[], str] = lambda: date.today().isoformat()


# ---------------------------------------------------------------- helpers


def _profile_block(profile: Profile | None) -> str:
    if profile is None:
        return "No profile saved yet."
    return (
        f"Name: {profile.name}. Target roles: {', '.join(profile.target_roles) or 'not set'}. "
        f"Seniority: {profile.seniority}. Locations: {', '.join(profile.locations) or 'not set'}. "
        f"Work authorisation: {profile.work_authorisation or 'not stated'}. "
        f"Languages: {', '.join(profile.languages) or 'not stated'}."
    )


def _enabled(state: CopilotState, deps: CopilotDeps) -> list[BaseTool]:
    return [deps.tools[n] for n in state.get("enabled_tools", []) if n in deps.tools]


def recent_messages(messages: list[AnyMessage], limit: int = MAX_HISTORY_MESSAGES) -> list:
    """The last `limit` messages, cut at a user message.

    Cutting anywhere else could separate a tool call from its result, which the API rejects.
    """
    if len(messages) <= limit:
        return messages
    for start in range(len(messages) - limit, len(messages)):
        if isinstance(messages[start], HumanMessage):
            return messages[start:]
    return messages[-1:]


def _tool_error(exc: Exception) -> str:
    """What the model sees when a tool fails: our friendly message, never a stack trace."""
    if isinstance(exc, AppError):
        return f"Tool error: {exc.user_message}"
    return f"Tool error: {type(exc).__name__}. Tell the user the tool failed."


# ---------------------------------------------------------------- nodes


def _previous_reply(messages: list[AnyMessage]) -> str:
    """The copilot's last text answer before the new message (context for the classifier)."""
    for message in reversed(messages[:-1]):
        if isinstance(message, AIMessage) and isinstance(message.content, str) and message.content:
            return message.content
    return ""


def input_guard(state: CopilotState, deps: CopilotDeps) -> dict:
    """Two layers before the agent sees a message (docs/SECURITY.md):

    1. Python rules (free): empty, too long, or an attempt to override the instructions.
    2. A cheap LLM classifier: is the message about the job search at all?

    A refused message gets a fixed answer, and the graph ends without calling the agent.
    """
    last = state["messages"][-1]
    text = last.content if isinstance(last.content, str) else str(last.content)
    check = check_chat_message(text)
    if not check.allowed:
        return {"messages": [AIMessage(check.reason)]}

    try:
        verdict = invoke_structured(
            deps.llm, TopicCheck, topic_guard_messages(text, _previous_reply(state["messages"]))
        )
    except StructuredOutputError:
        # Fail open: the agent's own prompt also declines off-topic requests (layer 3).
        logger.warning("Topic check gave no usable answer; letting the message through")
        return {}
    if not verdict.on_topic:
        logger.info("Chat message refused as off-topic: %s", verdict.reason)
        return {"messages": [AIMessage(OFF_TOPIC_MESSAGE)]}
    return {}


def agent(state: CopilotState, deps: CopilotDeps) -> dict:
    """The "reason" step: the LLM answers, or asks for tools to be called.

    Tools are bound for every call from `enabled_tools`, so switching a toggle in the sidebar
    changes what the model can use on the very next message (dynamic tool binding).
    """
    tools = _enabled(state, deps)
    system = copilot_system_message(
        today=deps.today(),
        tone=deps.get_preferences().tone,
        tools=[t.name for t in tools],
        profile_block=_profile_block(deps.get_profile()),
    )
    llm = deps.llm.bind_tools(tools) if (tools and deps.llm is not None) else deps.llm
    response = invoke_chat(llm, [system, *recent_messages(state["messages"])])
    return {"messages": [response]}


def approve(state: CopilotState, deps: CopilotDeps) -> Command:
    """Pause before a write tool runs; continue with the user's yes or no.

    Resume with `Command(resume={"approved": True})`. On "no", every tool call of that step
    gets a "declined" result, and the agent tells the user nothing was changed.

    Raises:
        pydantic.ValidationError: the resume value is not a valid ToolApprovalDecision.
    """
    calls = state["messages"][-1].tool_calls
    actions = [
        {
            "id": c["id"],
            "tool": c["name"],
            "args": c["args"],
            "summary": deps.describe_action(c["name"], c["args"]),
        }
        for c in calls
        if c["name"] in deps.approval_tools
    ]
    decision = ToolApprovalDecision.model_validate(
        interrupt({"type": "tool_approval", "actions": actions})
    )
    if decision.approved:
        return Command(goto="tools")
    results = [
        ToolMessage(
            DECLINED_MESSAGE if c["name"] in deps.approval_tools else SKIPPED_MESSAGE,
            tool_call_id=c["id"],
            name=c["name"],
        )
        for c in calls
    ]
    return Command(goto="agent", update={"messages": results})


def run_tools(state: CopilotState, deps: CopilotDeps) -> dict:
    """The "act" step: run the requested tools and add their results as ToolMessages.

    Only enabled tools can run. If the model asks for another one, ToolNode answers with an
    error message instead of running it.
    """
    node = ToolNode(_enabled(state, deps), handle_tool_errors=_tool_error)
    return node.invoke(state)


# ---------------------------------------------------------------- edges and wiring


def _after_guard(state: CopilotState) -> str:
    # The guard answered by itself (e.g. off-topic): nothing for the agent to do.
    return END if isinstance(state["messages"][-1], AIMessage) else "agent"


def _after_agent(state: CopilotState, deps: CopilotDeps) -> str:
    calls = getattr(state["messages"][-1], "tool_calls", None)
    if not calls:
        return END  # a normal answer: the turn is over
    if any(c["name"] in deps.approval_tools for c in calls):
        return "approve"
    return "tools"


def build_copilot_graph(
    deps: CopilotDeps, checkpointer: BaseCheckpointSaver | None
) -> CompiledStateGraph:
    """Build and compile the chat graph. Run it with `chat_config("chat-…")`."""
    graph = StateGraph(CopilotState)
    graph.add_node("input_guard", partial(input_guard, deps=deps))
    graph.add_node("agent", partial(agent, deps=deps))
    graph.add_node("approve", partial(approve, deps=deps), destinations=("tools", "agent"))
    graph.add_node("tools", partial(run_tools, deps=deps))

    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges("input_guard", _after_guard, ["agent", END])
    graph.add_conditional_edges(
        "agent", partial(_after_agent, deps=deps), ["tools", "approve", END]
    )
    graph.add_edge("tools", "agent")  # observe the results, then reason again
    return graph.compile(checkpointer=checkpointer)


def default_copilot_deps() -> CopilotDeps:
    """The real services: OpenRouter LLM, the registry's tools, memory from the Store."""
    from app.agent.llm import get_llm
    from app.config import get_settings
    from app.memory.store import get_preferences, get_profile
    from app.tools.registry import ALL_TOOLS, APPROVAL_TOOLS
    from app.tools.tracker import describe_update

    def describe(name: str, args: dict) -> str:
        try:
            if name == "update_tracker":
                return describe_update(**args)
        except (TypeError, ValueError):
            pass  # odd arguments from the model: show them raw, the tool will reject them
        return f"{name}({args})"

    return CopilotDeps(
        llm=get_llm() if get_settings().has_llm_key else None,
        tools=ALL_TOOLS,
        approval_tools=APPROVAL_TOOLS,
        describe_action=describe,
        get_profile=get_profile,
        get_preferences=get_preferences,
    )


@lru_cache
def get_copilot_graph() -> CompiledStateGraph:
    """The app's chat graph, saving threads in data/runtime/checkpoints.db (like the jobs)."""
    from app.agent.application_graph import make_checkpointer
    from app.config import get_settings

    return build_copilot_graph(
        default_copilot_deps(), make_checkpointer(get_settings().runtime_dir / "checkpoints.db")
    )


def chat_config(thread_id: str) -> dict:
    """The config for one chat thread, e.g. `chat_config("chat-1a2b3c")`."""
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
