# What: everything the Copilot chat page does: send a message and stream the answer as events
#       (text tokens, tool calls, tool results, approval requests, errors), resume after an
#       approval, retry an answer that broke off, and rebuild the chat history from the
#       checkpointer. Each answer's tokens are logged as the "chat" action.
# Why: the page only draws; this layer runs the copilot graph, turns its stream into simple
#      events, and converts every failure into a friendly message (§10).
# Used by: app/ui/pages/3_copilot_chat.py, tests/test_copilot_service.py.

import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.copilot_graph import chat_config, get_copilot_graph
from app.config import get_settings
from app.services.observability import track_run
from app.tools.registry import TOOL_LABELS, enabled_tool_names
from app.utils.errors import ConfigError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

STARTER_PROMPTS = [
    "What should I apply to next?",
    "Which of my skills come up most in job postings?",
    "Summarise my applications this week",
]
RESULT_PREVIEW_CHARS = 1_500
RECURSION_MESSAGE = (
    "I stopped because this needed too many steps. Please ask a narrower question, or split "
    "it into two."
)
NOT_WAITING_MESSAGE = "There is no action waiting for your approval."


@dataclass
class ChatEvent:
    """One thing that happened while the copilot worked, for the page to draw live."""

    kind: Literal["token", "tool_call", "tool_result", "approval", "error"]
    text: str = ""
    tool: str = ""  # the tool name, for tool_call / tool_result


@dataclass
class ToolUse:
    """One tool call in the history, shown as a "🔧 Used: …" expander."""

    name: str
    label: str
    args: dict
    result: str = ""


@dataclass
class ChatTurn:
    """One message bubble: the user's message, or the copilot's answer with its tool uses."""

    role: Literal["user", "assistant"]
    text: str = ""
    tools: list[ToolUse] = field(default_factory=list)


@dataclass
class PendingAction:
    """A write action waiting for the user's approval."""

    tool: str
    summary: str
    args: dict


def get_graph() -> CompiledStateGraph:
    """The compiled chat graph. Tests replace this function."""
    return get_copilot_graph()


def new_thread_id() -> str:
    """A fresh chat thread id, e.g. "chat-3f9c2a1b7d04"."""
    return f"chat-{uuid.uuid4().hex[:12]}"


def label_for(tool: str) -> str:
    """The friendly name of a tool, e.g. "🏢 Research companies"."""
    return TOOL_LABELS.get(tool, tool)


# ---------------------------------------------------------------- running


def _events(payload: object, thread_id: str) -> Iterator[ChatEvent]:
    """Run the graph and translate its stream into ChatEvents.

    Two stream modes at once: "messages" gives the LLM's answer token by token, "updates"
    gives each finished node (tool calls, tool results, the approval pause).
    """
    streamed_text = False  # did the current answer arrive as tokens already?
    stream = get_graph().stream(
        payload, chat_config(thread_id), stream_mode=["messages", "updates"]
    )
    for mode, data in stream:
        if mode == "messages":
            chunk, meta = data
            if (
                isinstance(chunk, AIMessageChunk)
                and meta.get("langgraph_node") == "agent"
                and isinstance(chunk.content, str)
                and chunk.content
            ):
                streamed_text = True
                yield ChatEvent("token", chunk.content)
            continue

        for node, update in data.items():
            if node == "__interrupt__":
                yield ChatEvent("approval")
                continue
            messages = (update or {}).get("messages", [])
            for message in messages:
                if isinstance(message, AIMessage):
                    # A model that doesn't stream (or the guard's own answer) sends its text
                    # only here; send it once.
                    if message.content and not streamed_text:
                        yield ChatEvent("token", str(message.content))
                    for call in message.tool_calls:
                        yield ChatEvent("tool_call", label_for(call["name"]), call["name"])
                    streamed_text = False
                elif isinstance(message, ToolMessage):
                    yield ChatEvent("tool_result", str(message.content), message.name or "")


def _safely(events: Iterable[ChatEvent], thread_id: str) -> Iterator[ChatEvent]:
    """Pass events through; turn any failure into one friendly "error" event.

    Also counts the tokens of the whole answer as the "chat" action (§11).
    """
    try:
        with track_run("chat", thread_id=thread_id):
            yield from events
    except GraphRecursionError:
        log_exception(logger, "Chat hit the recursion limit", thread_id=thread_id)
        yield ChatEvent("error", RECURSION_MESSAGE)
    except Exception as exc:
        log_exception(logger, "Chat failed", thread_id=thread_id)
        yield ChatEvent("error", friendly_message(exc))


def send_message(thread_id: str, text: str, toggles: Iterable[str]) -> Iterator[ChatEvent]:
    """Send the user's message and stream the copilot's work as events.

    Args:
        thread_id: the chat thread, from `new_thread_id()`.
        text: what the user typed.
        toggles: the tool toggles switched on in the sidebar (registry names).
    """
    if not get_settings().has_llm_key:
        yield ChatEvent("error", ConfigError.user_message)
        return
    payload = {
        "messages": [HumanMessage(text)],
        "enabled_tools": enabled_tool_names(toggles),
    }
    yield from _safely(_events(payload, thread_id), thread_id)


def answer_approval(thread_id: str, approved: bool) -> Iterator[ChatEvent]:
    """Resume after the approval card: run the write action, or tell the agent it was declined."""
    if not pending_approval(thread_id):
        yield ChatEvent("error", NOT_WAITING_MESSAGE)
        return
    yield from _safely(_events(Command(resume={"approved": approved}), thread_id), thread_id)


def can_retry(thread_id: str) -> bool:
    """True if the last answer broke off (e.g. no internet) and can continue where it stopped."""
    state = get_graph().get_state(chat_config(thread_id))
    waiting_for_user = any(task.interrupts for task in state.tasks)
    return bool(state.next) and not waiting_for_user


def retry(thread_id: str) -> Iterator[ChatEvent]:
    """Continue a broken-off answer from the last saved step (no duplicate user message)."""
    if not get_settings().has_llm_key:
        yield ChatEvent("error", ConfigError.user_message)
        return
    if not can_retry(thread_id):
        yield ChatEvent("error", "There is nothing to retry.")
        return
    # `None` as input means: run the steps that are still pending in the checkpoint.
    yield from _safely(_events(None, thread_id), thread_id)


# ---------------------------------------------------------------- reading the thread


def get_history(thread_id: str) -> list[ChatTurn]:
    """The conversation as message bubbles, rebuilt from the checkpointer on every rerun."""
    messages = get_graph().get_state(chat_config(thread_id)).values.get("messages", [])
    turns: list[ChatTurn] = []
    uses: dict[str, ToolUse] = {}
    for message in messages:
        if isinstance(message, HumanMessage):
            turns.append(ChatTurn("user", str(message.content)))
            continue
        if not turns or turns[-1].role != "assistant":
            turns.append(ChatTurn("assistant"))
        turn = turns[-1]
        if isinstance(message, AIMessage):
            if message.content:
                turn.text = f"{turn.text}\n\n{message.content}".strip()
            for call in message.tool_calls:
                use = ToolUse(call["name"], label_for(call["name"]), call["args"])
                uses[call["id"]] = use
                turn.tools.append(use)
        elif isinstance(message, ToolMessage) and message.tool_call_id in uses:
            uses[message.tool_call_id].result = str(message.content)[:RESULT_PREVIEW_CHARS]
    return turns


def pending_approval(thread_id: str) -> list[PendingAction]:
    """The write actions waiting for the user's OK (empty if nothing is waiting)."""
    state = get_graph().get_state(chat_config(thread_id))
    actions = []
    for task in state.tasks:
        for item in task.interrupts:
            value = item.value if isinstance(item.value, dict) else {}
            if value.get("type") == "tool_approval":
                actions += [
                    PendingAction(a["tool"], a["summary"], a["args"]) for a in value["actions"]
                ]
    return actions
