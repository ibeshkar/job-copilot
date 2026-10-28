# What: the app's own exception types, each carrying a friendly message for the user.
# Why: the UI must never show a stack trace (PROJECT_PLAN.md §10). Code raises these, and the
#      UI shows `user_message` while the technical details go to the log.
# Used by: app/agent/llm.py, app/db/repo.py, and later the graph nodes, tools, guards and UI pages.


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


class NotFoundError(AppError):
    """A database record (job, draft, ...) does not exist."""

    user_message = "That item no longer exists. It may have been deleted."


def friendly_message(exc: BaseException) -> str:
    """Return a message that is safe to show the user for any exception.

    Args:
        exc: the exception that was caught.

    Returns:
        The exception's `user_message` if it is an `AppError`, otherwise a generic message.
    """
    if isinstance(exc, AppError):
        return exc.user_message
    return AppError.user_message
