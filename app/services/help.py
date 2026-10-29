# What: the help-bot on the Help page: answers "how do I…?" questions about the app from
#       docs/USER_GUIDE.md only, with the guide sections it used.
# Why: an easy, cheap way to explain the app inside the app. The guide is small (a few thousand
#      tokens), so it goes into the prompt whole instead of through RAG: one LLM call, no
#      chunking, and the model always sees the full context. Logged as the "help_bot" action.
# Used by: app/ui/pages/5_help.py, tests/test_help.py.

from dataclasses import dataclass
from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from app.agent.llm import get_llm, invoke_structured
from app.agent.prompts import help_bot_messages
from app.agent.state import HelpAnswer
from app.config import PROJECT_ROOT, get_settings
from app.guards.input_guard import check_chat_message
from app.memory.store import get_preferences
from app.services.observability import track_run
from app.utils.errors import AppError, friendly_message
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

GUIDE_PATH = PROJECT_ROOT / "docs" / "USER_GUIDE.md"
HISTORY_TURNS = 3  # earlier questions sent along, so "and how do I undo it?" makes sense
NO_KEY_MESSAGE = (
    "The help-bot needs the OpenRouter API key (`OPENROUTER_API_KEY` in `.env`). Meanwhile, the "
    "FAQ above and docs/USER_GUIDE.md answer most questions."
)


@dataclass
class HelpReply:
    """One help-bot answer. `ok=False` means `answer` is an error message."""

    ok: bool
    answer: str
    sections: list[str]
    covered_by_guide: bool = True


@lru_cache
def load_guide() -> str:
    """The user guide's text (read once per process)."""
    return GUIDE_PATH.read_text(encoding="utf-8")


def guide_headings() -> set[str]:
    """The "## " section headings of the guide, e.g. "Use case 3: \"Prepare me for the
    interview\""."""
    return {line[3:].strip() for line in load_guide().splitlines() if line.startswith("## ")}


def ask(
    question: str, history: list[tuple[str, str]] | None = None, llm: BaseChatModel | None = None
) -> HelpReply:
    """Answer a question about using the app. Never raises.

    Args:
        question: what the user typed.
        history: earlier (question, answer) pairs of this browser session, oldest first.
        llm: a chat model; defaults to `get_llm()` (tests pass a fake).
    """
    check = check_chat_message(question)
    if not check.allowed:
        return HelpReply(False, check.reason, [])
    if llm is None and not get_settings().has_llm_key:
        return HelpReply(False, NO_KEY_MESSAGE, [])

    earlier = "\n".join(f"Q: {q}\nA: {a}" for q, a in (history or [])[-HISTORY_TURNS:])
    try:
        with track_run("help_bot"):
            reply = invoke_structured(
                llm or get_llm(),
                HelpAnswer,
                help_bot_messages(load_guide(), question.strip(), earlier, get_preferences().tone),
            )
    except AppError as exc:
        return HelpReply(False, exc.user_message, [])
    except Exception as exc:
        log_exception(logger, "Help-bot failed")
        return HelpReply(False, friendly_message(exc), [])
    # Only headings that really exist in the guide are shown as sources.
    sections = [s for s in reply.sections if s.strip() in guide_headings()]
    return HelpReply(True, reply.answer.strip(), sections, reply.covered_by_guide)
