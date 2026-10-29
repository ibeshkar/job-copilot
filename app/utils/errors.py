# What: the app's own exception types, each carrying a friendly message for the user.
# Why: the UI must never show a stack trace (PROJECT_PLAN.md §10). Code raises these, and the
#      UI shows `user_message` while the technical details go to the log.
#      `friendly_message()` also translates raw library errors (no internet, rejected key,
#      too many steps) that reach the UI without being wrapped first.
# Used by: app/agent/llm.py, app/db/repo.py, the graph nodes, app/tools/*, guards, services
#          and UI pages.

import httpx
import openai
from langgraph.errors import GraphRecursionError

NETWORK_MESSAGE = "I can't reach the AI service. Please check your internet connection, then retry."
KEY_REJECTED_MESSAGE = "OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env."
RECURSION_MESSAGE = (
    "I stopped because this needed too many steps. Your progress is saved; please retry or "
    "ask for something smaller."
)


class AppError(Exception):
    """Base class for all expected errors. `user_message` is safe to show in the UI."""

    user_message: str = "Something went wrong. Please try again."

    def __init__(self, detail: str = "", user_message: str | None = None) -> None:
        super().__init__(detail or self.user_message)
        if user_message is not None:
            self.user_message = user_message


class ConfigError(AppError):
    """A required setting (usually an API key) is missing or invalid."""

    user_message = (
        "The OpenRouter API key is missing. Set `OPENROUTER_API_KEY` in the `.env` file "
        "and restart the app."
    )


class LLMServiceError(AppError):
    """The AI service timed out, was rate limited or returned a server error."""

    user_message = "The AI service is busy, please retry."


class StructuredOutputError(AppError):
    """The LLM's answer did not match the expected Pydantic model, even after a retry."""

    user_message = "The AI gave an answer I couldn't understand. Please try again."


class FetchError(AppError):
    """A job URL could not be downloaded or read."""

    user_message = "I couldn't read that page. Please paste the job text instead."


class GuardBlockedError(AppError):
    """An input guard rejected the input (too long, off-topic, injection, unsafe URL)."""

    user_message = "This input can't be processed. Please check it and try again."


class DocumentError(AppError):
    """An uploaded document is not allowed (type, size) or has no readable text."""

    user_message = "This document can't be added. Use PDF, DOCX, TXT or MD files up to 5 MB."


class ExternalServiceError(AppError):
    """An outside service (Tavily, Adzuna) is down, rejected the key or returned nothing useful."""

    user_message = "That service isn't available right now. Please try again later."


class NotFoundError(AppError):
    """A database record (job, draft, ...) does not exist."""

    user_message = "That item no longer exists. It may have been deleted."


def _library_message(exc: BaseException) -> str | None:
    """A friendly message for well-known errors from the libraries we use, else None."""
    # Order matters: APITimeoutError is a subclass of APIConnectionError.
    if isinstance(exc, openai.APITimeoutError | openai.RateLimitError | openai.InternalServerError):
        return LLMServiceError.user_message
    if isinstance(exc, openai.APIConnectionError | httpx.ConnectError | httpx.TimeoutException):
        return NETWORK_MESSAGE
    if isinstance(exc, openai.AuthenticationError):
        return KEY_REJECTED_MESSAGE
    if isinstance(exc, GraphRecursionError):
        return RECURSION_MESSAGE
    return None


def friendly_message(exc: BaseException) -> str:
    """Return a message that is safe to show the user for any exception.

    Args:
        exc: the exception that was caught.

    Returns:
        The exception's `user_message` if it is an `AppError`; a specific message for known
        library errors (also when they are the `__cause__` of another error, e.g. inside a
        tool); otherwise a generic message. Never a stack trace.
    """
    current: BaseException | None = exc
    for _ in range(3):  # look a few links down the "raise ... from ..." chain
        if current is None:
            break
        if isinstance(current, AppError):
            return current.user_message
        if (message := _library_message(current)) is not None:
            return message
        current = current.__cause__
    return AppError.user_message
