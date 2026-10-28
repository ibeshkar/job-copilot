# What: `get_llm()` and `get_embeddings()`, the factories for the chat and embedding models
#       (both served by OpenRouter).
# Why: every graph node and the copilot get their model from here, so the model name,
#      temperature and max tokens come from settings (or the dev settings page) in one place.
# Used by: app/rag/ingest.py (embeddings), later the graph nodes, copilot graph and guards.

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import Settings, get_settings
from app.utils.errors import ConfigError


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
