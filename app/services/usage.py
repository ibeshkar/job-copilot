# What: token and cost accounting (PROJECT_PLAN.md §11). `track_usage("draft")` wraps one user
#       action, counts the tokens of every LLM call inside it, prices them with MODEL_PRICING
#       and saves one UsageLog row per model. Also: session totals, the per-action report and
#       the budget warning for the sidebar.
# Why: LLM calls cost money. The user sees what each action and the whole session cost, and
#      the developer settings show which actions are the expensive ones.
# Used by: app/services/applications.py, app/services/copilot.py, app/services/company.py,
#          app/main.py (sidebar badge), app/ui/pages/9_dev_settings.py.

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tracers.context import register_configure_hook

from app.config import MODEL_PRICING
from app.db import repo
from app.utils.logging import get_logger, log_exception

logger = get_logger(__name__)

BUDGET_WARNING_SHARE = 0.8  # warn at 80% of the session budget (§9)

# LangChain adds the handler in this variable to every LLM call made while it is set,
# including calls inside graph nodes that run in other threads (LangGraph copies the context).
# Registered once here; `get_usage_metadata_callback()` would register a new hook per call.
_current_handler: ContextVar[UsageMetadataCallbackHandler | None] = ContextVar(
    "job_copilot_usage", default=None
)
register_configure_hook(_current_handler, inheritable=True)


@dataclass
class Usage:
    """Tokens and cost of one action or a whole session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def label(self) -> str:
        """Short text for the UI, e.g. "12.4k tokens · $0.0062"."""
        tokens = self.total_tokens
        amount = f"{tokens / 1000:.1f}k" if tokens >= 1000 else str(tokens)
        return f"{amount} tokens · ${self.cost_usd:.4f}"


# ---------------------------------------------------------------- prices


def price_for(model: str) -> dict[str, float] | None:
    """The $/1M-token prices for a model id, or None if it isn't in MODEL_PRICING.

    Providers often answer with a dated name ("openai/gpt-4o-mini-2024-07-18") or without the
    "openai/" prefix, so the longest table entry the name starts with wins.
    """
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    candidates = []
    for key in MODEL_PRICING:
        short = key.split("/", 1)[-1]
        if model.startswith(key) or model.startswith(short):
            candidates.append(key)
    if not candidates:
        return None
    return MODEL_PRICING[max(candidates, key=len)]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price of one call in US dollars (0.0 for a model with unknown prices)."""
    prices = price_for(model)
    if prices is None:
        logger.warning("No price for model %r; counting its cost as $0", model)
        return 0.0
    return (input_tokens * prices["input"] + output_tokens * prices["output"]) / 1_000_000


# ---------------------------------------------------------------- tracking


def _save(action: str, handler: UsageMetadataCallbackHandler, usage: Usage) -> None:
    """Price the counted tokens, fill `usage` and write one UsageLog row per model."""
    for model, meta in handler.usage_metadata.items():
        input_tokens = int(meta.get("input_tokens", 0))
        output_tokens = int(meta.get("output_tokens", 0))
        cost = cost_usd(model, input_tokens, output_tokens)
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens
        usage.cost_usd += cost
        with repo.session_scope() as session:
            repo.log_usage(session, action, model, input_tokens, output_tokens, cost)


@contextmanager
def track_usage(action: str) -> Iterator[Usage]:
    """Count the tokens of every LLM call inside the `with` block and log them as `action`.

    The yielded `Usage` is filled in when the block ends, also if it ends with an error
    (tokens spent before a failure still cost money).

    Example:
        with track_usage("draft") as usage:
            graph.invoke(...)
        print(usage.label())
    """
    usage = Usage()
    handler = UsageMetadataCallbackHandler()
    token = _current_handler.set(handler)
    try:
        yield usage
    finally:
        # ValueError: a streaming generator was closed from another context; the variable
        # dies with that context anyway.
        with suppress(ValueError):
            _current_handler.reset(token)
        try:
            _save(action, handler, usage)
        except Exception:  # accounting must never break the user's action
            log_exception(logger, "Could not save token usage", action=action)


# ---------------------------------------------------------------- reports


def usage_since(since: datetime | None = None) -> Usage:
    """Total usage since a moment (e.g. the start of the browser session), or of all time."""
    with repo.session_scope() as session:
        input_tokens, output_tokens, cost = repo.usage_totals(session, since)
    return Usage(input_tokens, output_tokens, cost)


def last_action() -> tuple[str, Usage] | None:
    """The most recent logged action and its usage, e.g. ("analyse_fit", Usage(...))."""
    with repo.session_scope() as session:
        row = repo.latest_usage(session)
    if row is None:
        return None
    return row.action, Usage(row.input_tokens, row.output_tokens, row.cost_usd)


def usage_report(since: datetime | None = None) -> list[dict]:
    """One row per action for the developer settings table, most expensive first."""
    with repo.session_scope() as session:
        by_action = repo.usage_by_action(session, since)
    rows = [
        {
            "action": action,
            "calls": data["calls"],
            "input tokens": data["input_tokens"],
            "output tokens": data["output_tokens"],
            "cost (USD)": round(data["cost_usd"], 6),
        }
        for action, data in by_action.items()
    ]
    return sorted(rows, key=lambda row: row["cost (USD)"], reverse=True)


def budget_warning(session_usage: Usage, budget_tokens: int) -> str | None:
    """A warning once the session has used 80% of its token budget (None below that)."""
    if budget_tokens <= 0:
        return None
    share = session_usage.total_tokens / budget_tokens
    if share >= 1:
        return (
            f"This session went over its budget of {budget_tokens:,} tokens. Consider pausing; "
            "the limit is SESSION_TOKEN_BUDGET in .env."
        )
    if share >= BUDGET_WARNING_SHARE:
        return f"This session has used {share:.0%} of its {budget_tokens:,}-token budget."
    return None
