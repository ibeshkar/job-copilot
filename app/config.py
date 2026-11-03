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
# Chat models offered in the developer settings (embedding models can't chat). Only these can be
# saved there (app/validation/models.py SettingsInput).
CHAT_MODELS = [m for m in MODEL_PRICING if "embedding" not in m]
# The most output tokens each chat model can return in one answer (OpenRouter model pages).
MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "openai/gpt-4o-mini": 16_384,
    "openai/gpt-4.1-mini": 32_768,
    "openai/gpt-4.1-nano": 32_768,
    "openai/gpt-4o": 16_384,
}
DEV_OVERRIDES_FILE = "dev_settings.json"  # in runtime_dir; written by the dev settings page


# ---------------------------------------------------------------- input validation limits
# Every length and range the validators in app/validation/ check. Change them here only; the
# UI hints ("max 100 characters") and the error messages read the same numbers.

# Profile
NAME_MIN_CHARS, NAME_MAX_CHARS = 2, 100
PLACE_MAX_CHARS = 100  # a city, country or location
LIST_MAX_ITEMS = 10  # target roles, locations, languages, deal-breakers
LIST_ITEM_MIN_CHARS, LIST_ITEM_MAX_CHARS = 2, 80
SHORT_TEXT_MAX_CHARS = 200  # work authorisation, next action, a recipient line, a subject
URL_MAX_CHARS = 2_000
PHONE_DEFAULT_REGION = "DE"  # numbers without +/00 are read as German numbers
# Expected salary, as yearly gross after converting monthly / hourly figures.
SALARY_MIN_YEARLY, SALARY_MAX_YEARLY = 10_000, 500_000
# Currencies the salary field accepts, with the marks that identify them. The first is the default.
SALARY_CURRENCIES: dict[str, tuple[str, ...]] = {
    "EUR": ("€", "eur", "euro"),
    "USD": ("$", "usd"),
    "GBP": ("£", "gbp"),
    "CHF": ("chf",),
}
LETTER_LANGUAGE_MAX_CHARS = 50

# Job posting (pasted text or a link)
JOB_TEXT_MIN_CHARS = 200  # shorter than a few sentences can't be a real posting
JOB_TEXT_MAX_CHARS = 30_000  # PROJECT_PLAN.md §9
JOB_TITLE_MAX_CHARS = 200
COMPANY_MAX_CHARS = 200

# Free text the user writes
NOTES_MAX_CHARS = 5_000
TRACKER_NOTE_MAX_CHARS = 500  # one note the copilot adds
CONTEXT_MIN_CHARS, CONTEXT_MAX_CHARS = 10, 2_000  # "Add context and re-check"
CLAIM_MIN_CHARS, CLAIM_MAX_CHARS = 5, 500
FACT_MIN_CHARS, FACT_MAX_CHARS = 5, 500
STYLE_RULE_MIN_CHARS, STYLE_RULE_MAX_CHARS = 5, 300
PARAGRAPH_MAX_CHARS = 2_000  # one cover letter paragraph
LETTER_MAX_PARAGRAPHS = 8
CLOSING_MAX_CHARS = 100
CV_BULLET_MAX_CHARS = 400
CV_MAX_BULLETS = 15
FEEDBACK_COMMENT_MAX_CHARS = 300  # the 👎 comment
INSTRUCTION_MAX_CHARS = 500  # "Regenerate a section: what should change?"
FOCUS_MAX_CHARS = 200  # company research focus
CHAT_MIN_CHARS, CHAT_MAX_CHARS = 2, 4_000  # PROJECT_PLAN.md §9
ANSWER_MIN_CHARS, ANSWER_MAX_CHARS = 10, 4_000  # a 3-minute spoken answer ≈ 2,500 characters
PASSWORD_MAX_CHARS = 200

# Developer settings
TEMPERATURE_MIN, TEMPERATURE_MAX = 0.0, 2.0
MAX_TOKENS_MIN = 64

# Uploads
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB per file (PROJECT_PLAN.md §9)
MAX_FILES_PER_UPLOAD = 10
UPLOAD_EXTENSIONS = (".pdf", ".docx", ".md", ".txt")
MIN_DOCUMENT_CHARS = 20  # less than this isn't a document worth searching


class LLMOverrides(BaseModel):
    """Model settings chosen on the developer settings page. They win over `.env`."""

    default_model: str = Field(min_length=3)
    temperature: float = Field(ge=0.0, le=2.0)
    max_tokens: int = Field(ge=64, le=max(MODEL_MAX_OUTPUT_TOKENS.values()))


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
