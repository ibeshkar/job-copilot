# What: typed application settings loaded from `.env`, plus the model price table.
# Why: one place to read keys, model choice and file paths, so no other module touches
#      os.environ directly and secrets never get hard-coded.
# Used by: app/main.py, app/agent/llm.py, app/db/repo.py, app/utils/logging.py
#          (and later the usage/cost service and the dev settings page).

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
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
    adzuna_app_id: str | None = None
    adzuna_app_key: SecretStr | None = None

    # Observability
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "job-copilot"

    # Developer settings page password
    app_dev_password: SecretStr = SecretStr("change-me")

    # LLM defaults (the dev settings page can override these per session)
    default_model: str = "openai/gpt-4o-mini"
    temperature: float = 0.2
    max_tokens: int = 1500
    embedding_model: str = "openai/text-embedding-3-small"

    # Where SQLite databases, Chroma and logs live (gitignored)
    runtime_dir: Path = PROJECT_ROOT / "data" / "runtime"

    @property
    def has_llm_key(self) -> bool:
        """True when an OpenRouter key is configured, so LLM features can run."""
        return self.openrouter_api_key is not None

    @property
    def app_db_path(self) -> Path:
        """SQLite file for the tracker tables (jobs, drafts, usage, ...)."""
        return self.runtime_dir / "app.db"

    @property
    def app_db_url(self) -> str:
        """SQLAlchemy URL for the app database."""
        return f"sqlite:///{self.app_db_path}"


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once per process.

    Also makes sure the runtime folder exists, because the database and logs are written there.
    """
    settings = Settings()
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    return settings
