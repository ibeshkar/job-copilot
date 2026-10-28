# What: `get_llm()` and `get_embeddings()`, the factories for the chat and embedding models
#       (both served by OpenRouter), and `invoke_structured()`, the one way nodes call the LLM
#       when they need data back.
# Why: every graph node and the copilot get their model from here, so the model name,
#      temperature and max tokens come from settings in one place. `invoke_structured` adds the
#      error handling from PROJECT_PLAN.md §10: retries for busy servers and one retry for
#      answers that don't match the Pydantic schema.
# Used by: app/rag/ingest.py (embeddings), app/agent/nodes/*.py, app/agent/application_graph.py.

from typing import Any, TypeVar

import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.agent.prompts import structured_retry_message
from app.config import Settings, get_settings
from app.utils.errors import ConfigError, LLMServiceError, StructuredOutputError
from app.utils.logging import get_logger

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

# Errors worth retrying: the request may work a few seconds later.
_TRANSIENT_ERRORS = (
    openai.APIConnectionError,
    openai.APITimeoutError,
    openai.RateLimitError,
    openai.InternalServerError,
)


def get_llm(
    settings: Settings | None = None,
    *,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> ChatOpenAI:
    """Create a chat model. Arguments override the values from settings.

    Args:
        settings: the app settings; defaults to `get_settings()`.
        model: OpenRouter model id, e.g. "openai/gpt-4o-mini".
        temperature: 0 = predictable, higher = more creative.
        max_tokens: upper limit on the length of the answer.

    Returns:
        A configured `ChatOpenAI` instance.

    Raises:
        ConfigError: no OpenRouter API key is set.
    """
    settings = settings or get_settings()
    if not settings.has_llm_key:
        raise ConfigError("OPENROUTER_API_KEY is not set")

    return ChatOpenAI(
        model=model or settings.default_model,
        temperature=settings.temperature if temperature is None else temperature,
        max_tokens=max_tokens or settings.max_tokens,
        # OpenRouter is OpenAI-compatible: same client, different URL and key.
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        # Optional header that shows the app's name in the OpenRouter dashboard.
        default_headers={"X-Title": "Job Application Copilot"},
        timeout=60,
        # Network retries are handled by `tenacity` around our calls (PROJECT_PLAN.md §10),
        # so the client's own retries stay low to avoid retrying 3 x 3 = 9 times.
        max_retries=1,
        # Return token counts in the stream too, needed for cost tracking.
        stream_usage=True,
    )


def get_embeddings(settings: Settings | None = None) -> OpenAIEmbeddings:
    """Create the embedding model that turns text into vectors for the knowledge base.

    Raises:
        ConfigError: no OpenRouter API key is set.
    """
    settings = settings or get_settings()
    if not settings.has_llm_key:
        raise ConfigError("OPENROUTER_API_KEY is not set")

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        # The length check uses OpenAI's tokenizer, which doesn't know OpenRouter model ids
        # like "openai/text-embedding-3-small". Our chunks are ~500 chars, far below the limit.
        check_embedding_ctx_length=False,
        max_retries=2,
    )


@retry(
    retry=retry_if_exception_type(_TRANSIENT_ERRORS),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),  # 1s, 2s, then give up
    reraise=True,
)
def _invoke_with_retry(runnable: Any, messages: list[BaseMessage]) -> dict[str, Any]:
    return runnable.invoke(messages)


def invoke_structured(llm: BaseChatModel | None, schema: type[T], messages: list[BaseMessage]) -> T:
    """Call the LLM and get back an instance of `schema` (structured output).

    Busy or unreachable servers are retried 3 times with growing waits. If the answer doesn't
    match the schema, the model gets one more try with the validation error added.

    Args:
        llm: the chat model (from `get_llm()`, or a fake in tests).
        schema: the Pydantic model the answer must match.
        messages: the prompt, built by a helper in app/agent/prompts.py.

    Returns:
        The parsed answer.

    Raises:
        LLMServiceError: the service stayed busy or unreachable.
        ConfigError: no API key (llm is None), or the key was rejected.
        StructuredOutputError: the answer still didn't match the schema after the retry.
    """
    if llm is None:
        raise ConfigError("OPENROUTER_API_KEY is not set")
    structured = llm.with_structured_output(schema, include_raw=True)
    attempt_messages = list(messages)
    for attempt in (1, 2):
        try:
            result = _invoke_with_retry(structured, attempt_messages)
        except openai.AuthenticationError as exc:
            raise ConfigError(
                "OpenRouter rejected the API key",
                user_message="OpenRouter rejected the API key. Check OPENROUTER_API_KEY in .env.",
            ) from exc
        except _TRANSIENT_ERRORS as exc:
            raise LLMServiceError(str(exc)) from exc
        except openai.LengthFinishReasonError as exc:
            # The answer hit max_tokens and was cut off. Asking again gives the same result,
            # so fail with a clear message instead of a stack trace.
            logger.warning("Structured output for %s hit the token limit", schema.__name__)
            raise StructuredOutputError(f"{schema.__name__} answer was cut off") from exc

        if result.get("parsing_error") is None and result.get("parsed") is not None:
            return result["parsed"]

        error = str(result.get("parsing_error") or "empty answer")
        logger.warning(
            "Structured output failed for %s (attempt %d): %s | raw=%r",
            schema.__name__,
            attempt,
            error,
            result.get("raw"),
        )
        attempt_messages = [*messages, structured_retry_message(error)]

    raise StructuredOutputError(f"{schema.__name__} did not validate after a retry")
