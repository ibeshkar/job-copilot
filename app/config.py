# What: typed application settings loaded from `.env`, plus the model price table.
# Why: one place to read keys, model choice and file paths, so no other module touches
#      os.environ directly and secrets never get hard-coded.
# Used by: almost every module; app/services/usage.py (prices), app/services/dev_settings.py
#          (writes the model overrides that `get_settings()` applies).

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Prices in US dollars per 1 million tokens, keyed by OpenRouter model id ("provider/model").
# These change often: check https://openrouter.ai/models before trusting the cost numbers.
MODEL_PRICING: dict[str, dict[str, float]] = {
    "openai/gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "openai/gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "openai/gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "openai/gpt-4o": {"input": 2.50, "output": 10.00},
    "openai/text-embedding-3-small": {"input": 0.02, "output": 0.0},
}
# Chat models offered in the developer settings (embedding models can't chat).
CHAT_MODELS = [m for m in MODEL_PRICING if "embedding" not in m]
DEV_OVERRIDES_FILE = "dev_settings.json"  # in runtime_dir; written by the dev settings page


class LLMOverrides(BaseModel):
    """Model settings chosen on the developer settings page. They win over `.env`."""

    default_model: str = Field(min_length=3)
    temperature: float = Field(ge=0.0, le=2.0)
    max_tokens: int = Field(ge=64, le=16_000)


class Settings(BaseSettings):
    """All runtime settings. Each field maps to an env var of the same name in upper case."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # `OPENROUTER_API_KEY=` (empty) in .env should count as "not set", not as "".
        env_ignore_empty=True,
        extra="ignore",
    )

    # LLM access goes through OpenRouter, which speaks the OpenAI API format, so the
    # `langchain-openai` client works with a different base URL.
    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Other API keys: SecretStr hides the value in logs and reprs.
    tavily_api_key: SecretStr | None = None

    # Observability
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "job-copilot"
    # EU accounts need https://eu.api.smith.langchain.com; a key from the other region gets 403
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    # True = LangSmith traces show the run structure, tokens and timing but not the texts
    # (CV data, job postings). PROJECT_PLAN.md §9.
    langsmith_hide_inputs: bool = False

    # Developer settings page password
    app_dev_password: SecretStr = SecretStr("change-me")

    # LLM defaults (the dev settings page can override these per session)
    default_model: str = "openai/gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 1500
    embedding_model: str = "openai/text-embedding-3-small"

    # Cost guard (§9): the sidebar warns at 80% of this many tokens per browser session.
    session_token_budget: int = 200_000
    # Ask OpenRouter at startup whether the key works (§10). Tests switch this off.
    startup_key_check: bool = True

    # Where SQLite databases, Chroma and logs live (gitignored)
    runtime_dir: Path = PROJECT_ROOT / "data" / "runtime"

    @property
    def has_llm_key(self) -> bool:
        """True when an OpenRouter key is configured, so LLM features can run."""
        return self.openrouter_api_key is not None

    @property
    def has_tavily_key(self) -> bool:
        """True when company research (Tavily) can run."""
        return self.tavily_api_key is not None

    @property
    def app_db_path(self) -> Path:
        """SQLite file for the tracker tables (jobs, drafts, usage, ...)."""
        return self.runtime_dir / "app.db"

    @property
    def chroma_dir(self) -> Path:
        """Folder of the persistent Chroma vector store (the knowledge base)."""
        return self.runtime_dir / "chroma"

    @property
    def memory_db_path(self) -> Path:
        """SQLite file for the LangGraph Store (long-term memory: profile, preferences, ...)."""
        return self.runtime_dir / "memory.db"

    @property
    def app_db_url(self) -> str:
        """SQLAlchemy URL for the app database."""
        return f"sqlite:///{self.app_db_path}"


def load_llm_overrides(runtime_dir: Path) -> LLMOverrides | None:
    """The saved developer-settings overrides, or None if there are none (or they're broken)."""
    path = runtime_dir / DEV_OVERRIDES_FILE
    if not path.exists():
        return None
    try:
        return LLMOverrides.model_validate(json.loads(path.read_text()))
    except (ValueError, ValidationError):
        return None  # a hand-edited, broken file must not stop the app; .env values apply


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once per process.

    Order of priority: developer-settings overrides (`dev_settings.json`) > `.env` > defaults.
    Also makes sure the runtime folder exists, because the database and logs are written there.
    """
    settings = Settings()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    overrides = load_llm_overrides(settings.runtime_dir)
    if overrides is not None:
        settings = settings.model_copy(update=overrides.model_dump())
    return settings
